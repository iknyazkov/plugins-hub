package logger

import (
	"io"
)

type AsyncSource struct {
	writers  []io.Writer
	logsChan chan []byte
}

// io.Witer

func (writer *AsyncSource) Write(p []byte) (n int, err error) {
	writer.logsChan <- p
	return len(p), nil
}

// Private methods

func (writer *AsyncSource) startListening() {
	for logMsg := range writer.logsChan {
		for _, internalWriter := range writer.writers {
			internalWriter.Write(logMsg)
		}
	}
}

// Initialization

func NewAsyncWriter(writers ...io.Writer) io.Writer {
	source := &AsyncSource{
		writers:  writers,
		logsChan: make(chan []byte, 100),
	}

	go source.startListening()
	return source
}
