package gateway

import (
	"sync"

	"github.com/iknyazkov/yag/pkg/plugin"
)

type pluginRegistry struct {
	mu      sync.RWMutex
	plugins map[string]plugin.Plugin
	order   []plugin.Plugin
}

func newPluginRegistry() *pluginRegistry {
	return &pluginRegistry{plugins: make(map[string]plugin.Plugin)}
}

func (registry *pluginRegistry) add(p plugin.Plugin) {
	registry.mu.Lock()
	defer registry.mu.Unlock()

	if _, exists := registry.plugins[p.Name()]; exists {
		return
	}

	registry.plugins[p.Name()] = p
	registry.order = append(registry.order, p)
}

func (registry *pluginRegistry) get(name string) (plugin.Plugin, bool) {
	registry.mu.RLock()
	defer registry.mu.RUnlock()

	p, hasPlugin := registry.plugins[name]
	return p, hasPlugin
}

func (registry *pluginRegistry) each(action func(p plugin.Plugin)) {
	registry.mu.RLock()
	defer registry.mu.RUnlock()

	for _, p := range registry.order {
		action(p)
	}
}
