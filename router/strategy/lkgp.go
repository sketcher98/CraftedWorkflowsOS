package strategy

import (
	"context"
	"sync"
)

// LKGPState holds the last-known-good provider per preference
type LKGPState struct {
	mu    sync.RWMutex
	pins  map[string]string // preference -> provider
	muAge sync.RWMutex
	age   map[string]int64 // preference -> unix timestamp
}

// NewLKGPState creates a new LKGP state tracker
func NewLKGPState() *LKGPState {
	return &LKGPState{
		pins: make(map[string]string),
		age:  make(map[string]int64),
	}
}

// Get returns the pinned provider for a preference
func (l *LKGPState) Get(preference string) string {
	l.mu.RLock()
	defer l.mu.RUnlock()
	return l.pins[preference]
}

// Set pins a provider for a preference
func (l *LKGPState) Set(preference, provider string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.pins[preference] = provider
}

// Delete removes a pin
func (l *LKGPState) Delete(preference string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.pins, preference)
}

// LKGPConfig holds configuration for the LKGP strategy
type LKGPConfig struct {
	PinWeight    float64 `json:"pin_weight"`    // 0.0-1.0: how strongly to prefer pinned provider
	MaxPinAge    int64   `json:"max_pin_age"`   // Max age in seconds before pin expires
	FallbackRule string `json:"fallback_rule"` // "next_provider" | "next_preference" | "all"
}

// DefaultLKGPConfig returns default LKGP configuration
func DefaultLKGPConfig() LKGPConfig {
	return LKGPConfig{
		PinWeight:    1.0,       // Always prefer pinned provider
		MaxPinAge:    3600,      // 1 hour
		FallbackRule: "next_preference",
	}
}

// LKGPStrategy implements LKGP as an explicit routing strategy
type LKGPStrategy struct {
	mu       sync.RWMutex
	state    *LKGPState
	config   LKGPConfig
}

// NewLKGPStrategy creates a new LKGP strategy
func NewLKGPStrategy(config LKGPConfig) *LKGPStrategy {
	return &LKGPStrategy{
		state:  NewLKGPState(),
		config: config,
	}
}

// Name returns the strategy name
func (s *LKGPStrategy) Name() string {
	return "lkgp"
}

// Apply applies the LKGP strategy: moves pinned provider to front
func (s *LKGPStrategy) Apply(ctx context.Context, targets []Target, req *Request) []Target {
	pinned := s.getPinned(req.Preference)
	if pinned == "" {
		return targets // No pin, return original order
	}

	// Check pin age
	if s.isPinExpired(req.Preference) {
		s.DeletePin(req.Preference)
		return targets // Pin expired, use original order
	}

	// Move pinned provider to front, preserve order of rest
	return moveToFront(targets, pinned)
}

// GetPinned returns the pinned provider for a preference
func (s *LKGPState) GetPinned(preference string) string {
	return s.Get(preference)
}

// SetPin pins a provider for a preference
func (s *LKGPState) SetPin(preference, provider string) {
	s.Set(preference, provider)
}

// DeletePin removes a pin
func (s *LKGPState) DeletePin(preference string) {
	s.Delete(preference)
}

// IsPinExpired checks if a pin has expired
func (s *LKGPState) IsPinExpired(preference string) bool {
	// TODO: implement age check
	return false
}

// GetPinned returns the pinned provider for a preference
func (s *LKGPStrategy) getPinned(preference string) string {
	return s.state.Get(preference)
}

// SetPin pins a provider for a preference
func (s *LKGPStrategy) SetPin(preference, provider string) {
	s.state.Set(preference, provider)
}

// DeletePin removes a pin
func (s *LKGPStrategy) DeletePin(preference string) {
	s.state.Delete(preference)
}

// IsPinExpired checks if a pin has expired
func (s *LKGPStrategy) isPinExpired(preference string) bool {
	// TODO: implement age check using s.config.MaxPinAge
	return false
}

// moveToFront moves the pinned provider to the front of the list
func moveToFront(targets []Target, pinned string) []Target {
	if len(targets) == 0 {
		return targets
	}

	// Find pinned target
	var pinnedIdx = -1
	for i, t := range targets {
		if t.Provider == pinned {
			pinnedIdx = i
			break
		}
	}

	if pinnedIdx == -1 {
		return targets // Pinned provider not in targets
	}

	if pinnedIdx == 0 {
		return targets // Already at front
	}

	// Create new slice with pinned at front
	result := make([]Target, len(targets))
	result[0] = targets[pinnedIdx]
	copy(result[1:], targets[:pinnedIdx])
	copy(result[pinnedIdx+1:], targets[pinnedIdx+1:])
	return result
}