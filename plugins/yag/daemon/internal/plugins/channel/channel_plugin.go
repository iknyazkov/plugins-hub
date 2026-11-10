package channel

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/iknyazkov/yag/pkg/gateway"
	"github.com/iknyazkov/yag/pkg/plugin"
)

const (
	maxWholeMessageLength = 4096
	maxBufferSize         = 3
)

type ChannelPlugin struct {
	gatewayAddress string
	gatewayClient  *http.Client
	logger         *log.Logger

	messagesBuffer                map[string][]*Message
	outcommingGatewayMessagesChan chan gateway.SendParameters
}

func (p *ChannelPlugin) Name() string {
	return "channel"
}

func (p *ChannelPlugin) OnAttach() error {
	return nil
}

func (p *ChannelPlugin) OnGatewayStart(ctx context.Context) error {
	go func() {
		for {
			message := <-p.outcommingGatewayMessagesChan
			time.Sleep(time.Second * 1)
			_, err := sendMessageToGateway(p.gatewayClient, p.gatewayAddress, message)
			if err != nil {
				p.logger.Println(fmt.Errorf("error during send msg from channel plugin %w", err))
			}
		}
	}()

	return nil
}

func (p *ChannelPlugin) Tick(parameters plugin.TickParameters) error {
	return nil
}

func (p *ChannelPlugin) SendMessage(payload plugin.PayloadMessage) (*plugin.PayloadMessage, error) {
	message := new(Message)
	err := json.NewDecoder(bytes.NewReader(payload.Message)).Decode(message)
	if err != nil {
		return nil, err
	}

	messagesInBuffer := p.messagesBuffer[message.MessageID]
	messagesInBuffer = append(messagesInBuffer, message)
	p.messagesBuffer[message.MessageID] = messagesInBuffer

	switch {
	case message.IsFinal:
		p.enqueueMessages(messagesInBuffer)
		p.messagesBuffer[message.MessageID] = nil
	case message.HookEventName == PermissionRequestEvent:
		p.enqueueMessages(messagesInBuffer)
		p.messagesBuffer[message.MessageID] = nil
	}

	return nil, nil
}

func (p *ChannelPlugin) enqueueMessages(messages []*Message) {
	rawBytes := convertMessages(messages)
	for _, part := range rawBytes {
		request := gateway.SendParameters{
			Name: "telegram-channel",
			Payload: plugin.PayloadMessage{
				Message: part,
			},
		}
		p.outcommingGatewayMessagesChan <- request
	}
}

// Private methods

func convertMessages(messages []*Message) [][]byte {
	finalMessage := &Text{}
	for _, msg := range messages {
		finalMessage.ID = msg.MessageID
		finalMessage.Content += msg.Delta
	}

	if len([]rune(finalMessage.Content)) > maxWholeMessageLength {
		parts := splitTextParts(finalMessage.Content, maxWholeMessageLength)
		rawBytes := [][]byte{}
		for _, part := range parts {
			rawBytesPart, err := json.Marshal(Text{ID: finalMessage.ID, Content: part})
			if err != nil {
				continue
			}

			rawBytes = append(rawBytes, rawBytesPart)
		}

		return rawBytes
	}

	rawBytes, _ := json.Marshal(*finalMessage)
	return [][]byte{rawBytes}
}

func isOverFlow(messages []*Message) bool {
	wholeLen := 0
	for _, msg := range messages {
		wholeLen += len([]rune(msg.Delta))
	}

	return wholeLen > maxWholeMessageLength
}

func sendMessageToGateway(client *http.Client, address string, request gateway.SendParameters) (*plugin.PayloadMessage, error) {
	rawParameters, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}

	response, err := client.Post(address, "application/json", bytes.NewReader(rawParameters))
	if err != nil {
		return nil, fmt.Errorf("gateway request failed: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read gateway response: %w", err)
	}

	if response.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("gateway %s failed with status %d: %s", request.Name, response.StatusCode, body)
	}

	answer := new(plugin.PayloadMessage)
	if err := json.Unmarshal(body, answer); err != nil {
		return nil, fmt.Errorf("failed to decode gateway response: %w", err)
	}

	return answer, nil
}

// Initialization

func New(gatewayAddress string, logger *log.Logger) plugin.Plugin {
	return &ChannelPlugin{
		gatewayAddress:                gatewayAddress,
		gatewayClient:                 &http.Client{Timeout: time.Second * 10},
		messagesBuffer:                make(map[string][]*Message),
		outcommingGatewayMessagesChan: make(chan gateway.SendParameters, 100),
		logger:                        logger,
	}
}
