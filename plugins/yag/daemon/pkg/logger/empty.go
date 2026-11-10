package logger

import (
	"io"
)

type EmptySource struct{}

// io.Witer

func (writer *EmptySource) Write(p []byte) (n int, err error) {
	return len(p), nil
}

// Initialization

func NewEmptySource() io.Writer {
	return &EmptySource{}
}
