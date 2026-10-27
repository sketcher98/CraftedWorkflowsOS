package strategy

import (
	"context"
	"sync"
)

// Strategy interface - each routing strategy implements this
type Strategy interface {
	Name() string
	Apply(ctx context.Context, targets []Target, req *Request) []Target
}

// StrategyRegistry holds all registered strategies
type Registry struct {
	mu         sync.RWMutex
	strategies map[string]Strategy
}

// NewRegistry creates a new strategy registry with built-in strategies
func NewRegistry() *Registry {
	r := &Registry{
		strategies: make(map[string]Strategy),
	}
	// Register built-in strategies
	r.Register("priority", &PriorityStrategy{})
	r.Register("p2c", &P2CStrategy{})
	// LKGP will be registered by main.go after config is loaded
	return r
}

// Register adds a strategy to the registry
func (r *Registry) Register(name string, strategy Strategy) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.strategies[name] = strategy
}

// Get retrieves a strategy by name
func (r *Registry) Get(name string) (Strategy, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	strat, ok := r.strategies[name]
	return strat, ok
}

// Apply applies a strategy by name
func (r *Registry) Apply(ctx context.Context, name string, targets []Target, req *Request) []Target {
	r.mu.RLock()
	strat, ok := r.strategies[name]
	r.mu.RUnlock()
	if !ok {
		return targets
	}
	return strat.Apply(ctx, targets, req)
}

// GetAll returns all registered strategy names
func (r *Registry) GetAll() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	names := make([]string, 0, len(r.strategies))
	for name := range r.strategies {
		names = append(names, name)
	}
	return names
}