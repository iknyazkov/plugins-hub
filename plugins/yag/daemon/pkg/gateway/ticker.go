package gateway

import (
	"context"
	"time"

	"github.com/iknyazkov/yag/pkg/plugin"
)

func startTicker(ctx context.Context, tickPerSecond time.Duration, plugins *pluginRegistry) {
	interval := time.Second / tickPerSecond

	ticker := time.NewTicker(interval)
	lastTime := time.Now()
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			now := time.Now()
			delta := now.Sub(lastTime).Seconds()
			lastTime = now

			tickParameters := plugin.TickParameters{Delta: time.Duration(delta)}
			plugins.each(func(p plugin.Plugin) {
				p.Tick(tickParameters)
			})
		}
	}
}
