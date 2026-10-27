package strategy

import (
	"context"
	"sort"
)

// PriorityStrategy implements priority-based routing
type PriorityStrategy struct{}

func NewPriorityStrategy() *PriorityStrategy {
	return &PriorityStrategy{}
}

func (s *PriorityStrategy) Name() string {
	return "priority"
}

func (s *PriorityStrategy) Apply(ctx context.Context, targets []Target, req *Request) []Target {
	// Sort by priority (lower = higher priority), then by provider name for stability
	sort.SliceStable(targets, func(i, j int) bool {
		if targets[i].Priority != targets[j].Priority {
			return targets[i].Priority < targets[j].Priority
		}
		return targets[i].Provider < targets[j].Provider
	})
	return targets
}