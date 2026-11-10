package gateway

import "time"

type Config struct {
	Name          string
	Port          int
	TickPerSecond time.Duration
}
