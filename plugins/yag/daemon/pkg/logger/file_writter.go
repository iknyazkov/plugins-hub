package logger

import (
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

type FileWriter struct {
	fileNamePostfix string
	rootFolder      string
	fileWriter      io.Writer
}

// io.Witer

func (writer *FileWriter) Write(p []byte) (n int, err error) {
	if writer.fileWriter != nil {
		return writer.fileWriter.Write(p)
	}

	filePath := getFilePath(writer.fileNamePostfix, writer.rootFolder)
	fileDir := getFileDir(writer.fileNamePostfix, writer.rootFolder)
	err = os.MkdirAll(fileDir, 0o755)
	if err != nil {
		return len(p), err
	}

	file, err := os.OpenFile(filePath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return len(p), err
	}

	writer.fileWriter = file
	startGorutineToCloseFile(file)
	return writer.fileWriter.Write(p)
}

// Private methods

func getFilePath(fileNamePostfix string, rootFolder string) string {
	fileDir := getFileDir(fileNamePostfix, rootFolder)
	fileName := fmt.Sprintf("%s_app.log", time.Now().Format("2006-01-02"))

	return filepath.Join(fileDir, fileName)
}

func getFileDir(fileNamePostfix string, rootFolder string) string {
	configDir, err := os.UserConfigDir()
	if err != nil {
		configDir = os.TempDir()
	}

	return filepath.Join(configDir, rootFolder, fileNamePostfix)
}

func startGorutineToCloseFile(file *os.File) {
	go func() {
		channel := make(chan os.Signal, 1)
		signal.Notify(channel, os.Interrupt, syscall.SIGTERM)
		<-channel
		file.Close()
		os.Exit(0)
	}()
}

// Initialization

func NewFileWriter(fileNamePostfix string, rootFolder string) io.Writer {
	return &FileWriter{
		fileNamePostfix: fileNamePostfix,
		rootFolder:      rootFolder,
	}
}
