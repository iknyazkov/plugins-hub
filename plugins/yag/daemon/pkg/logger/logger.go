package logger

import (
	"io"
	"log"
)

func New(domain string, writers ...io.Writer) *log.Logger {
	multiWritter := io.MultiWriter(writers...)
	return log.New(multiWritter, domain+" ", log.LstdFlags)
}
