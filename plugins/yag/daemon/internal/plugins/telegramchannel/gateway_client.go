package telegramchannel

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/iknyazkov/yag/pkg/gateway"
	"github.com/iknyazkov/yag/pkg/plugin"
)

const gatewayTimeout = 10 * time.Second

type tmuxRequest struct {
	Action  string `json:"action"`
	Target  string `json:"target"`
	Text    string `json:"text"`
	Name    string `json:"name"`
	Command string `json:"command"`
	Dir     string `json:"dir"`
}

type paneEntry struct {
	SessionName string `json:"session_name"`
	Command     string `json:"command"`
	Title       string `json:"title"`
}

func newGatewayClient() *http.Client {
	return &http.Client{Timeout: gatewayTimeout}
}

func (p *TelegramChannelPlugin) requestGateway(name string, request tmuxRequest) (*plugin.PayloadMessage, error) {
	rawRequest, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}

	parameters := gateway.SendParameters{
		Name:    name,
		Payload: plugin.PayloadMessage{Message: rawRequest},
	}

	rawParameters, err := json.Marshal(parameters)
	if err != nil {
		return nil, err
	}

	response, err := p.gatewayClient.Post(p.gatewayAddress, "application/json", bytes.NewReader(rawParameters))
	if err != nil {
		return nil, fmt.Errorf("gateway request failed: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read gateway response: %w", err)
	}

	if response.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("gateway %s failed with status %d: %s", name, response.StatusCode, body)
	}

	answer := new(plugin.PayloadMessage)
	if err := json.Unmarshal(body, answer); err != nil {
		return nil, fmt.Errorf("failed to decode gateway response: %w", err)
	}

	return answer, nil
}
