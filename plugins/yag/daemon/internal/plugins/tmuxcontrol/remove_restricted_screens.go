package tmuxcontrol

import "strings"

const classifierRestrisction = "We're changing auto mode to no longer charge for classifier requests in Claude Code."

func removeRestrictedScreens() error {
	panes, err := listSessions()
	if err != nil {
		return err
	}

	for _, panel := range panes {
		sessionName := normalizeSessionName(panel.SessionName)
		output, err := captureSession(sessionName)
		if err != nil {
			continue
		}

		if strings.Contains(output, classifierRestrisction) {
			submitInput(sessionName)
		}
	}

	return nil
}
