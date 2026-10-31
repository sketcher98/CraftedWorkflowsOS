package main

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"time"
)

// KeyPoolSelector implements Bifrost's weighted-random key selector
// (core/keyselectors/weightedrandom.go:10-66) over a provider's HEALTHY
// keys, with a used-keys set for per-request rotation. Static weights are
// uniform for now; health-adjusted weighting plugs in later via p.keys.

// selectProviderKey picks a healthy key, preferring keys NOT in usedKeys
// (rotation), weighted uniformly. Falls back to any healthy key when all
// healthy keys are used. Returns ("", "", false) when no key is healthy.
func selectProviderKey(p *Provider, usedKeys map[string]bool) (string, string, bool) {
	now := time.Now()

	p.mu.Lock()
	defer p.mu.Unlock()

	if p.keys == nil {
		p.keys = make(map[string]*KeyHealth)
	}
	if usedKeys == nil {
		usedKeys = map[string]bool{}
	}

	type candidate struct {
		env string
		key string
	}
	var healthy []candidate
	var fresh []candidate

	for _, env := range providerKeyEnvs(p) {
		key := os.Getenv(env)
		if key == "" {
			continue
		}
		h := p.keys[env]
		if h == nil {
			h = &KeyHealth{}
			p.keys[env] = h
		}
		if h.HardFailed || (!h.CooldownTil.IsZero() && now.Before(h.CooldownTil)) {
			continue
		}
		healthy = append(healthy, candidate{env, key})
		if !usedKeys[env] {
			fresh = append(fresh, candidate{env, key})
		}
	}

	if len(healthy) == 0 {
		return "", "", false
	}

	// Prefer unused (fresh) keys for rotation; if every healthy key has
	// been used this request, reset the pool (Bifrost: fresh weighted round).
	pool := fresh
	if len(pool) == 0 {
		pool = healthy
	}

	if len(pool) == 1 {
		c := pool[0]
		usedKeys[c.env] = true
		return c.env, c.key, true
	}

	// Selection mode from p.keyStrategy: "round_robin" deterministically
	// walks healthy keys (even long-run distribution); "random" uses the
	// uniform weighted-random below.
	if p.keyStrategy == "round_robin" {
		idx := int(p.rrCounter % uint64(len(pool)))
		p.rrCounter++
		c := pool[idx]
		usedKeys[c.env] = true
		return c.env, c.key, true
	}

	// Weighted random — uniform weights (all 1). Overflow-hardened: if the
	// sum is non-finite, fall back to uniform.
	total := float64(len(pool))
	if total <= 0 {
		return "", "", false
	}
	r := rand.Float64() * total
	idx := int(r)
	if idx >= len(pool) {
		idx = len(pool) - 1
	}
	c := pool[idx]
	usedKeys[c.env] = true
	return c.env, c.key, true
}

// keyPoolState renders the current key health for telemetry/debug use.
func keyPoolState(p *Provider) []map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := []map[string]any{}
	for _, env := range providerKeyEnvs(p) {
		h := p.keys[env]
		state := map[string]any{"key_env": env}
		if h == nil {
			state["state"] = "unprobed"
		} else if h.HardFailed {
			state["state"] = "hard_failed"
		} else if !h.CooldownTil.IsZero() {
			state["state"] = "cooldown"
			state["until"] = h.CooldownTil.Format(time.RFC3339)
		} else {
			state["state"] = "healthy"
		}
		out = append(out, state)
	}
	return out
}

// debugKeyPool is a small JSON dump for the TUI diagnostics.
func debugKeyPool(p *Provider) string {
	b, err := json.Marshal(keyPoolState(p))
	if err != nil {
		return fmt.Sprintf("keypool marshal error: %v", err)
	}
	return string(b)
}
