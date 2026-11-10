package channel

import "encoding/json"

type Text struct {
	ID      string `json:"id"`
	Content string `json:"text"`
}

type RequestPermissions struct {
	Name        string          `json:"name"`
	ToolContent json.RawMessage `json:"tool_content"`
}
