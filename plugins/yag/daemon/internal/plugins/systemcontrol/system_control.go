package systemcontrol

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/iknyazkov/yag/pkg/plugin"
)

const (
	pluginName = "system-control"

	configDirName = ".config/yag"
	envFileName   = ".env"
	pidFileName   = "gateway.pid"
	stateFileName = "state.json"

	keyTelegramBotToken = "TELEGRAM_BOT_TOKEN"
	keySocksProxy       = "SOCKS_PROXY"

	envTakeover = "YAG_TAKEOVER"

	killPollInterval = 300 * time.Millisecond
	killTimeout      = 5 * time.Second
)

type request struct {
	Action string `json:"action"`
}

type config struct {
	TelegramBotToken string `json:"telegram_bot_token"`
	SocksProxy       string `json:"socks_proxy"`
}

type state struct {
	Version string `json:"version"`
}

type SystemControlPlugin struct {
	version string
}

func NewSystemControlPlugin() *SystemControlPlugin {
	return &SystemControlPlugin{version: buildVersion()}
}

func (p *SystemControlPlugin) Name() string {
	return pluginName
}

func (p *SystemControlPlugin) OnAttach() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("failed to resolve home directory: %w", err)
	}

	dir := filepath.Join(home, configDirName)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("failed to create config directory %s: %w", dir, err)
	}

	pid := readPid(dir)
	saved := readState(dir)
	takeover := os.Getenv(envTakeover) == "1"
	os.Unsetenv(envTakeover)

	if pid > 0 && processAlive(pid) {
		if !takeover && saved.Version == p.version {
			fmt.Fprintf(os.Stderr, "systemcontrol: gateway already running (pid %d, version %s), exiting\n", pid, p.version)
			os.Exit(0)
		}

		if err := killStaleProcess(pid); err != nil {
			fmt.Fprintf(os.Stderr, "systemcontrol: %v\n", err)
			os.Exit(1)
		}
	}

	if err := writePid(dir, os.Getpid()); err != nil {
		return err
	}

	return writeState(dir, state{Version: p.version})
}

func (p *SystemControlPlugin) OnGatewayStart(ctx context.Context) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("failed to resolve home directory: %w", err)
	}

	dir := filepath.Join(home, configDirName)

	envFile := filepath.Join(dir, envFileName)
	if _, err := os.Stat(envFile); err == nil {
		return nil
	}

	template := fmt.Sprintf("%s=\n%s=\n", keyTelegramBotToken, keySocksProxy)
	if err := os.WriteFile(envFile, []byte(template), 0o600); err != nil {
		return fmt.Errorf("failed to create %s: %w", envFile, err)
	}

	return nil
}

func (p *SystemControlPlugin) Tick(parameters plugin.TickParameters) error {
	return nil
}

func (p *SystemControlPlugin) SendMessage(payload plugin.PayloadMessage) (*plugin.PayloadMessage, error) {
	request := new(request)
	err := json.Unmarshal(payload.Message, request)
	if err != nil {
		return nil, fmt.Errorf("invalid system-control payload: %w", err)
	}

	switch request.Action {
	case "config":
		return p.serveConfig()
	case "version":
		rawBytes, err := json.Marshal(map[string]string{"version": p.version})
		if err != nil {
			return nil, err
		}

		return &plugin.PayloadMessage{Message: rawBytes}, nil
	case "reboot":
		return nil, p.spawnSuccessor()
	default:
		return nil, fmt.Errorf("unknown action %q", request.Action)
	}
}

func (p *SystemControlPlugin) serveConfig() (*plugin.PayloadMessage, error) {
	values, err := readEnvFile()
	if err != nil {
		return nil, err
	}

	answer := config{
		TelegramBotToken: values[keyTelegramBotToken],
		SocksProxy:       values[keySocksProxy],
	}

	rawBytes, err := json.Marshal(answer)
	if err != nil {
		return nil, err
	}

	return &plugin.PayloadMessage{Message: rawBytes}, nil
}

func (p *SystemControlPlugin) spawnSuccessor() error {
	successor := exec.Command(os.Args[0], os.Args[1:]...)
	successor.Env = append(os.Environ(), envTakeover+"=1")
	successor.Stdout = os.Stdout
	successor.Stderr = os.Stderr

	return successor.Start()
}

func processAlive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

func killStaleProcess(pid int) error {
	err := syscall.Kill(pid, syscall.SIGKILL)
	if err != nil && !errors.Is(err, syscall.ESRCH) {
		return fmt.Errorf("failed to kill process %d: %w", pid, err)
	}

	deadline := time.Now().Add(killTimeout)
	for processAlive(pid) {
		if time.Now().After(deadline) {
			return fmt.Errorf("process %d did not exit within %s", pid, killTimeout)
		}

		time.Sleep(killPollInterval)
	}

	return nil
}

func readPid(dir string) int {
	rawBytes, err := os.ReadFile(filepath.Join(dir, pidFileName))
	if err != nil {
		return 0
	}

	pid, err := strconv.Atoi(strings.TrimSpace(string(rawBytes)))
	if err != nil || pid <= 0 {
		return 0
	}

	return pid
}

func readState(dir string) state {
	rawBytes, err := os.ReadFile(filepath.Join(dir, stateFileName))
	if err != nil {
		return state{}
	}

	saved := new(state)
	if json.Unmarshal(rawBytes, saved) != nil {
		return state{}
	}

	return *saved
}

func writePid(dir string, pid int) error {
	if err := os.WriteFile(filepath.Join(dir, pidFileName), []byte(strconv.Itoa(pid)), 0o644); err != nil {
		return fmt.Errorf("failed to write %s: %w", pidFileName, err)
	}

	return nil
}

func writeState(dir string, saved state) error {
	rawBytes, err := json.Marshal(saved)
	if err != nil {
		return err
	}

	if err := os.WriteFile(filepath.Join(dir, stateFileName), rawBytes, 0o644); err != nil {
		return fmt.Errorf("failed to write %s: %w", stateFileName, err)
	}

	return nil
}

func readEnvFile() (map[string]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("failed to resolve home directory: %w", err)
	}

	rawBytes, err := os.ReadFile(filepath.Join(home, configDirName, envFileName))
	if err != nil {
		return nil, fmt.Errorf("failed to read .env: %w", err)
	}

	values := make(map[string]string)
	for line := range strings.SplitSeq(string(rawBytes), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		key, value, found := strings.Cut(line, "=")
		if !found {
			continue
		}

		values[strings.TrimSpace(key)] = strings.TrimSpace(value)
	}

	return values, nil
}
