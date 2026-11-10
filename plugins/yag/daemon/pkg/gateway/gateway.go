package gateway

import (
	"context"

	"github.com/iknyazkov/yag/pkg/plugin"
)

type Gateway interface {
	Start(ctx context.Context) error

	AddPlugin(p plugin.Plugin)
}
