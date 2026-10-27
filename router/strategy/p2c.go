package strategy

import (
	"context"
	"math/rand"
)

// P2CStrategy implements Power of Two Choices routing
// Sample two random targets, pick the better one based on score
type P2CStrategy struct{}

func NewP2CStrategy() *P2CStrategy {
	return &P2CStrategy{}
}

func (s *P2CStrategy) Name() string {
	return "p2c"
}

func (s *P2CStrategy) Apply(ctx context.Context, targets []Target, req *Request) []Target {
	if len(targets) < 2 {
		return targets
	}

	// Sample two random targets
	a := targets[rand.Intn(len(targets))]
	b := targets[rand.Intn(len(targets))]

	// Ensure they're different
	if a.Provider == b.Provider && a.Model == b.Model {
		return targets
	}

	// Pick the better one based on score
	better := pickBetter(a, b)

	// Move the better one to front, keep rest in original order
	result := []Target{better}
	for _, t := range targets {
		if t.Provider != better.Provider || t.Model != better.Model {
			result = append(result, t)
		}
	}

	return result
}

// pickBetter selects the better target based on scoring
func pickBetter(a, b Target) Target {
	scoreA := scoreTarget(a)
	scoreB := scoreTarget(b)

	if scoreA >= scoreB {
		return a
	}
	return b
}

// scoreTarget calculates a score for a target based on health, latency, cost
func scoreTarget(t Target) float64 {
	score := 0.0

	// Health factor (0-100)
	if m, ok := t.Metadata["healthy"].(bool); ok && m {
		score += 50
	} else {
		score += 10 // Unhealthy penalty
	}

	// Latency factor (lower is better)
	if latency, ok := t.Metadata["avg_latency_ms"].(float64); ok {
		if latency < 1000 {
			score += 30
		} else if latency < 3000 {
			score += 20
		} else {
			score += 10
		}
	}

	// Cost factor (lower is better)
	if cost, ok := t.Metadata["cost_per_1k_tokens"].(float64); ok {
		if cost < 0.01 {
			score += 20
		} else if cost < 0.05 {
			score += 10
		} else {
			score += 5
		}
	}

	// Healthy bonus
	if healthy, ok := t.Metadata["healthy"].(bool); ok && healthy {
		score += 10
	}

	return score
}