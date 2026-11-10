package systemcontrol

import (
	_ "embed"
	"strings"
)

//go:embed VERSION
var versionSource string

func buildVersion() string {
	if version := strings.TrimSpace(versionSource); version != "" {
		return version
	}

	return "dev"
}
