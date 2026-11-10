package plugin

import "context"

type Plugin interface {
	Name() string

	OnAttach() error
	OnGatewayStart(ctx context.Context) error

	Tick(parameters TickParameters) error

	SendMessage(payload PayloadMessage) (*PayloadMessage, error)
}
