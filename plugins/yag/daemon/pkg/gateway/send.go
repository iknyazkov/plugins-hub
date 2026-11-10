package gateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/iknyazkov/yag/pkg/plugin"
)

type SendParameters struct {
	Name      string                `json:"name"`
	ForwardTo string                `json:"forward_to,omitempty"`
	Payload   plugin.PayloadMessage `json:"payload"`
}

type SendHandler struct {
	plugins        *pluginRegistry
	forwardAddress string
	internalClient *http.Client
	logger         *log.Logger
}

func (handler *SendHandler) Handle(writer http.ResponseWriter, request *http.Request) {
	parameters := new(SendParameters)
	err := json.NewDecoder(request.Body).Decode(parameters)
	if err != nil {
		WriteError(writer, http.StatusBadRequest, err)
		return
	}

	plugin, hasPlugin := handler.plugins.get(parameters.Name)
	if !hasPlugin {
		WriteError(writer, http.StatusBadRequest, fmt.Errorf("destination was not found %s", parameters.Name))
		return
	}

	answer, err := plugin.SendMessage(parameters.Payload)
	if err != nil {
		WriteError(writer, http.StatusBadRequest, err)
		return
	}

	if answer == nil {
		WriteJSON(writer, http.StatusOK, map[string]any{"status": "message was recieved"})
		return
	}

	if parameters.ForwardTo == "" {
		WriteJSON(writer, http.StatusOK, *answer)
		return
	}

	forwardParameters := SendParameters{
		Name:    parameters.ForwardTo,
		Payload: *answer,
	}
	go func() {
		if err := forward(handler.internalClient, handler.forwardAddress, forwardParameters); err != nil {
			handler.logger.Printf("forward to %s failed: %v", parameters.ForwardTo, err)
		}
	}()

	WriteJSON(writer, http.StatusOK, map[string]any{"status": "message was recieved"})
}

func forward(client *http.Client, address string, parameters SendParameters) error {
	rawBytes, err := json.Marshal(parameters)
	if err != nil {
		return err
	}
	request, err := http.NewRequest(
		http.MethodPost,
		address,
		bytes.NewReader(rawBytes),
	)
	if err != nil {
		return err
	}

	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()

	if response.StatusCode >= http.StatusBadRequest {
		body, _ := io.ReadAll(response.Body)
		return fmt.Errorf("forward to %s failed with status %d: %s", parameters.Name, response.StatusCode, body)
	}

	return nil
}

func NewSendHandler(forwardAddress string, logger *log.Logger, plugins *pluginRegistry) *SendHandler {
	return &SendHandler{
		plugins:        plugins,
		forwardAddress: forwardAddress,
		logger:         logger,
		internalClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}
