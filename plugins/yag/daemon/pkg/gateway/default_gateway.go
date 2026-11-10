package gateway

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/http"

	"github.com/iknyazkov/yag/pkg/gateway/middleware/logging"
	"github.com/iknyazkov/yag/pkg/plugin"
)

type DefaultGateway struct {
	cfg     Config
	plugins *pluginRegistry
	logger  *log.Logger
}

func (gateway *DefaultGateway) Start(ctx context.Context) error {
	server, err := makeServer(gateway.cfg, gateway.plugins, gateway.logger)
	if err != nil {
		return err
	}

	server.BaseContext = func(net.Listener) context.Context {
		return ctx
	}

	listener, err := net.Listen("tcp", server.Addr)
	if err != nil {
		return err
	}

	go server.Serve(listener)
	gateway.attachPlugins(ctx)
	go startTicker(ctx, gateway.cfg.TickPerSecond, gateway.plugins)

	<-ctx.Done()
	return server.Shutdown(context.Background())
}

func (gateway *DefaultGateway) AddPlugin(p plugin.Plugin) {
	gateway.plugins.add(p)

	err := p.OnAttach()
	if err != nil {
		gateway.logger.Println(fmt.Errorf("plugin %s error: %v", p.Name(), err))
	}
}

func (gateway *DefaultGateway) attachPlugins(ctx context.Context) {
	gateway.plugins.each(func(p plugin.Plugin) {
		err := p.OnGatewayStart(ctx)
		if err != nil {
			gateway.logger.Printf("OnAttach of %s failed: %v", p.Name(), err)
		}
	})
}

func makeServer(cfg Config, plugins *pluginRegistry, logger *log.Logger) (*http.Server, error) {
	address := fmt.Sprintf(":%d", cfg.Port)
	mux := makeServerMux(cfg, plugins, logger)

	return &http.Server{
		Addr:    address,
		Handler: middlewares(mux, logger),
	}, nil
}

func makeServerMux(cfg Config, plugins *pluginRegistry, logger *log.Logger) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", health)
	mux.HandleFunc("/send", NewSendHandler(forwardAddress(cfg.Port), logger, plugins).Handle)
	return mux
}

func forwardAddress(port int) string {
	return fmt.Sprintf("http://localhost:%d/send", port)
}

func middlewares(mux *http.ServeMux, logger *log.Logger) http.Handler {
	return logging.New(logger).Handle(mux)
}

func health(writer http.ResponseWriter, request *http.Request) {
	WriteJSON(writer, http.StatusOK, map[string]any{"status": "OK"})
}

func New(cfg Config, logger *log.Logger) Gateway {
	return &DefaultGateway{
		cfg:     cfg,
		logger:  logger,
		plugins: newPluginRegistry(),
	}
}
