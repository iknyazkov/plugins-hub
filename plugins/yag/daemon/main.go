package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/iknyazkov/yag/internal/plugins/channel"
	"github.com/iknyazkov/yag/internal/plugins/systemcontrol"
	"github.com/iknyazkov/yag/internal/plugins/telegramchannel"
	"github.com/iknyazkov/yag/internal/plugins/tmuxcontrol"
	"github.com/iknyazkov/yag/pkg/gateway"
	"github.com/iknyazkov/yag/pkg/logger"
)

const (
	tickPerSecond time.Duration = 5
	appName                     = "yet-another-gateway"
)

var lg = logger.New(appName, logger.NewFormatWriter(os.Stdout))

func main() {
	cfg := gateway.Config{
		Name:          appName,
		Port:          63991,
		TickPerSecond: tickPerSecond,
	}

	forwardAddress := fmt.Sprintf("http://localhost:%d/send", cfg.Port)

	ctx := context.Background()
	g := gateway.New(cfg, lg)

	g.AddPlugin(systemcontrol.NewSystemControlPlugin())
	g.AddPlugin(tmuxcontrol.NewTmuxControlPlugin())
	g.AddPlugin(channel.New(forwardAddress, lg))
	g.AddPlugin(telegramchannel.NewTelegramChannelPlugin(forwardAddress))

	err := g.Start(ctx)

	if err != nil {
		lg.Fatal(err)
	}
}
