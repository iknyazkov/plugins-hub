package gateway

import (
	"encoding/json"
	"log"
	"net/http"
)

func WriteText(writer http.ResponseWriter, status int, msg string) {
	writer.Header().Set("Content-Type", "text/plain; charset=utf-8")
	writer.WriteHeader(status)
	code, err := writer.Write([]byte(msg))

	log.Printf("subscription was given with code %d, error: %v", code, err)
}

func WriteJSON(writer http.ResponseWriter, status int, object any) {
	writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	writer.WriteHeader(status)

	if err := json.NewEncoder(writer).Encode(object); err != nil {
		http.Error(writer, "failed to encode json", http.StatusInternalServerError)
	}
}

func WriteError(writer http.ResponseWriter, status int, err error) {
	WriteJSON(writer, status, map[string]string{
		"error": err.Error(),
	})
}
