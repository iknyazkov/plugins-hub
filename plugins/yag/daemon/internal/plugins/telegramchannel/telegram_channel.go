package telegramchannel

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"

	telegramify "github.com/barbashov/telegramify-markdown-go"
	"github.com/iknyazkov/yag/pkg/plugin"
)

const (
	pluginName     = "telegram-channel"
	defaultSession = "default"

	pollTimeout = time.Minute

	systemControlName = "system-control"
)

type config struct {
	TelegramBotToken string `json:"telegram_bot_token"`
	SocksProxy       string `json:"socks_proxy"`
}

type TelegramChannelPlugin struct {
	gatewayAddress string
	gatewayClient  *http.Client

	mu            sync.Mutex
	bot           *bot.Bot
	chatID        int64
	activeSession string

	logger *log.Logger
}

func NewTelegramChannelPlugin(gatewayAddress string) *TelegramChannelPlugin {
	return &TelegramChannelPlugin{
		gatewayAddress: gatewayAddress,
		gatewayClient:  newGatewayClient(),
		activeSession:  defaultSession,
	}
}

func (p *TelegramChannelPlugin) Name() string {
	return pluginName
}

func (p *TelegramChannelPlugin) OnAttach() error {
	return nil
}

func (p *TelegramChannelPlugin) OnGatewayStart(ctx context.Context) error {
	answer, err := p.requestGateway(systemControlName, tmuxRequest{Action: "config"})
	if err != nil {
		return fmt.Errorf("failed to request config from %s: %w", systemControlName, err)
	}

	settings := new(config)
	if err := json.Unmarshal(answer.Message, settings); err != nil {
		return fmt.Errorf("failed to decode %s config: %w", systemControlName, err)
	}

	if settings.TelegramBotToken == "" {
		return errors.New("TELEGRAM_BOT_TOKEN is empty in ~/.config/yag/.env, telegram bot is not started")
	}

	instance, err := p.buildBot(settings)
	if err != nil {
		return err
	}

	_, err = instance.SetMyCommands(ctx, &bot.SetMyCommandsParams{
		Commands: []models.BotCommand{
			{Command: "new", Description: "create session: /new <name> <dir> <command...>"},
			{Command: "kill", Description: "kill session: /kill <name>"},
			{Command: "list", Description: "list yag sessions"},
			{Command: "screen", Description: "show active session screen"},
			{Command: "pick", Description: "pick active session"},
			{Command: "reboot", Description: "restart the gateway"},
			{Command: "version", Description: "show gateway version"},
		},
	})
	if err != nil {
		return fmt.Errorf("failed to set telegram commands: %w", err)
	}

	p.mu.Lock()
	p.bot = instance
	p.mu.Unlock()

	go instance.Start(ctx)

	return nil
}

func (p *TelegramChannelPlugin) buildBot(settings *config) (*bot.Bot, error) {
	options := []bot.Option{
		bot.WithDefaultHandler(p.handleUpdate),
	}
	if settings.SocksProxy != "" {
		proxyValue := settings.SocksProxy
		if !strings.Contains(proxyValue, "://") {
			proxyValue = "socks5://" + proxyValue
		}

		proxyURL, err := url.Parse(proxyValue)
		if err != nil {
			return nil, fmt.Errorf("invalid SOCKS_PROXY %q: %w", settings.SocksProxy, err)
		}

		options = append(options, bot.WithHTTPClient(pollTimeout, &http.Client{
			Timeout: pollTimeout,
			Transport: &http.Transport{
				Proxy: http.ProxyURL(proxyURL),
			},
		}))
	}

	instance, err := bot.New(settings.TelegramBotToken, options...)
	if err != nil {
		return nil, fmt.Errorf("failed to create telegram bot: %w", err)
	}

	return instance, nil
}

func (p *TelegramChannelPlugin) Tick(parameters plugin.TickParameters) error {
	return nil
}

func (p *TelegramChannelPlugin) SendMessage(payload plugin.PayloadMessage) (*plugin.PayloadMessage, error) {
	p.mu.Lock()
	instance, chatID := p.bot, p.chatID
	p.mu.Unlock()

	if instance == nil {
		return nil, errors.New("telegram bot is not started")
	}

	if chatID == 0 {
		return nil, errors.New("no incoming chat yet, nobody has written to the bot")
	}

	var message struct {
		ID      string `json:"id"`
		Content string `json:"text"`
	}

	err := json.Unmarshal(payload.Message, &message)
	if err != nil {
		return nil, err
	}

	chunks := telegramify.RenderChunks(message.Content, telegramify.DefaultMaxLength)
	for _, chunk := range chunks {
		_, err = instance.SendMessage(context.Background(), &bot.SendMessageParams{
			ChatID:    chatID,
			Text:      chunk.MarkdownV2,
			ParseMode: models.ParseModeMarkdown,
		})
	}

	return nil, err
}
