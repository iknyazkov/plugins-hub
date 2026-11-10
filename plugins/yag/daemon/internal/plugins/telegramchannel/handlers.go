package telegramchannel

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/go-telegram/bot"
	"github.com/go-telegram/bot/models"
)

const (
	tmuxControlName = "tmux-control"

	pickCallbackPrefix = "pick:"

	maxMessageParts   = 10
	telegramTextLimit = 4096
)

func (p *TelegramChannelPlugin) handleUpdate(ctx context.Context, b *bot.Bot, update *models.Update) {
	if update.CallbackQuery != nil {
		p.handlePick(ctx, b, update.CallbackQuery)
		return
	}

	if update.Message == nil {
		return
	}

	p.mu.Lock()
	p.chatID = update.Message.Chat.ID
	p.mu.Unlock()

	text := strings.TrimSpace(update.Message.Text)
	if text == "" {
		return
	}

	chatID := update.Message.Chat.ID

	switch {
	case text == "/list":
		p.replyList(ctx, b, chatID)
	case text == "/screen":
		p.replyScreen(ctx, b, chatID)
	case text == "/pick":
		p.replyPick(ctx, b, chatID)
	case text == "/new" || strings.HasPrefix(text, "/new "):
		p.replyNew(ctx, b, chatID, strings.TrimSpace(strings.TrimPrefix(text, "/new")))
	case text == "/kill" || strings.HasPrefix(text, "/kill "):
		p.replyKill(ctx, b, chatID, strings.TrimSpace(strings.TrimPrefix(text, "/kill")))
	case text == "/reboot":
		p.replyReboot(ctx, b, chatID)
	case text == "/version":
		p.replyVersion(ctx, b, chatID)
	default:
		p.forwardText(ctx, b, chatID, text)
	}
}

func (p *TelegramChannelPlugin) replyList(ctx context.Context, b *bot.Bot, chatID int64) {
	answer, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "list"})
	if err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	panes := make([]paneEntry, 0)
	if err := json.Unmarshal(answer.Message, &panes); err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: failed to decode session list: %v", err))
		return
	}

	if len(panes) == 0 {
		p.reply(ctx, b, chatID, "no sessions")
		return
	}

	lines := make([]string, 0, len(panes))
	for _, pane := range panes {
		lines = append(lines, fmt.Sprintf("%s · %s · %s", pane.SessionName, pane.Command, pane.Title))
	}

	p.reply(ctx, b, chatID, strings.Join(lines, "\n"))
}

func (p *TelegramChannelPlugin) replyNew(ctx context.Context, b *bot.Bot, chatID int64, args string) {
	parts := strings.Fields(args)
	if len(parts) < 3 {
		p.reply(ctx, b, chatID, "usage: /new <name> <dir> <command...>")
		return
	}

	request := tmuxRequest{
		Action:  "new-session",
		Name:    parts[0],
		Dir:     expandHome(parts[1]),
		Command: strings.Join(parts[2:], " "),
	}

	if _, err := p.requestGateway(tmuxControlName, request); err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	p.mu.Lock()
	p.activeSession = request.Name
	p.mu.Unlock()

	p.reply(ctx, b, chatID, fmt.Sprintf("session %s created and active", request.Name))
}

func (p *TelegramChannelPlugin) replyKill(ctx context.Context, b *bot.Bot, chatID int64, args string) {
	name := strings.TrimSpace(args)
	if name == "" {
		p.reply(ctx, b, chatID, "usage: /kill <name>")
		return
	}

	if _, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "kill-session", Name: name}); err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	p.mu.Lock()
	if p.activeSession == name {
		p.activeSession = defaultSession
	}
	p.mu.Unlock()

	p.reply(ctx, b, chatID, fmt.Sprintf("session %s killed", name))
}

func (p *TelegramChannelPlugin) forwardText(ctx context.Context, b *bot.Bot, chatID int64, text string) {
	if stripped, ok := strings.CutPrefix(text, "$ "); ok {
		text = stripped
	}

	p.mu.Lock()
	target := p.activeSession
	p.mu.Unlock()

	_, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "send", Target: target, Text: text})
	if err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
	}
}

func (p *TelegramChannelPlugin) replyReboot(ctx context.Context, b *bot.Bot, chatID int64) {
	p.reply(ctx, b, chatID, "rebooting...")

	go func() {
		_, _ = p.requestGateway(systemControlName, tmuxRequest{Action: "reboot"})
	}()
}

func (p *TelegramChannelPlugin) replyVersion(ctx context.Context, b *bot.Bot, chatID int64) {
	answer, err := p.requestGateway(systemControlName, tmuxRequest{Action: "version"})
	if err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	version := new(struct {
		Version string `json:"version"`
	})
	if err := json.Unmarshal(answer.Message, version); err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: failed to decode version: %v", err))
		return
	}

	p.reply(ctx, b, chatID, "yag "+version.Version)
}

func (p *TelegramChannelPlugin) reply(ctx context.Context, b *bot.Bot, chatID int64, text string) {
	_, err := b.SendMessage(ctx, &bot.SendMessageParams{
		ChatID: chatID,
		Text:   text,
	})
	if err != nil {
		fmt.Printf("telegramchannel: failed to reply to chat %d: %v\n", chatID, err)
	}
}

func (p *TelegramChannelPlugin) replyScreen(ctx context.Context, b *bot.Bot, chatID int64) {
	p.mu.Lock()
	target := p.activeSession
	p.mu.Unlock()

	answer, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "capture", Target: target})
	if err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	for _, part := range splitScreenParts(string(answer.Message)) {
		p.replyCode(ctx, b, chatID, part)
	}
}

func (p *TelegramChannelPlugin) replyCode(ctx context.Context, b *bot.Bot, chatID int64, text string) {
	_, err := b.SendMessage(ctx, &bot.SendMessageParams{
		ChatID:    chatID,
		Text:      text,
		ParseMode: models.ParseModeMarkdown,
	})
	if err != nil {
		fmt.Printf("telegramchannel: failed to reply to chat %d: %v\n", chatID, err)
	}
}

func (p *TelegramChannelPlugin) replyPick(ctx context.Context, b *bot.Bot, chatID int64) {
	answer, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "list"})
	if err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: %v", err))
		return
	}

	panes := make([]paneEntry, 0)
	if err := json.Unmarshal(answer.Message, &panes); err != nil {
		p.reply(ctx, b, chatID, fmt.Sprintf("error: failed to decode session list: %v", err))
		return
	}

	if len(panes) == 0 {
		p.reply(ctx, b, chatID, "no sessions")
		return
	}

	rows := make([][]models.InlineKeyboardButton, 0, len(panes))
	for _, pane := range panes {
		rows = append(rows, []models.InlineKeyboardButton{{
			Text:         fmt.Sprintf("%s · %s", pane.SessionName, pane.Command),
			CallbackData: pickCallbackPrefix + pane.SessionName,
		}})
	}

	_, err = b.SendMessage(ctx, &bot.SendMessageParams{
		ChatID:      chatID,
		Text:        "pick a session:",
		ReplyMarkup: models.InlineKeyboardMarkup{InlineKeyboard: rows},
	})
	if err != nil {
		fmt.Printf("telegramchannel: failed to send keyboard to chat %d: %v\n", chatID, err)
	}
}

func (p *TelegramChannelPlugin) handlePick(ctx context.Context, b *bot.Bot, query *models.CallbackQuery) {
	answerCallback := func(text string) {
		_, _ = b.AnswerCallbackQuery(ctx, &bot.AnswerCallbackQueryParams{
			CallbackQueryID: query.ID,
			Text:            text,
		})
	}

	name, found := strings.CutPrefix(query.Data, pickCallbackPrefix)
	if !found || name == "" {
		answerCallback("error: unknown callback")
		return
	}

	if !p.sessionAlive(name) {
		p.editPickMessage(ctx, b, query, fmt.Sprintf("error: can't find session %s", name))
		answerCallback("error: can't find session")
		return
	}

	p.mu.Lock()
	p.activeSession = name
	p.mu.Unlock()

	p.editPickMessage(ctx, b, query, "active: "+name)
}

func (p *TelegramChannelPlugin) sessionAlive(name string) bool {
	answer, err := p.requestGateway(tmuxControlName, tmuxRequest{Action: "list"})
	if err != nil {
		return false
	}

	panes := make([]paneEntry, 0)
	if err := json.Unmarshal(answer.Message, &panes); err != nil {
		return false
	}

	for _, pane := range panes {
		if pane.SessionName == name {
			return true
		}
	}

	return false
}

func (p *TelegramChannelPlugin) editPickMessage(ctx context.Context, b *bot.Bot, query *models.CallbackQuery, text string) {
	message := query.Message.Message
	if message == nil {
		return
	}

	_, err := b.EditMessageText(ctx, &bot.EditMessageTextParams{
		ChatID:    message.Chat.ID,
		MessageID: message.ID,
		Text:      text,
	})
	if err != nil {
		fmt.Printf("telegramchannel: failed to edit pick message: %v\n", err)
	}
}

func splitScreenParts(text string) []string {
	parts := splitTextParts(escapeMarkdownV2Code(text), telegramTextLimit-len("```\n\n```"))

	wrapped := make([]string, len(parts))
	for i, part := range parts {
		wrapped[i] = "```\n" + part + "\n```"
	}

	return wrapped
}

func splitTextParts(text string, limit int) []string {
	parts := make([]string, 0, 1)

	current := strings.Builder{}
	currentLen := 0

	flush := func() {
		if currentLen > 0 {
			parts = append(parts, current.String())
			current.Reset()
			currentLen = 0
		}
	}

	for line := range strings.SplitSeq(text, "\n") {
		need := utf8.RuneCountInString(line) + 1

		if need > limit {
			flush()
			parts = append(parts, chunksByRunes(line, limit)...)
			continue
		}

		if currentLen+need > limit {
			flush()
		}

		current.WriteString(line)
		current.WriteString("\n")
		currentLen += need
	}
	flush()

	if len(parts) > maxMessageParts {
		parts = parts[len(parts)-maxMessageParts:]
	}

	return parts
}

func chunksByRunes(text string, size int) []string {
	runes := []rune(text)
	result := make([]string, 0, len(runes)/size+1)

	for start := 0; start < len(runes); start += size {
		end := min(start+size, len(runes))

		if end < len(runes) && runes[end-1] == '\\' {
			end--
		}

		result = append(result, string(runes[start:end]))
	}

	return result
}

func escapeMarkdownV2Code(text string) string {
	text = strings.ReplaceAll(text, `\`, `\\`)
	return strings.ReplaceAll(text, "`", "\\`")
}

func expandHome(path string) string {
	if path != "~" && !strings.HasPrefix(path, "~/") {
		return path
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return path
	}

	return filepath.Join(home, strings.TrimPrefix(path, "~"))
}
