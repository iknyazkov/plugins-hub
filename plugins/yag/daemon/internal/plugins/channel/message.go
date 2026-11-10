package channel

import "encoding/json"

const (
	MessageDisplayEvent    HookEvent = "MessageDisplay"
	PermissionRequestEvent HookEvent = "PermissionRequest"
)

type HookEvent string

type Message struct {
	HookEventName  HookEvent `json:"hook_event_name"`
	SessionID      string    `json:"session_id"`
	TranscriptPath string    `json:"transcript_path"`
	Cwd            string    `json:"cwd"`

	TurnID    string `json:"turn_id"`
	MessageID string `json:"message_id"`
	Index     int    `json:"index"`
	IsFinal   bool   `json:"final"`
	Delta     string `json:"delta"`

	PermissionMode string          `json:"permission_mode"`
	ToolName       string          `json:"tool_name"`
	ToolInput      json.RawMessage `json:"tool_input"`
}
