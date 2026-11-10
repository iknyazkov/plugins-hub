package logging

import (
	"log"
	"net/http"
)

type LoggingMiddleware struct {
	logger *log.Logger
}

func (middlewate *LoggingMiddleware) Handle(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writter http.ResponseWriter, request *http.Request) {
		middlewate.logger.Println(request.Method, request.URL.Path)
		next.ServeHTTP(writter, request)
	})
}

func New(logger *log.Logger) *LoggingMiddleware {
	return &LoggingMiddleware{logger: logger}
}
