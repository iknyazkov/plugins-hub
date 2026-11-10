package tmuxcontrol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/iknyazkov/yag/pkg/plugin"
)

const (
	pluginName    = "tmux-control"
	sessionPrefix = "yag-"

	captureLines = "-100"
)

type parameters struct {
	Action  string `json:"action"`
	Target  string `json:"target"`
	Text    string `json:"text"`
	Name    string `json:"name"`
	Command string `json:"command"`
	Dir     string `json:"dir"`
}

type paneEntry struct {
	SessionName string `json:"session_name"`
	Command     string `json:"command"`
	Title       string `json:"title"`
}

type TmuxControlPlugin struct{}

func NewTmuxControlPlugin() *TmuxControlPlugin {
	return &TmuxControlPlugin{}
}

func (p *TmuxControlPlugin) Name() string {
	return pluginName
}

func (p *TmuxControlPlugin) OnAttach() error {
	return nil
}

func (p *TmuxControlPlugin) OnGatewayStart(ctx context.Context) error {
	return nil
}

func (p *TmuxControlPlugin) Tick(parameters plugin.TickParameters) error {
	return nil
}

func (p *TmuxControlPlugin) SendMessage(payload plugin.PayloadMessage) (*plugin.PayloadMessage, error) {
	request := new(parameters)
	err := json.Unmarshal(payload.Message, request)
	if err != nil {
		return nil, fmt.Errorf("invalid tmux-control payload: %w", err)
	}

	switch request.Action {
	case "send":
		return nil, p.sendKeys(request.Target, request.Text)
	case "capture":
		return p.capture(request.Target)
	case "new-session":
		return nil, p.newSession(request.Name, request.Command, request.Dir)
	case "kill-session":
		return nil, p.killSession(request.Name)
	case "list":
		return p.list()
	default:
		return nil, fmt.Errorf("unknown action %q", request.Action)
	}
}

func (p *TmuxControlPlugin) sendKeys(target, text string) error {
	if target == "" || text == "" {
		return errors.New("target and text are required for send")
	}

	fullTarget := sessionPrefix + target
	_, err := runTMux("send-keys", "-t", fullTarget, "--", text)
	if err != nil {
		return err
	}

	return submitInput(fullTarget)
}

func submitInput(target string) error {
	_, err := runTMux("send-keys", "-t", target, "Enter")
	return err
}

func (p *TmuxControlPlugin) capture(target string) (*plugin.PayloadMessage, error) {
	if target == "" {
		return nil, errors.New("target is required for capture")
	}

	output, err := runTMux("capture-pane", "-p", "-J", "-S", captureLines, "-t", sessionPrefix+target)
	if err != nil {
		return nil, err
	}

	return &plugin.PayloadMessage{Message: []byte(output)}, nil
}

func (p *TmuxControlPlugin) newSession(name, command, dir string) error {
	if name == "" || command == "" {
		return errors.New("name and command are required for new-session")
	}

	args := []string{"new-session", "-d", "-s", sessionPrefix + name}
	if dir != "" {
		args = append(args, "-c", dir)
	}

	args = append(args, fmt.Sprintf("zsh -ic %s", shellQuote(command)))

	if _, err := runTMux(args...); err != nil {
		return err
	}

	time.Sleep(300 * time.Millisecond)
	if _, err := runTMux("has-session", "-t", sessionPrefix+name); err != nil {
		return fmt.Errorf("session %s exited immediately: command %q is not a runnable binary or script (shell aliases do not work here)", name, command)
	}

	return nil
}

func (p *TmuxControlPlugin) killSession(name string) error {
	if name == "" {
		return errors.New("name is required for kill-session")
	}

	_, err := runTMux("kill-session", "-t", sessionPrefix+name)
	return err
}

func (p *TmuxControlPlugin) list() (*plugin.PayloadMessage, error) {
	output, err := runTMux("list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}\t#{pane_title}")
	if err != nil {
		return nil, err
	}

	panes := make([]paneEntry, 0)
	for line := range strings.SplitSeq(strings.TrimSpace(output), "\n") {
		if line == "" {
			continue
		}

		parts := strings.SplitN(line, "\t", 3)
		if len(parts) != 3 || !strings.HasPrefix(parts[0], sessionPrefix) {
			continue
		}

		panes = append(panes, paneEntry{
			SessionName: strings.TrimPrefix(parts[0], sessionPrefix),
			Command:     parts[1],
			Title:       parts[2],
		})
	}

	rawBytes, err := json.Marshal(panes)
	if err != nil {
		return nil, err
	}

	return &plugin.PayloadMessage{Message: rawBytes}, nil
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

func runTMux(args ...string) (string, error) {
	return runTMuxStdin("", args...)
}

func runTMuxStdin(stdin string, args ...string) (string, error) {
	command := exec.Command("tmux", args...)
	if stdin != "" {
		command.Stdin = bytes.NewReader([]byte(stdin))
	}

	output, err := command.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return "", fmt.Errorf("tmux %s: %s", strings.Join(args, " "), bytes.TrimSpace(exitErr.Stderr))
		}
		return "", err
	}

	return string(output), nil
}
