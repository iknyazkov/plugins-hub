package logger

import (
	"fmt"
	"io"
	"time"
)

type FormatrWriter struct {
	writer io.Writer
}

// io.Witer

func (writer *FormatrWriter) Write(p []byte) (n int, err error) {
	oldMsg := string(p)
	msg := fmt.Sprintf(
		"%s | %s",
		time.Now().Format("2006-01-02 15:04:05"),
		oldMsg,
	)

	return writer.writer.Write([]byte(msg))
}

// Initialization

func NewFormatWriter(writer io.Writer) io.Writer {
	return &FormatrWriter{
		writer: writer,
	}
}
