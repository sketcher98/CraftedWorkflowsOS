package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"craftedworkflows-router/strategy"
)

type Provider struct {
	Name    string   `json:"name"`
	BaseURL string   `json:"base_url"`
	KeyEnv  string   `json:"key_env"`
	KeyEnvs []string `json:"key_envs"`

	mu        sync.Mutex
	routes    map[string]*RouteHealth
	keys      map[string]*KeyHealth
	catalog   map[string]bool
	catalogAt time.Time
}

type RouteHealth struct {
	Failures    int
	CooldownTil time.Time
	HardFailed  bool
	WarmAt      time.Time
}

type KeyHealth struct {
	Failures    int
	CooldownTil time.Time
	HardFailed  bool
}

type Route struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
}

type Preference struct {
	Name          string  `json:"name"`
	ContextWindow int     `json:"context_window"`
	Routes        []Route `json:"routes"`
}

type RetryConfig struct {
	FailureLimit             int `json:"failure_limit"`
	CooldownSeconds          int `json:"cooldown_seconds"`
	MaxCooldownSeconds       int `json:"max_cooldown_seconds"`
	AllFailedWait            int `json:"all_failed_wait_seconds"`
	HardFailedWaitSeconds    int `json:"hard_failed_wait_seconds"`
	RouteTimeoutSeconds      int `json:"route_timeout_seconds"`
	StreamIdleTimeoutSeconds int `json:"stream_idle_timeout_seconds"`
	// FirstEventTimeoutSeconds: fail fast when a provider sends headers but
	// no data at all within this window (pre-commit abort → failover).
	FirstEventTimeoutSeconds int `json:"first_event_timeout_seconds"`
	// C (hold-and-serve): how long an exhausted router PARKS a request
	// re-checking cooldowns before emitting the legacy 503. 0 disables hold.
	MaxHoldSeconds int `json:"max_hold_seconds"`
}

type Config struct {
	Host                  string `json:"host"`
	Port                  int    `json:"port"`
	CatalogRefreshSeconds int    `json:"catalog_refresh_seconds"`
	// Strategy selects the routing strategy: "" / "configured" (default,
	// configured order), "lkgp" (opt-in), "priority", or "p2c".
	// Validated against the strategy trait table (strategy/traits.go).
	Strategy    string       `json:"strategy"`
	Retry       RetryConfig  `json:"retry"`
	Preferences []Preference `json:"preferences"`
	Providers   []*Provider  `json:"providers"`
	// Cache: exact-match response cache (non-stream requests only).
	Cache CacheConfig `json:"cache"`
	// Budget: per-provider sliding-window admission limits.
	Budget BudgetConfig `json:"budget"`
	// Size: request body limits.
	Size SizeConfig `json:"size"`
}

type CacheConfig struct {
	Enabled    bool `json:"enabled"`
	MaxSize    int  `json:"max_size"`
	TTLSeconds int  `json:"ttl_seconds"`
}

type SizeConfig struct {
	MaxRequestMB int `json:"max_request_mb"` // 0 = unlimited (default 10)
}

type BudgetConfig struct {
	Enabled           bool `json:"enabled"`
	RequestsPerMinute int  `json:"requests_per_minute"` // per provider, 0 = unlimited
	WindowSeconds     int  `json:"window_seconds"`      // sliding window (default 60)
}

var (
	cfg   Config
	cfgMu sync.RWMutex
)

// StrategyRegistry holds all routing strategies
var strategyRegistry *strategy.Registry

// responseCache is the exact-match cache (enabled via config).
var responseCache *ExactMatchCache

// LKGP state is now managed by the LKGPStrategy

func providerByName(name string) *Provider {
	cfgMu.RLock()
	defer cfgMu.RUnlock()

	for _, p := range cfg.Providers {
		if p.Name == name {
			return p
		}
	}
	return nil
}

func routeHealthLocked(p *Provider, model string) *RouteHealth {
	if p.routes == nil {
		p.routes = make(map[string]*RouteHealth)
	}
	if p.routes[model] == nil {
		p.routes[model] = &RouteHealth{}
	}
	return p.routes[model]
}

func providerHealthy(p *Provider, model string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := p.routes[model]
	if h == nil {
		return true
	}

	if h.HardFailed {
		return false
	}
	if !h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil) {
		return false
	}
	// Warm probe within 10 minutes overrides failure count
	if !h.WarmAt.IsZero() && time.Since(h.WarmAt) < 10*time.Minute {
		return true
	}
	// B-1: a cell is eligible once its cooldown has expired, regardless of
	// the accumulated Failures counter. Previously this returned
	// `h.Failures == 0`, which permanently poisoned a cell after a single
	// failure (Failures only ever reset on success, and success required
	// eligibility — a deadlock). Cooldown expiry must restore eligibility,
	// or a transient error kills a route forever.
	return true
}

func providerCooldown(p *Provider, model string) time.Duration {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := p.routes[model]
	if h == nil || h.CooldownTil.IsZero() {
		return 0
	}

	d := time.Until(h.CooldownTil)
	if d < 0 {
		return 0
	}

	return d
}

func providerFailed(p *Provider, model string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := routeHealthLocked(p, model)
	wasHealthy := !h.HardFailed && (h.CooldownTil.IsZero() || time.Now().After(h.CooldownTil))
	h.Failures++

	limit := cfg.Retry.FailureLimit
	if limit < 1 {
		limit = 1
	}

	if h.Failures < limit {
		return
	}

	cooldown := cfg.Retry.CooldownSeconds
	if cooldown < 1 {
		cooldown = 60
	}

	// Exponential backoff: double for each failure BEYOND the limit
	// Failure limit=1: failure 1 -> base, failure 2 -> 2x, failure 3 -> 4x, etc.
	// F6: also cap when max_cooldown_seconds is 0 (default cap 10min).
	extraFailures := h.Failures - limit
	for i := 0; i < extraFailures; i++ {
		cooldown *= 2
		maxCap := cfg.Retry.MaxCooldownSeconds
		if maxCap < 1 {
			maxCap = 600
		}
		if cooldown >= maxCap {
			cooldown = maxCap
			break
		}
	}

	if cfg.Retry.MaxCooldownSeconds > 0 &&
		cooldown > cfg.Retry.MaxCooldownSeconds {
		cooldown = cfg.Retry.MaxCooldownSeconds
	}

	h.CooldownTil = time.Now().Add(time.Duration(cooldown) * time.Second)

	log.Printf(
		"provider=%s model=%s failure=%d cooldown=%ds until=%s",
		p.Name,
		model,
		h.Failures,
		cooldown,
		h.CooldownTil.Format(time.RFC3339),
	)

	logRouteEvent(RouteEvent{
		Event:           "route_cooldown",
		Provider:        p.Name,
		Model:           model,
		Outcome:         "cooldown",
		CooldownSeconds: cooldown,
		CooldownUntil:   h.CooldownTil.Format(time.RFC3339),
	})

	// GAP-007: health transition (healthy -> cooldown).
	if wasHealthy {
		logRouteEvent(RouteEvent{
			Event:           "health_transition",
			Provider:        p.Name,
			Model:           model,
			Outcome:         "healthy_to_cooldown",
			CooldownSeconds: cooldown,
			CooldownUntil:   h.CooldownTil.Format(time.RFC3339),
		})
	}
}

func providerSucceeded(p *Provider, model string, preferenceName string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := routeHealthLocked(p, model)
	wasUnhealthy := h.Failures > 0 || !h.CooldownTil.IsZero() || h.HardFailed

	h.Failures = 0
	h.CooldownTil = time.Time{}
	h.HardFailed = false
	h.WarmAt = time.Now()

	if wasUnhealthy {
		logRouteEvent(RouteEvent{
			Event:      "route_recovered",
			Provider:   p.Name,
			Model:      model,
			Outcome:    "recovered",
			Preference: preferenceName,
		})
		// GAP-007: health transition back to healthy.
		logRouteEvent(RouteEvent{
			Event:    "health_transition",
			Provider: p.Name,
			Model:    model,
			Outcome:  "unhealthy_to_healthy",
		})
	}
}

func providerKeySucceeded(p *Provider, env string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.keys == nil {
		p.keys = make(map[string]*KeyHealth)
	}

	h := p.keys[env]
	if h == nil {
		h = &KeyHealth{}
		p.keys[env] = h
	}

	h.Failures = 0
	h.CooldownTil = time.Time{}
	h.HardFailed = false
}

func providerKeyFailed(p *Provider, env string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.keys == nil {
		p.keys = make(map[string]*KeyHealth)
	}

	h := p.keys[env]
	if h == nil {
		h = &KeyHealth{}
		p.keys[env] = h
	}

	h.Failures++

	limit := cfg.Retry.FailureLimit
	if limit < 1 {
		limit = 1
	}

	if h.Failures < limit {
		return
	}

	cooldown := cfg.Retry.CooldownSeconds
	if cooldown < 1 {
		cooldown = 60
	}

	h.CooldownTil = time.Now().Add(time.Duration(cooldown) * time.Second)

	log.Printf(
		"provider=%s key_env=%s failure=%d cooldown=%ds",
		p.Name,
		env,
		h.Failures,
		cooldown,
	)

	// GAP-005: a key hitting its failure limit should be rotatable — emit
	// the signal as visible telemetry so operators can act on it.
	logRouteEvent(RouteEvent{
		Event:           "credential_rotation_signal",
		Provider:        p.Name,
		KeyEnv:          env,
		Outcome:         "key_failed",
		CooldownSeconds: cooldown,
		CooldownUntil:   h.CooldownTil.Format(time.RFC3339),
		FallbackDepth:   h.Failures,
	})
}

func providerKeyHealth(p *Provider, env string) *KeyHealth {
	p.mu.Lock()
	defer p.mu.Unlock()

	if p.keys == nil {
		p.keys = make(map[string]*KeyHealth)
	}

	h := p.keys[env]
	if h == nil {
		h = &KeyHealth{}
		p.keys[env] = h
	}

	return h
}

func providerKeys(p *Provider) []string {
	var keys []string
	for _, env := range providerKeyEnvs(p) {
		if key := os.Getenv(env); key != "" {
			keys = append(keys, key)
		}
	}
	return keys
}

func providerKeyEnvs(p *Provider) []string {
	var envs []string
	seen := make(map[string]bool)

	add := func(env string) {
		if env == "" || seen[env] {
			return
		}
		seen[env] = true
		envs = append(envs, env)
	}

	add(p.KeyEnv)
	for _, env := range p.KeyEnvs {
		add(env)
	}

	return envs
}

func providerHardFailed(p *Provider, model string, requestID string, preferenceName string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := routeHealthLocked(p, model)
	h.HardFailed = true

	log.Printf(
		"provider=%s model=%s route marked unavailable",
		p.Name,
		model,
	)

	logRouteEvent(RouteEvent{
		Event:    "route_quarantined",
		Provider: p.Name,
		Model:    model,
		Outcome:  "permanent",
	})
	// GAP-007: health transition to hard-failed.
	logRouteEvent(RouteEvent{
		Event:    "health_transition",
		Provider: p.Name,
		Model:    model,
		Outcome:  "unhealthy_to_hardfailed",
	})
}

// tryRecoverHardFailed attempts to recover hard-failed routes by making a test request
func tryRecoverHardFailed(p *Provider, model string) bool {
	client := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http.Transport{
			DialContext: (&net.Dialer{
				Timeout: 5 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout: 5 * time.Second,
		},
	}

	for _, keyEnv := range providerKeyEnvs(p) {
		p.mu.Lock()
		h := p.keys[keyEnv]
		skip := h != nil && (h.HardFailed ||
			(!h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil)))
		p.mu.Unlock()

		if skip {
			continue
		}

		key := os.Getenv(keyEnv)
		if key == "" {
			continue
		}

		req, err := http.NewRequest(
			http.MethodPost,
			strings.TrimRight(p.BaseURL, "/")+"/chat/completions",
			bytes.NewReader([]byte(`{"model":"`+model+`","messages":[{"role":"user","content":"health check"}]}`)),
		)
		if err != nil {
			continue
		}

		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)

		resp, err := client.Do(req)
		if err != nil {
			continue
		}

		status := resp.StatusCode
		resp.Body.Close()

		if status >= 200 && status < 300 {
			providerKeySucceeded(p, keyEnv)
			return true
		}

		if status == http.StatusUnauthorized {
			providerKeyFailed(p, keyEnv)
			continue
		}

		// Preserve existing provider-level recovery semantics for
		// 403/404/5xx and other non-2xx responses.
		return false
	}

	return false
}

// hardFailureRecoveryLoop periodically checks hard-failed routes for recovery
func hardFailureRecoveryLoop() {
	ticker := time.NewTicker(60 * time.Second) // Check every minute
	defer ticker.Stop()

	for range ticker.C {
		cfgMu.RLock()
		providers := append([]*Provider(nil), cfg.Providers...)
		cfgMu.RUnlock()

		for _, p := range providers {
			// B-2: collect hard-failed models WITHOUT holding p.mu across
			// the probe. tryRecoverHardFailed takes p.mu itself — holding it
			// here self-deadlocked (non-reentrant Mutex) on the first
			// hard-failed route, wedging the entire provider forever.
			var toRecover []string
			p.mu.Lock()
			for model, h := range p.routes {
				if h != nil && h.HardFailed {
					toRecover = append(toRecover, model)
				}
			}
			p.mu.Unlock()

			for _, model := range toRecover {
				// Try to recover
				if tryRecoverHardFailed(p, model) {
					p.mu.Lock()
					if h := p.routes[model]; h != nil {
						h.HardFailed = false
						h.Failures = 0
						h.CooldownTil = time.Time{}
					}
					p.mu.Unlock()

					log.Printf(
						"provider=%s model=%s recovered from hard failure",
						p.Name,
						model,
					)

					logRouteEvent(RouteEvent{
						Event:    "route_recovered",
						Provider: p.Name,
						Model:    model,
						Outcome:  "recovered",
						Context:  "background",
					})
					// A3: emit the health transition for consistency with the
					// foreground recovery path (providerSucceeded).
					logRouteEvent(RouteEvent{
						Event:    "health_transition",
						Provider: p.Name,
						Model:    model,
						Outcome:  "unhealthy_to_healthy",
					})
				}
			}
		}
	}
}

func requestModel(body []byte) string {
	var payload struct {
		Model string `json:"model"`
	}

	if json.Unmarshal(body, &payload) != nil {
		return ""
	}

	return strings.TrimSpace(payload.Model)
}

// requestStream reports whether the request asked for streaming (cache is
// non-stream only).
func requestStream(body []byte) bool {
	var payload struct {
		Stream bool `json:"stream"`
	}
	if json.Unmarshal(body, &payload) != nil {
		return false
	}
	return payload.Stream
}

func preferenceForModel(model string) *Preference {
	cfgMu.RLock()
	defer cfgMu.RUnlock()

	model = strings.TrimSpace(model)

	for i := range cfg.Preferences {
		p := &cfg.Preferences[i]

		if strings.EqualFold(model, p.Name) {
			return p
		}

		for _, route := range p.Routes {
			if strings.EqualFold(model, route.Model) {
				return p
			}
		}
	}

	// Unknown model - return nil to trigger explicit error
	return nil
}

func routeAvailable(p *Provider, model string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()

	if h := p.routes[model]; h != nil && h.HardFailed {
		return false
	}

	// Unknown catalog = don't block the route.
	// The real completion request remains authoritative.
	if len(p.catalog) == 0 {
		return true
	}

	return p.catalog[model]
}

func catalogURL(p *Provider) string {
	return strings.TrimRight(p.BaseURL, "/") + "/models"
}

func refreshProviderCatalog(p *Provider) {
	client := &http.Client{
		Timeout: 15 * time.Second,
		Transport: &http.Transport{
			DialContext: (&net.Dialer{
				Timeout: 10 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout: 10 * time.Second,
		},
	}

	for _, keyEnv := range providerKeyEnvs(p) {
		p.mu.Lock()
		h := p.keys[keyEnv]
		skip := h != nil && (h.HardFailed ||
			(!h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil)))
		p.mu.Unlock()

		if skip {
			continue
		}

		key := os.Getenv(keyEnv)
		if key == "" {
			continue
		}

		req, err := http.NewRequest(
			http.MethodGet,
			catalogURL(p),
			nil,
		)
		if err != nil {
			return
		}

		req.Header.Set("Authorization", "Bearer "+key)

		resp, err := client.Do(req)
		if err != nil {
			log.Printf("catalog provider=%s key_env=%s error=%v", p.Name, keyEnv, err)
			// GAP-004: catalog fetch failure is visible telemetry.
			logRouteEvent(RouteEvent{
				Event:    "catalog_skip",
				Provider: p.Name,
				KeyEnv:   keyEnv,
				Outcome:  "error",
				Error:    err.Error(),
			})
			continue
		}

		status := resp.StatusCode

		if status == http.StatusUnauthorized {
			resp.Body.Close()
			providerKeyFailed(p, keyEnv)
			// rotation signal emitted inside providerKeyFailed (GAP-005)
			continue
		}

		if status < 200 || status >= 300 {
			log.Printf(
				"catalog provider=%s key_env=%s status=%s",
				p.Name,
				keyEnv,
				resp.Status,
			)
			resp.Body.Close()
			logRouteEvent(RouteEvent{
				Event:    "catalog_skip",
				Provider: p.Name,
				KeyEnv:   keyEnv,
				Outcome:  "http_error",
				Status:   status,
			})
			return
		}

		var payload struct {
			Data []struct {
				ID string `json:"id"`
			} `json:"data"`
		}

		err = json.NewDecoder(resp.Body).Decode(&payload)
		resp.Body.Close()

		if err != nil {
			log.Printf("catalog provider=%s key_env=%s decode=%v", p.Name, keyEnv, err)
			return
		}

		catalog := make(map[string]bool, len(payload.Data))
		for _, model := range payload.Data {
			if model.ID != "" {
				catalog[model.ID] = true
			}
		}

		providerKeySucceeded(p, keyEnv)

		p.mu.Lock()
		p.catalog = catalog
		p.catalogAt = time.Now()
		p.mu.Unlock()

		log.Printf(
			"catalog provider=%s key_env=%s models=%d",
			p.Name,
			keyEnv,
			len(catalog),
		)
		// GAP-004: successful refresh is visible telemetry.
		logRouteEvent(RouteEvent{
			Event:    "catalog_refreshed",
			Provider: p.Name,
			KeyEnv:   keyEnv,
			Outcome:  "ok",
			Status:   status,
			Model:    fmt.Sprintf("%d", len(catalog)),
		})
		return
	}

	log.Printf("catalog provider=%s no usable API key succeeded", p.Name)
}

func refreshAllCatalogs() {
	cfgMu.RLock()
	providers := append([]*Provider(nil), cfg.Providers...)
	cfgMu.RUnlock()

	for _, p := range providers {
		go refreshProviderCatalog(p)
	}
}

func catalogLoop() {
	refreshAllCatalogs()

	seconds := cfg.CatalogRefreshSeconds
	if seconds < 60 {
		seconds = 3600
	}

	ticker := time.NewTicker(
		time.Duration(seconds) * time.Second,
	)
	defer ticker.Stop()

	for range ticker.C {
		refreshAllCatalogs()
	}
}

func earliestCooldown() time.Duration {
	cfgMu.RLock()
	providers := append([]*Provider(nil), cfg.Providers...)
	cfgMu.RUnlock()

	var earliest time.Duration
	hasHardFailed := false

	for _, p := range providers {
		p.mu.Lock()

		for _, h := range p.routes {
			if h == nil {
				continue
			}

			// Track hard-failed routes for wait calculation
			if h.HardFailed {
				hasHardFailed = true
				continue
			}

			if h.CooldownTil.IsZero() {
				continue
			}

			d := time.Until(h.CooldownTil)
			if d > 0 && (earliest == 0 || d < earliest) {
				earliest = d
			}
		}

		// F2: key cooldowns matter too — if every key for a provider is
		// cooling, the provider cannot serve, and the hold loop would spin
		// silently until max_hold_seconds instead of surfacing it.
		for _, kh := range p.keys {
			if kh == nil || kh.CooldownTil.IsZero() {
				continue
			}
			d := time.Until(kh.CooldownTil)
			if d > 0 && (earliest == 0 || d < earliest) {
				earliest = d
			}
		}

		p.mu.Unlock()
	}

	// If there are hard-failed routes but no cooldown routes,
	// return a special marker to indicate hard-failed wait needed
	if earliest == 0 && hasHardFailed {
		return -1 // Special marker: hard-failed routes exist
	}

	return earliest
}

type Status struct {
	Name       string `json:"name"`
	Healthy    bool   `json:"healthy"`
	Failures   int    `json:"failures"`
	Cooldown   string `json:"cooldown,omitempty"`
	HardFailed bool   `json:"hard_failed"`
	Catalog    int    `json:"catalog_models"`
	// B-7: per-cell state. /health previously reported only a provider-level
	// boolean that hid dead cells (AUDIT-003) — 5 of 7 cells could be dead
	// and the provider still showed green.
	Routes []CellStatus `json:"routes,omitempty"`
}

// CellStatus is the per-provider×model health for one route.
type CellStatus struct {
	Model         string `json:"model"`
	Healthy       bool   `json:"healthy"`
	Failures      int    `json:"failures"`
	HardFailed    bool   `json:"hard_failed"`
	CooldownUntil string `json:"cooldown_until,omitempty"`
	Warm          bool   `json:"warm,omitempty"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// C (hold-and-serve): park an exhausted request instead of 503ing it.
// Ticks every second; the first cooldown that expires releases the hold and
// the caller re-walks the route chain. Client cancel aborts. Exceeding
// max_hold_seconds falls back to the legacy 503 router_exhausted signal.
// Returns true if the request should be retried, false if it was answered.
func holdAndServe(w http.ResponseWriter, r *http.Request) bool {
	maxHold := time.Duration(cfg.Retry.MaxHoldSeconds) * time.Second
	if maxHold <= 0 {
		waitForRecovery(w, r)
		return false
	}
	log.Printf("hold-and-serve: parking request up to %s (all routes cooling)", maxHold)

	holdDeadline := time.NewTimer(maxHold)
	defer holdDeadline.Stop()
	tick := time.NewTicker(time.Second)
	defer tick.Stop()

	for {
		select {
		case <-r.Context().Done():
			log.Printf("hold-and-serve: client cancelled during hold")
			return false
		case <-holdDeadline.C:
			log.Printf("hold-and-serve: max hold %s exceeded, emitting 503", maxHold)
			waitForRecovery(w, r)
			return false
		case <-tick.C:
			// earliestCooldown() <= 0 means some route left cooldown: retry now.
			wait := earliestCooldown()
			if wait == -1 {
				continue // all hard-failed: probe loop owns recovery; keep holding
			}
			if wait <= 0 {
				// No route is cooling — but if no route could be ATTEMPTED either
				// (every route skipped as catalog-absent / unknown model), there is
				// nothing to wait for. Re-walking would spin forever at 1Hz and
				// never answer the client. Emit the legacy 503 instead.
				if nextAttemptablePreference() == nil {
					log.Printf("hold-and-serve: no attemptable route (all skipped); emitting 503")
					waitForRecovery(w, r)
					return false
				}
				log.Printf("hold-and-serve: cooldown expired, re-walking routes")
				return true
			}
		}
	}
}

func waitForRecovery(w http.ResponseWriter, r *http.Request) {
	wait := earliestCooldown()

	// Special case: hard-failed routes exist but no cooldown routes
	if wait == -1 {
		wait = time.Duration(cfg.Retry.HardFailedWaitSeconds) * time.Second
		if wait < time.Second {
			wait = time.Second
		}
		log.Printf("all routes hard-failed; waiting %s for recovery probe", wait)
	} else if wait <= 0 {
		wait = time.Duration(cfg.Retry.AllFailedWait) * time.Second
		if wait < time.Second {
			wait = time.Second
		}
		log.Printf("all routes unavailable; waiting %s", wait)
	}

	// Return 503 immediately instead of blocking the request
	retryAfterSec := int(wait.Seconds())
	if retryAfterSec < 1 {
		retryAfterSec = 1
	}

	w.Header().Set("Retry-After", strconv.Itoa(retryAfterSec))
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusServiceUnavailable)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":            "router_exhausted",
		"retry_after_secs": strconv.Itoa(retryAfterSec),
	})

	// Recovery continues in hardFailureRecoveryLoop() background - no sleep here
}

func classifyFailure(status int, err error) (transient bool, permanent bool) {
	if err != nil {
		return true, false
	}

	switch {
	case status >= 200 && status < 300:
		// 2xx = success — NOT a failure class. Return (false, false) so
		// tryRoute falls through into streamResponse. (B-9 regression fix:
		// the default branch must never swallow success into transient.)
		return false, false
	case status == http.StatusTooManyRequests:
		return true, false
	case status >= 500:
		return true, false
	case status == http.StatusNotFound:
		return false, true
	case status == http.StatusUnauthorized:
		// 401 = invalid API key, treat as transient (key might be rotated)
		// Provider-level quarantine would be better but requires more design
		return true, false
	case status == http.StatusRequestTimeout:
		// 408 = upstream stalled waiting for us — transient, retryable.
		return true, false
	case status == http.StatusForbidden:
		return false, true
	case status == http.StatusBadRequest, status == 413, status == http.StatusUnprocessableEntity:
		// B-9: 400/413/422 are permanent client errors — never "success".
		// Previously the default branch returned (false, false), which made
		// tryRoute fall through into streamResponse and record outcome
		// "success" for a 413 (AUDIT-007).
		return false, true
	default:
		// B-9: any OTHER 4xx is the client's fault → permanent. Unknown codes
		// must never be silently counted as success.
		if status >= 400 && status < 500 {
			return false, true
		}
		// Unexpected 2xx/3xx — treat as transient so the route can retry.
		return true, false
	}
}

func buildRequest(
	r *http.Request,
	body []byte,
	route Route,
	p *Provider,
	key string,
) (*http.Request, error) {
	var payload map[string]interface{}

	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, err
	}

	payload["model"] = route.Model

	reqBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	target := strings.TrimRight(p.BaseURL, "/") + "/chat/completions"

	req, err := http.NewRequestWithContext(
		r.Context(),
		http.MethodPost,
		target,
		bytes.NewReader(reqBody),
	)
	if err != nil {
		return nil, err
	}

	for key, values := range r.Header {
		if isForbiddenUpstreamHeaderName(key) {
			continue
		}
		for _, value := range values {
			value = sanitizeHeaderValue(value)
			if value == "" {
				continue
			}
			req.Header.Add(key, value)
		}
	}

	req.Header.Set("Content-Type", "application/json")

	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}

	return req, nil
}

func copyHeaders(dst http.ResponseWriter, src http.Header) {
	for key, values := range src {
		for _, value := range values {
			dst.Header().Add(key, value)
		}
	}
}

type sseApplicationError struct {
	Message string
	Type    string
	Code    int
}

func (e *sseApplicationError) Error() string {
	return fmt.Sprintf(
		"upstream SSE application error: %s (type=%s code=%d)",
		e.Message,
		e.Type,
		e.Code,
	)
}

func findSSEEventEnd(data []byte) int {
	best := -1

	for _, sep := range [][]byte{
		[]byte("\r\n\r\n"),
		[]byte("\n\n"),
		[]byte("\r\r"),
	} {
		if i := bytes.Index(data, sep); i >= 0 {
			end := i + len(sep)
			if best == -1 || end < best {
				best = end
			}
		}
	}

	return best
}

func inspectSSEEvent(event []byte) (meaningful bool, appErr *sseApplicationError) {
	lines := bytes.Split(event, []byte("\n"))
	dataLines := make([][]byte, 0, 2)

	for _, line := range lines {
		line = bytes.TrimSuffix(line, []byte("\r"))

		if len(line) == 0 || line[0] == ':' {
			continue
		}

		switch {
		case bytes.Equal(line, []byte("data")):
			dataLines = append(dataLines, nil)

		case bytes.HasPrefix(line, []byte("data:")):
			value := line[len("data:"):]
			if len(value) > 0 && value[0] == ' ' {
				value = value[1:]
			}
			dataLines = append(dataLines, value)
		}
	}

	if len(dataLines) == 0 {
		return false, nil
	}

	payload := bytes.Join(dataLines, []byte("\n"))
	payload = bytes.TrimSpace(payload)

	if bytes.Equal(payload, []byte("[DONE]")) {
		return true, nil
	}

	var envelope struct {
		Error *struct {
			Message string `json:"message"`
			Type    string `json:"type"`
			Code    int    `json:"code"`
		} `json:"error"`
	}

	if err := json.Unmarshal(payload, &envelope); err == nil && envelope.Error != nil {
		return true, &sseApplicationError{
			Message: envelope.Error.Message,
			Type:    envelope.Error.Type,
			Code:    envelope.Error.Code,
		}
	}

	return true, nil
}

// errFirstEventTimeout is returned by streamResponse when no upstream data
// arrived before the first-event deadline (pre-commit). Fully retryable:
// tryRoute marks the provider failed and the proxy loop fails over.
var errFirstEventTimeout = errors.New("first event timeout: upstream sent no data before deadline")

func streamResponse(
	w http.ResponseWriter,
	resp *http.Response,
	ctx context.Context,
	onCommit func(),
	firstChunkMS *int64,
	started time.Time,
	finalAttempt bool,
) error {
	log.Printf("streamResponse: ENTERED for request %s", w.Header().Get("X-Request-ID"))
	defer resp.Body.Close()

	requestID := w.Header().Get("X-Request-ID")
	routerProvider := w.Header().Get("X-Router-Provider")
	routerModel := w.Header().Get("X-Router-Model")
	routerPreference := w.Header().Get("X-Router-Preference")
	routerFallback := w.Header().Get("X-Router-Fallback")
	routerFallbackCount := w.Header().Get("X-Router-Fallback-Count")
	routerFallbackFrom := w.Header().Get("X-Router-Fallback-From")
	routerPrefFallback := w.Header().Get("X-Router-Preference-Fallback")
	routerPrefFrom := w.Header().Get("X-Router-Preference-From")
	routerPrefTo := w.Header().Get("X-Router-Preference-To")
	routerSkippedHardFailed := w.Header().Get("X-Router-Skipped-Hard-Failed")

	copyHeaders(w, resp.Header)

	if requestID != "" {
		w.Header().Set("X-Request-ID", requestID)
	}
	if routerProvider != "" {
		w.Header().Set("X-Router-Provider", routerProvider)
	}
	if routerModel != "" {
		w.Header().Set("X-Router-Model", routerModel)
	}
	if routerPreference != "" {
		w.Header().Set("X-Router-Preference", routerPreference)
	}
	if routerFallback != "" {
		w.Header().Set("X-Router-Fallback", routerFallback)
	}
	if routerFallbackCount != "" {
		w.Header().Set("X-Router-Fallback-Count", routerFallbackCount)
	}
	if routerFallbackFrom != "" {
		w.Header().Set("X-Router-Fallback-From", routerFallbackFrom)
	}
	if routerPrefFallback != "" {
		w.Header().Set("X-Router-Preference-Fallback", routerPrefFallback)
	}
	if routerPrefFrom != "" {
		w.Header().Set("X-Router-Preference-From", routerPrefFrom)
	}
	if routerPrefTo != "" {
		w.Header().Set("X-Router-Preference-To", routerPrefTo)
	}
	if routerSkippedHardFailed != "" {
		w.Header().Set("X-Router-Skipped-Hard-Failed", routerSkippedHardFailed)
	}

	flusher, canFlush := w.(http.Flusher)
	idleTimeout := func() time.Duration {
		cfgMu.RLock()
		defer cfgMu.RUnlock()
		if cfg.Retry.StreamIdleTimeoutSeconds > 0 {
			return time.Duration(cfg.Retry.StreamIdleTimeoutSeconds) * time.Second
		}
		return 60 * time.Second
	}()

	type readResult struct {
		data []byte
		err  error
	}

	readCh := make(chan readResult, 1)

	read := func() {
		buf := make([]byte, 32*1024)
		n, err := resp.Body.Read(buf)
		if n > 0 {
			buf = buf[:n]
		} else {
			buf = nil
		}
		readCh <- readResult{data: buf, err: err}
	}

	go read()

	timer := time.NewTimer(idleTimeout)
	defer timer.Stop()

	// Fail-fast: separate first-event deadline for the pre-commit window.
	// A provider that sends headers but zero data bytes is stalling before
	// any commitment — abort it early and fail over, instead of waiting the
	// full idle window. The idle timer below still governs post-commit gaps.
	firstEventTimeout := func() time.Duration {
		cfgMu.RLock()
		defer cfgMu.RUnlock()
		if cfg.Retry.FirstEventTimeoutSeconds > 0 {
			return time.Duration(cfg.Retry.FirstEventTimeoutSeconds) * time.Second
		}
		return 45 * time.Second
	}()
	// Bifrost rule: the FINAL attempt never gets a first-token cutoff — it
	// runs to an answer (or the idle/client deadline). Earlier attempts use
	// the first-event timer so a stalled provider hands off fast.
	var firstEventC <-chan time.Time
	if !finalAttempt {
		firstEventTimer := time.NewTimer(firstEventTimeout)
		defer firstEventTimer.Stop()
		firstEventC = firstEventTimer.C
	}

	log.Printf(
		"streamResponse: fail-fast timers armed for request %s, first_event=%s idle=%s",
		requestID,
		firstEventTimeout,
		idleTimeout,
	)

	const maxPreflightBytes = 1 << 20
	preflight := make([]byte, 0, 32*1024)
	scanOffset := 0
	committed := false

	commit := func(data []byte) error {
		w.WriteHeader(resp.StatusCode)

		if len(data) > 0 {
			if firstChunkMS != nil && *firstChunkMS == 0 {
				// GAP-001: time-to-first-byte = first downstream write.
				*firstChunkMS = time.Since(started).Milliseconds()
			}
			if _, writeErr := w.Write(data); writeErr != nil {
				return writeErr
			}
			if canFlush {
				flusher.Flush()
			}
		}

		committed = true

		if onCommit != nil {
			onCommit()
		}

		log.Printf(
			"streamResponse: response committed for request %s provider=%s",
			requestID,
			routerProvider,
		)

		return nil
	}

	for {
		select {
		case <-ctx.Done():
			log.Printf(
				"streamResponse: request context canceled for request %s: %v",
				requestID,
				ctx.Err(),
			)
			resp.Body.Close()
			return ctx.Err()

		case <-timer.C:
			if !committed {
				// Pre-commit silence for the full idle window — treat as a
				// first-event stall: abort upstream, allow provider failover.
				log.Printf(
					"STREAM IDLE TIMEOUT (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID,
					idleTimeout,
				)
				resp.Body.Close()
				return errFirstEventTimeout
			}
			log.Printf(
				"STREAM IDLE TIMEOUT FIRED for request %s after %s",
				requestID,
				idleTimeout,
			)
			resp.Body.Close()
			// Post-commit stall: send an explicit terminal error event so the
			// client sees a signal instead of a silent truncation.
			errEvent := fmt.Sprintf(
				"data: {\"error\":{\"message\":\"upstream stream stalled: stream idle timeout: no data for %s\",\"type\":\"stream_error\",\"code\":\"stream_stalled\"}}\n\ndata: [DONE]\n\n",
				idleTimeout,
			)
			if _, writeErr := w.Write([]byte(errEvent)); writeErr != nil {
				log.Printf(
					"streamResponse: failed to write terminal error event for request %s: %v",
					requestID,
					writeErr,
				)
			} else if canFlush {
				flusher.Flush()
				log.Printf(
					"streamResponse: wrote terminal stream_error event for request %s",
					requestID,
				)
			}
			return fmt.Errorf("stream idle timeout: no data for %s", idleTimeout)

		case <-firstEventC:
			if !committed {
				log.Printf(
					"FIRST-EVENT TIMEOUT FIRED (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID,
					firstEventTimeout,
				)
				resp.Body.Close()
				return errFirstEventTimeout
			}
			// Already committed: first-event timer no longer applies; the
			// idle timer governs from here.
			log.Printf(
				"streamResponse: first-event timer fired post-commit for request %s (ignored)",
				requestID,
			)

		case result := <-readCh:
			if !timer.Stop() {
				select {
				case <-timer.C:
				default:
				}
			}
			if firstEventC != nil {
				// Drain any pending first-event signal so it cannot fire later.
				select {
				case <-firstEventC:
				default:
				}
			}

			log.Printf(
				"streamResponse: read result n=%d err=%v for request %s",
				len(result.data),
				result.err,
				requestID,
			)

			if len(result.data) > 0 {
				if !committed {
					preflight = append(preflight, result.data...)

					for scanOffset < len(preflight) {
						eventEnd := findSSEEventEnd(preflight[scanOffset:])
						if eventEnd < 0 {
							break
						}
						eventEnd += scanOffset

						meaningful, appErr := inspectSSEEvent(
							preflight[scanOffset:eventEnd],
						)

						if appErr != nil {
							log.Printf(
								"streamResponse: upstream SSE application error request=%s provider=%s code=%d message=%q",
								requestID,
								routerProvider,
								appErr.Code,
								appErr.Message,
							)
							resp.Body.Close()
							return appErr
						}

						if meaningful {
							if err := commit(preflight); err != nil {
								resp.Body.Close()
								return err
							}

							preflight = nil
							scanOffset = 0
							break
						}

						scanOffset = eventEnd
					}

					if !committed && len(preflight) >= maxPreflightBytes {
						log.Printf(
							"streamResponse: preflight cap reached request=%s bytes=%d; committing opaque stream",
							requestID,
							len(preflight),
						)
						if err := commit(preflight); err != nil {
							resp.Body.Close()
							return err
						}
						preflight = nil
						scanOffset = 0
					}
				} else {
					if _, writeErr := w.Write(result.data); writeErr != nil {
						resp.Body.Close()
						return writeErr
					}

					if canFlush {
						flusher.Flush()
					}
				}
			}

			if result.err == io.EOF {
				if !committed && len(preflight) > 0 {
					if err := commit(preflight); err != nil {
						return err
					}
				}

				log.Printf("streamResponse: EOF for request %s", requestID)
				return nil
			}

			if result.err != nil {
				log.Printf(
					"streamResponse: read error for request %s: %v",
					requestID,
					result.err,
				)
				// F4: after commit, a read error must still terminate the SSE
				// stream explicitly — otherwise the client waits forever for a
				// [DONE] that never comes and reports truncation.
				if committed {
					_, _ = w.Write([]byte("\ndata: [DONE]\n\n"))
					if canFlush {
						flusher.Flush()
					}
				}
				return result.err
			}

			timer.Reset(idleTimeout)
			go read()
		}
	}
}

func tryRoute(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	route Route,
	p *Provider,
	requestID string,
	preferenceName string,
	fallbackCount int,
	fallbackFrom string,
	skippedHardFailed bool,
	preferenceFallback bool,
	preferenceFrom string,
	preferenceTo string,
	finalAttempt bool,
) (success bool, retryable bool, err error) {
	started := time.Now()
	statusCode := 0
	routeOutcome := "failure"
	// GAP-001/003: captured during the upstream call, emitted on route_result.
	var firstChunkMS int64
	var retryAfterSeconds int
	// F7: classify the failure kind for telemetry.
	errorKind := ""

	// Set routing headers BEFORE upstream call - they must be set before headers are committed
	w.Header().Set("X-Request-ID", requestID)
	w.Header().Set("X-Router-Provider", p.Name)
	w.Header().Set("X-Router-Model", route.Model)
	w.Header().Set("X-Router-Preference", preferenceName)
	if fallbackCount > 0 {
		w.Header().Set("X-Router-Fallback", "true")
		w.Header().Set("X-Router-Fallback-Count", strconv.Itoa(fallbackCount))
		if fallbackFrom != "" {
			w.Header().Set("X-Router-Fallback-From", fallbackFrom)
		}
	}
	// Set Skipped-Hard-Failed header if any route was skipped due to hard-failure
	if skippedHardFailed {
		w.Header().Set("X-Router-Skipped-Hard-Failed", "true")
	}
	// Set preference fallback headers
	if preferenceFallback {
		w.Header().Set("X-Router-Preference-Fallback", "true")
		w.Header().Set("X-Router-Preference-From", preferenceFrom)
		w.Header().Set("X-Router-Preference-To", preferenceTo)
	}

	keyEnv, key, ok := selectProviderKey(p, map[string]bool{})
	if !ok {
		return false, false, fmt.Errorf("no usable API key for provider %s", p.Name)
	}

	// S10/S11: budget admission moved after the cache block (see below).

	defer func() {
		outcome := routeOutcome
		e := RouteEvent{
			Event:      "route_result",
			RequestID:  requestID,
			Provider:   p.Name,
			Model:      route.Model,
			Preference: preferenceName,
			Outcome:    outcome,
			LatencyMS:  time.Since(started).Milliseconds(),
			Status:     statusCode,
			KeyEnv:     keyEnv,
			// GAP-001: time-to-first-byte (ms).
			FirstChunkLatencyMS: firstChunkMS,
			// GAP-002: fallback depth at the time this route served.
			FallbackDepth: fallbackCount,
			// GAP-003: Retry-After captured from a 429 upstream.
			RetryAfterSeconds: retryAfterSeconds,
			ErrorKind:         errorKind,
		}
		if fallbackFrom != "" {
			e.FallbackChain = []string{fallbackFrom, p.Name}
		} else {
			e.FallbackChain = []string{p.Name}
		}
		if err != nil {
			e.Error = err.Error()
		}
		logRouteEvent(e)

		// Record metrics
		requestsTotal.WithLabelValues(p.Name, route.Model, outcome).Inc()
		requestDuration.WithLabelValues(p.Name, route.Model).Observe(time.Since(started).Seconds())
	}()

	logRouteEvent(RouteEvent{
		Event:      "route_attempt",
		RequestID:  requestID,
		Provider:   p.Name,
		Model:      route.Model,
		Preference: preferenceName,
	})

	// Exact-match cache: non-stream only. Hit serves immediately (with a
	// telemetry event); miss tees the response for storage on success.
	var cacheKeyStr string
	var cacheRec *teeWriter
	if responseCache != nil && !requestStream(body) {
		var payload map[string]interface{}
		if json.Unmarshal(body, &payload) == nil {
			cacheKeyStr = cacheKey(payload)
			if ent, ok := responseCache.Get(cacheKeyStr); ok {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("X-Router-Cache", "HIT")
				w.WriteHeader(ent.Status)
				_, _ = w.Write(ent.Body)
				logRouteEvent(RouteEvent{
					Event:      "cache_hit",
					RequestID:  requestID,
					Provider:   p.Name,
					Model:      route.Model,
					Preference: preferenceName,
					Status:     ent.Status,
				})
				return true, false, nil
			}
			cacheRec = &teeWriter{ResponseWriter: w, status: 200}
			w = cacheRec
		}
	}

	// S11/S12: local sliding-window admission (reserve→refund on reject).
	// Runs AFTER the cache check so cache hits never consume provider quota.
	cfgMu.RLock()
	budgetEnabled := cfg.Budget.Enabled
	budgetRPM := cfg.Budget.RequestsPerMinute
	budgetWin := cfg.Budget.WindowSeconds
	cfgMu.RUnlock()
	if budgetEnabled && budgetRPM > 0 && cacheRec != nil {
		win := time.Duration(budgetWin) * time.Second
		cnt := budgetReg.windowFor(p.Name, keyEnv, win).Reserve(1)
		if cnt > int64(budgetRPM) {
			budgetReg.windowFor(p.Name, keyEnv, win).Refund(1)
			log.Printf("budget reject provider=%s model=%s key_env=%s count=%d limit=%d",
				p.Name, route.Model, keyEnv, cnt, budgetRPM)
			return false, true, fmt.Errorf("provider=%s window exceeded (%d/%d)", p.Name, cnt, budgetRPM)
		}
	}

	req, err := buildRequest(r, body, route, p, key)
	if err != nil {
		return false, true, err
	}

	client := &http.Client{
		Transport: &http.Transport{
			DialContext: (&net.Dialer{
				Timeout: 10 * time.Second,
			}).DialContext,
			TLSHandshakeTimeout: 10 * time.Second,
			ResponseHeaderTimeout: func() time.Duration {
				cfgMu.RLock()
				n := cfg.Retry.RouteTimeoutSeconds
				cfgMu.RUnlock()
				if n < 1 {
					n = 60
				}
				return time.Duration(n) * time.Second
			}(),
			ExpectContinueTimeout: time.Second,
		},
	}

	log.Printf(
		"route preference=%s provider=%s model=%s",
		route.Provider,
		p.Name,
		route.Model,
	)

	cfgMu.RLock()
	routeTimeoutSeconds := cfg.Retry.RouteTimeoutSeconds
	cfgMu.RUnlock()
	if routeTimeoutSeconds < 1 {
		routeTimeoutSeconds = 60
	}

	routeCtx, cancel := context.WithCancel(req.Context())
	headersReady := false
	routeTimedOut := false
	var headersMu sync.Mutex

	timer := time.AfterFunc(
		time.Duration(routeTimeoutSeconds)*time.Second,
		func() {
			headersMu.Lock()
			defer headersMu.Unlock()
			if !headersReady {
				routeTimedOut = true
				cancel()
			}
		},
	)

	req = req.WithContext(routeCtx)
	resp, err := client.Do(req)

	if resp != nil {
		statusCode = resp.StatusCode
	}

	headersMu.Lock()
	headersReady = true
	timer.Stop()
	headersMu.Unlock()
	if err != nil {
		if r.Context().Err() != nil {
			return false, false, r.Context().Err()
		}

		// B-10: transport errors (DNS, connection refused, reset) are
		// transient, not permanent. Previously they fell through as
		// outcome:"failure" — indistinguishable from hard failures in
		// telemetry (AUDIT-008).
		routeOutcome = "transient"
		errorKind = "transport"
		if routeTimedOut {
			routeOutcome = "timeout"
			errorKind = "timeout"
		}
		providerFailed(p, route.Model)
		return false, true, err
	}

	// Keep the request context alive while streaming.
	// It is canceled when tryRoute returns.
	defer cancel()

	// Failure taxonomy (Bifrost-class table + LiteLLM retryable set): body
	// peek for 4xx classification; success never reads the stream.
	var cls FailureClass = FailureSuccess
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		peek, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		cls = classifyFailureBody(resp.StatusCode, string(peek))
		errorKind = cls.String()
	}
	transient := cls.retryable()
	permanent := !transient && cls != FailureSuccess

	if permanent {
		routeOutcome = "permanent"
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		providerHardFailed(p, route.Model, requestID, preferenceName)

		return false, false, fmt.Errorf(
			"provider=%s model=%s returned %s",
			p.Name,
			route.Model,
			resp.Status,
		)
	}

	if transient {
		routeOutcome = "transient"
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()

		// F1: capture Retry-After BEFORE returning — this branch returns for
		// 429s, so the capture must live here, not after it. Accepts int
		// seconds OR HTTP-date (RFC 7231), sanitized to [0,60].
		if resp.StatusCode == http.StatusTooManyRequests {
			if ra := resp.Header.Get("Retry-After"); ra != "" {
				retryAfterSeconds = parseRetryAfter(ra)
			}
		}

		if resp.StatusCode == http.StatusUnauthorized {
			providerKeyFailed(p, keyEnv)
			return false, true, fmt.Errorf(
				"provider=%s key_env=%s returned %s",
				p.Name,
				keyEnv,
				resp.Status,
			)
		}

		providerFailed(p, route.Model)

		return false, true, fmt.Errorf(
			"provider=%s model=%s returned %s",
			p.Name,
			route.Model,
			resp.Status,
		)
	}

	// GAP-003: capture Retry-After when the upstream returns 429.
	// (kept for 1xx/2xx-adjacent paths; the transient branch above handles 429)
	if resp != nil && resp.StatusCode == http.StatusTooManyRequests {
		if ra := resp.Header.Get("Retry-After"); ra != "" {
			retryAfterSeconds = parseRetryAfter(ra)
		}
	}

	// streamResponse performs SSE preflight before downstream commitment.
	routeOutcome = "success"
	err = streamResponse(w, resp, r.Context(), func() {
		providerKeySucceeded(p, keyEnv)
		providerSucceeded(p, route.Model, preferenceName)
		// GAP-006: the response committed to the client — in-flight requests
		// are now observable end-to-end (pending via route_attempt, this
		// event when bytes start flowing downstream).
		logRouteEvent(RouteEvent{
			Event:               "route_committed",
			RequestID:           requestID,
			Provider:            p.Name,
			Model:               route.Model,
			Preference:          preferenceName,
			Outcome:             "committed",
			Status:              resp.StatusCode,
			LatencyMS:           time.Since(started).Milliseconds(),
			KeyEnv:              keyEnv,
			FirstChunkLatencyMS: firstChunkMS,
			FallbackDepth:       fallbackCount,
		})
	}, &firstChunkMS, started, finalAttempt)

	if err != nil {
		if errors.Is(err, errFirstEventTimeout) {
			// Pre-commit abort: nothing was sent downstream — fully retryable.
			routeOutcome = "transient"
			statusCode = http.StatusGatewayTimeout
			errorKind = "ttft_timeout"
			providerFailed(p, route.Model)
			log.Printf(
				"provider=%s model=%s first-event timeout BEFORE commitment — cooldown + failover",
				p.Name,
				route.Model,
			)
			return false, true, err
		}

		if sseErr, ok := err.(*sseApplicationError); ok {
			routeOutcome = "transient"
			if sseErr.Code > 0 {
				statusCode = sseErr.Code
			}
			providerFailed(p, route.Model)

			log.Printf(
				"provider=%s model=%s upstream SSE application failure before commitment: %v",
				p.Name,
				route.Model,
				sseErr,
			)

			return false, true, err
		}

		// B-5: the CLIENT cancelled (user stop / Hermes timeout). This says
		// nothing about the provider — do NOT cool the cell down.
		if errors.Is(err, context.Canceled) && r.Context().Err() != nil {
			routeOutcome = "client_abort"
			statusCode = 200 // stream had committed; client left
			log.Printf(
				"provider=%s model=%s client aborted stream (no provider penalty)",
				p.Name,
				route.Model,
			)
			// F8: return success=false — the request did NOT complete, so
			// the provider must not be pinned as last-known-good. (The
			// client is already gone; nothing else can be served.)
			return false, false, err
		}

		routeOutcome = "stream_error"
		// Post-commitment stream failure: cannot replay downstream, but the
		// cell IS unhealthy — cool it down so the NEXT request skips it
		// instead of stalling again for a full idle window.
		providerFailed(p, route.Model)
		log.Printf(
			"provider=%s model=%s stream ended after commitment: %v (cooldown applied)",
			p.Name,
			route.Model,
			err,
		)
	}

	// Store a non-stream success into the exact-match cache.
	if err == nil && routeOutcome == "success" && cacheRec != nil {
		responseCache.Put(cacheKeyStr, cacheRec.buf.Bytes(), cacheRec.status)
		logRouteEvent(RouteEvent{
			Event:      "cache_store",
			RequestID:  requestID,
			Provider:   p.Name,
			Model:      route.Model,
			Preference: preferenceName,
			Status:     cacheRec.status,
		})
	}

	return true, false, err
}

func proxy(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(
			w,
			"method not allowed",
			http.StatusMethodNotAllowed,
		)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(
			w,
			"failed reading request",
			http.StatusBadRequest,
		)
		return
	}

	// Size limit (item 4): enforce before any routing work.
	cfgMu.RLock()
	maxReqMB := cfg.Size.MaxRequestMB
	cfgMu.RUnlock()
	if maxReqMB > 0 && len(body) > maxReqMB*1024*1024 {
		http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
		return
	}

	// Generate request ID for telemetry correlation
	requestID := fmt.Sprintf("req-%d-%d", time.Now().UnixNano(), os.Getpid()%10000)

	model := requestModel(body)
	preference := preferenceForModel(model)

	if preference == nil {
		http.Error(
			w,
			fmt.Sprintf("model '%s' not configured in router preferences", model),
			http.StatusBadRequest,
		)
		return
	}

	log.Printf(
		"request model=%s preference=%s request_id=%s",
		model,
		preference.Name,
		requestID,
	)

	// B-4: request_started — one event per request, usable as a lifecycle
	// anchor so reconstruction can pair start → result/abort/exhausted.
	logRouteEvent(RouteEvent{
		Event:          "request_started",
		RequestID:      requestID,
		Preference:     preference.Name,
		RequestedModel: model,
	})

	// Keep cycling until a route succeeds or the client cancels.
	if err := r.Context().Err(); err != nil {
		return
	}
	// Preference fallback state must persist across the outer loop iterations
	preferenceFallback := false
	preferenceFrom := ""
	preferenceTo := ""

	// Track the original preference for LKGP pinning on cross-preference fallback success
	originalPreference := preference.Name

	for {
		attempted := false
		fallbackCount := 0
		fallbackFrom := ""
		skippedHardFailed := false

		// Strategy application — any trait mode (lkgp, priority, p2c) via
		// the registry; "configured"/"" honours config order exactly.
		orderedRoutes := preference.Routes
		if strategyRegistry != nil && cfg.Strategy != "" && cfg.Strategy != "configured" {
			if strat, ok := strategyRegistry.Get(cfg.Strategy); ok {
				// Build dummy targets for the strategy
				dummyTargets := make([]strategy.Target, len(preference.Routes))
				for i, rt := range preference.Routes {
					dummyTargets[i] = strategy.Target{Provider: rt.Provider, Model: rt.Model}
				}

				orderedStrategyTargets := strat.Apply(r.Context(), dummyTargets, &strategy.Request{
					Preference: preference.Name,
				})

				// Convert back to Route slice
				orderedRoutes = make([]Route, len(orderedStrategyTargets))
				for i, t := range orderedStrategyTargets {
					orderedRoutes[i] = Route{Provider: t.Provider, Model: t.Model}
				}
			}
		}

		for ri, route := range orderedRoutes {
			p := providerByName(route.Provider)
			if p == nil {
				log.Printf(
					"route provider=%s not configured",
					route.Provider,
				)
				logRouteEvent(RouteEvent{
					Event:      "route_skipped",
					RequestID:  requestID,
					Provider:   route.Provider,
					Model:      route.Model,
					Preference: preference.Name,
					Outcome:    "provider_unconfigured",
				})
				continue
			}

			// Check if the route is hard-failed BEFORE health check
			p.mu.Lock()
			h := p.routes[route.Model]
			if h != nil && h.HardFailed {
				skippedHardFailed = true
				p.mu.Unlock()
				logRouteEvent(RouteEvent{
					Event:      "route_skipped",
					RequestID:  requestID,
					Provider:   p.Name,
					Model:      route.Model,
					Preference: preference.Name,
					Outcome:    "hard_failed",
				})
				continue
			}
			p.mu.Unlock()

			if !providerHealthy(p, route.Model) {
				logRouteEvent(RouteEvent{
					Event:      "route_skipped",
					RequestID:  requestID,
					Provider:   p.Name,
					Model:      route.Model,
					Preference: preference.Name,
					Outcome:    "unhealthy",
				})
				continue
			}

			if !routeAvailable(p, route.Model) {
				log.Printf(
					"skip provider=%s model=%s catalog=absent",
					p.Name,
					route.Model,
				)
				logRouteEvent(RouteEvent{
					Event:      "route_skipped",
					RequestID:  requestID,
					Provider:   p.Name,
					Model:      route.Model,
					Preference: preference.Name,
					Outcome:    "catalog_absent",
				})
				continue
			}

			attempted = true

			finalAttempt := ri == len(orderedRoutes)-1

			for {
				success, retryable, err := tryRoute(
					w,
					r,
					body,
					route,
					p,
					requestID,
					preference.Name,
					fallbackCount,
					fallbackFrom,
					skippedHardFailed,
					preferenceFallback,
					preferenceFrom,
					preferenceTo,
					finalAttempt,
				)

				if r.Context().Err() != nil {
					return
				}

				if success {
					// LKGP: record which provider succeeded for this
					// preference so the next request tries it first.
					if strategyRegistry != nil {
						if lkgpStrat, ok := strategyRegistry.Get("lkgp"); ok {
							if lkgpStrat, ok := lkgpStrat.(*strategy.LKGPStrategy); ok {
								lkgpStrat.SetPin(originalPreference, p.Name)
							}
						}
					}
					return
				}

				// A 401 means the current provider key failed.
				// Retry this same route so selectProviderKey() can choose
				// the next healthy key before provider fallback occurs.
				if retryable && err != nil && strings.Contains(err.Error(), "key_env=") {
					log.Printf(
						"key fallback provider=%s model=%s error=%v",
						p.Name,
						route.Model,
						err,
					)
					continue
				}

				if !retryable {
					log.Printf(
						"route permanently unavailable provider=%s model=%s error=%v",
						p.Name,
						route.Model,
						err,
					)
				} else {
					log.Printf(
						"route failed provider=%s model=%s error=%v",
						p.Name,
						route.Model,
						err,
					)
				}

				fallbackCount++
				fallbackFrom = p.Name
				// S9: scoped pin clear — only drop the LKGP pin when the
				// FAILED target IS the pinned provider; a sibling failure
				// must not discard a healthy pin (OmniRoute staleLkgpClear).
				if strategyRegistry != nil && cfg.Strategy == "lkgp" {
					if lkgpStrat, ok := strategyRegistry.Get("lkgp"); ok {
						if l, ok := lkgpStrat.(*strategy.LKGPStrategy); ok {
							if l.GetPin(originalPreference) == p.Name {
								l.DeletePin(originalPreference)
								log.Printf("lkgp pin cleared preference=%s provider=%s (failed)", originalPreference, p.Name)
							}
						}
					}
				}
				break
			}
		}

		if attempted {
			// Every route for this preference failed.
			// Move to the next preferred model.
			next := nextPreference(preference)

			if next != nil {
				current := preference.Name
				preference = next
				log.Printf(
					"preference fallback %s -> %s request_id=%s",
					current,
					next.Name,
					requestID,
				)
				// Emit preference fallback telemetry
				logRouteEvent(RouteEvent{
					Event:          "preference_fallback",
					RequestID:      requestID,
					Provider:       "",
					Model:          "",
					Preference:     "",
					Outcome:        "fallback",
					FromPreference: current,
					ToPreference:   next.Name,
					Context:        "request",
				})
				// Record metrics
				preferenceFallbacks.WithLabelValues(current, next.Name).Inc()
				// Pass preference fallback info to next tryRoute call
				preferenceFallback = true
				preferenceFrom = current
				preferenceTo = next.Name
				continue
			}
		}

		// All configured preferences have been exhausted.
		// B-4: emit the exhaustion event (AUDIT-012) — previously the final
		// outcome of an exhausted request was invisible in telemetry.
		logRouteEvent(RouteEvent{
			Event:      "request_exhausted",
			RequestID:  requestID,
			Preference: preference.Name,
			Model:      model,
			Outcome:    "exhausted",
		})

		// C (hold-and-serve): park and wait for a cooldown to expire,
		// then re-walk every preference from the top. Only emit the
		// legacy 503 if the hold window itself is exceeded.
		if holdAndServe(w, r) {
			// Restart the walk at the first preference that can actually be
			// attempted now. Blindly using firstPreference() can land on a
			// preference whose models no provider serves (catalog=absent),
			// which skips every route, never advances the chain, and spins
			// the hold at 1Hz until the client times out.
			next := nextAttemptablePreference()
			if next == nil {
				log.Printf("no attemptable preference after hold; emitting 503")
				waitForRecovery(w, r)
				return
			}
			preference = next
			continue
		}
		return
	}
}

// nextAttemptablePreference returns the first preference (in configured order)
// that has at least one route that could be tried right now: provider exists,
// model is not hard-failed, model is present in the provider catalog, and the
// route is not currently cooling. Returns nil when nothing is attemptable.
func nextAttemptablePreference() *Preference {
	cfgMu.RLock()
	prefs := make([]Preference, len(cfg.Preferences))
	copy(prefs, cfg.Preferences)
	providers := append([]*Provider(nil), cfg.Providers...)
	cfgMu.RUnlock()

	byName := make(map[string]*Provider, len(providers))
	for _, p := range providers {
		byName[p.Name] = p
	}

	for i := range prefs {
		for _, route := range prefs[i].Routes {
			p := byName[route.Provider]
			if p == nil {
				continue
			}
			p.mu.Lock()
			h := p.routes[route.Model]
			hardFailed := h != nil && h.HardFailed
			cooling := h != nil && !h.CooldownTil.IsZero() && time.Until(h.CooldownTil) > 0
			inCatalog := len(p.catalog) == 0 || p.catalog[route.Model]
			p.mu.Unlock()

			if hardFailed || cooling || !inCatalog {
				continue
			}
			return &prefs[i]
		}
	}
	return nil
}

func firstPreference() *Preference {
	cfgMu.RLock()
	defer cfgMu.RUnlock()

	if len(cfg.Preferences) == 0 {
		return nil
	}

	return &cfg.Preferences[0]
}

func nextPreference(current *Preference) *Preference {
	cfgMu.RLock()
	defer cfgMu.RUnlock()

	for i := range cfg.Preferences {
		if cfg.Preferences[i].Name == current.Name {
			if i+1 < len(cfg.Preferences) {
				return &cfg.Preferences[i+1]
			}
			return nil
		}
	}

	return nil
}

func health(w http.ResponseWriter, r *http.Request) {
	cfgMu.RLock()
	providers := append([]*Provider(nil), cfg.Providers...)
	cfgMu.RUnlock()

	result := make([]Status, 0, len(providers))

	for _, p := range providers {
		p.mu.Lock()

		healthy := true
		failures := 0
		hardFailed := false
		var earliest time.Time
		cells := make([]CellStatus, 0, len(p.routes))
		now := time.Now()

		for model, h := range p.routes {
			if h == nil {
				continue
			}

			var cell CellStatus
			cell.Model = model
			cell.Failures = h.Failures
			cell.HardFailed = h.HardFailed
			cell.Healthy = !h.HardFailed &&
				(h.CooldownTil.IsZero() || now.After(h.CooldownTil))
			if !h.CooldownTil.IsZero() {
				cell.CooldownUntil = h.CooldownTil.Format(time.RFC3339)
			}
			if !h.WarmAt.IsZero() && now.Sub(h.WarmAt) < 10*time.Minute {
				cell.Warm = true
			}
			cells = append(cells, cell)

			failures += h.Failures
			if h.HardFailed {
				hardFailed = true
				healthy = false
				continue
			}
			if !h.CooldownTil.IsZero() && now.Before(h.CooldownTil) {
				healthy = false
				if earliest.IsZero() || h.CooldownTil.Before(earliest) {
					earliest = h.CooldownTil
				}
			}
		}

		s := Status{
			Name:       p.Name,
			Healthy:    healthy,
			Failures:   failures,
			HardFailed: hardFailed,
			Catalog:    len(p.catalog),
			Routes:     cells,
		}

		if !earliest.IsZero() {
			s.Cooldown = earliest.Format(time.RFC3339)
		}

		p.mu.Unlock()
		result = append(result, s)
	}

	writeJSON(w, http.StatusOK, result)
}

func models(w http.ResponseWriter, r *http.Request) {
	type Model struct {
		ID      string `json:"id"`
		Object  string `json:"object"`
		OwnedBy string `json:"owned_by"`
	}

	cfgMu.RLock()
	defer cfgMu.RUnlock()

	seen := map[string]bool{}
	data := []Model{}

	for _, preference := range cfg.Preferences {
		for _, route := range preference.Routes {
			if seen[route.Model] {
				continue
			}

			seen[route.Model] = true

			data = append(data, Model{
				ID:      route.Model,
				Object:  "model",
				OwnedBy: "craftedworkflows-router",
			})
		}
	}

	w.Header().Set("Content-Type", "application/json")

	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"data":   data,
	})
}

var eventFileHandle *os.File
var eventMu sync.Mutex

type RouteEvent struct {
	Timestamp  string `json:"timestamp"`
	Event      string `json:"event"`
	RequestID  string `json:"request_id,omitempty"`
	Provider   string `json:"provider,omitempty"`
	Model      string `json:"model,omitempty"`
	Preference string `json:"preference,omitempty"`
	// RequestedModel: the model the client originally asked for (B-4).
	// Model is the model actually served after substitution.
	RequestedModel string `json:"requested_model,omitempty"`
	// ErrorKind classifies transport vs upstream vs policy failures (B-10).
	ErrorKind string `json:"error_kind,omitempty"`
	// BuildID: identifies which router build wrote this event (B-12) so
	// telemetry from different binaries is never mixed up.
	BuildID string `json:"build_id,omitempty"`
	// FirstChunkLatencyMS: time from request start to first downstream SSE
	// chunk (time-to-first-byte). GAP-001.
	FirstChunkLatencyMS int64 `json:"first_chunk_latency_ms,omitempty"`
	// FallbackDepth: how many provider/preference fallbacks occurred before
	// this result (0 = first route served). GAP-002.
	FallbackDepth int `json:"fallback_depth,omitempty"`
	// FallbackChain: ordered list of providers attempted for this request.
	FallbackChain []string `json:"fallback_chain,omitempty"`
	// RetryAfterSeconds: Retry-After header value captured from a 429.
	RetryAfterSeconds int `json:"retry_after_seconds,omitempty"`
	// KeyEnv already exists; KeyID distinguishes which key served.
	KeyID           string `json:"key_id,omitempty"`
	Outcome         string `json:"outcome,omitempty"`
	Status          int    `json:"status,omitempty"`
	LatencyMS       int64  `json:"latency_ms,omitempty"`
	Error           string `json:"error,omitempty"`
	KeyEnv          string `json:"key_env,omitempty"`
	CooldownSeconds int    `json:"cooldown_seconds,omitempty"`
	CooldownUntil   string `json:"cooldown_until,omitempty"`
	FromPreference  string `json:"from_preference,omitempty"`
	ToPreference    string `json:"to_preference,omitempty"`
	Context         string `json:"context,omitempty"` // "request" or "background"
}

// buildID is stamped at startup (B-12) so consumers can tell which binary
// produced an event. Override via -ldflags "-X main.buildID=...".
var buildID = "dev"

func logRouteEvent(e RouteEvent) {
	eventMu.Lock()
	defer eventMu.Unlock()

	if eventFileHandle == nil {
		return
	}

	e.Timestamp = time.Now().UTC().Format(time.RFC3339Nano)
	e.BuildID = buildID
	_ = json.NewEncoder(eventFileHandle).Encode(e)
	_ = eventFileHandle.Sync()
}

func setupLogging() {
	eventFile, eventErr := os.OpenFile("router-events.jsonl", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if eventErr != nil {
		log.Fatal(eventErr)
	}
	eventFileHandle = eventFile
	f, err := os.OpenFile("router.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		log.Fatal(err)
	}
	// B-11: write ONLY to the log file. The old io.MultiWriter(os.Stderr, f)
	// plus startr's `nohup ./router >> router.log 2>&1` doubled every line —
	// stderr was captured by both the MultiWriter AND the shell redirect.
	// Drop the stderr branch; the file is the single sink.
	log.SetOutput(f)
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}

func loadConfig() {
	// Harness support (Phase C, T-series): allow running the PRODUCTION
	// binary against an isolated config/port. Default remains config.json
	// + 8643. CONFIG_FILE was fork-only before — without it the prod binary
	// could not be exercised on :8644, so every test validated the fork
	// instead of what is deployed (AUDIT-011).
	configPath := os.Getenv("CONFIG_FILE")
	if configPath == "" {
		configPath = "config.json"
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		log.Fatal(err)
	}

	if err := json.Unmarshal(data, &cfg); err != nil {
		log.Fatal(err)
	}

	if cfg.Port == 0 {
		cfg.Port = 8643
	}
	if p := os.Getenv("ROUTER_PORT"); p != "" {
		if port, perr := strconv.Atoi(p); perr == nil && port > 0 {
			cfg.Port = port
		}
	}

	// Validate configuration
	if err := validateConfig(); err != nil {
		log.Fatalf("config validation failed: %v", err)
	}

	log.Printf(
		"loaded %d preferences, %d providers",
		len(cfg.Preferences),
		len(cfg.Providers),
	)
}

func validateConfig() error {
	if len(cfg.Preferences) == 0 {
		return fmt.Errorf("at least one preference required")
	}

	for i, pref := range cfg.Preferences {
		if pref.Name == "" {
			return fmt.Errorf("preference[%d]: name required", i)
		}
		if len(pref.Routes) == 0 {
			return fmt.Errorf("preference[%s]: at least one route required", pref.Name)
		}
		for j, route := range pref.Routes {
			if route.Provider == "" {
				return fmt.Errorf("preference[%s] route[%d]: provider required", pref.Name, j)
			}
			if route.Model == "" {
				return fmt.Errorf("preference[%s] route[%d]: model required", pref.Name, j)
			}
			// Verify provider exists
			found := false
			for _, p := range cfg.Providers {
				if p.Name == route.Provider {
					found = true
					break
				}
			}
			if !found {
				return fmt.Errorf("preference[%s] route[%d]: provider '%s' not configured", pref.Name, j, route.Provider)
			}
		}
	}

	if len(cfg.Providers) == 0 {
		return fmt.Errorf("at least one provider required")
	}

	for i, p := range cfg.Providers {
		if p.Name == "" {
			return fmt.Errorf("provider[%d]: name required", i)
		}
		if p.BaseURL == "" {
			return fmt.Errorf("provider[%s]: base_url required", p.Name)
		}
		// Validate base_url is parseable
		if _, err := url.Parse(p.BaseURL); err != nil {
			return fmt.Errorf("provider[%s]: invalid base_url: %v", p.Name, err)
		}
		// F3: duplicate provider names break providerByName lookup silently.
		for j := 0; j < i; j++ {
			if cfg.Providers[j].Name == p.Name {
				return fmt.Errorf("provider[%d]: duplicate provider name '%s'", i, p.Name)
			}
		}
		// F3: key_envs must be non-empty — a provider with no keys can never
		// serve and would exhaust the chain with 'no usable API key'.
		envs := providerKeyEnvs(p)
		if len(envs) == 0 {
			return fmt.Errorf("provider[%s]: no key_env/key_envs configured", p.Name)
		}
	}

	// F3: duplicate model across a preference's routes would make the chain
	// attempt the same model twice on the same provider.
	for pi, pref := range cfg.Preferences {
		seenModel := map[string]bool{}
		for _, route := range pref.Routes {
			if seenModel[route.Provider+"/"+route.Model] {
				return fmt.Errorf("preference[%s]: duplicate route %s/%s",
					pref.Name, route.Provider, route.Model)
			}
			seenModel[route.Provider+"/"+route.Model] = true
		}
		_ = pi
	}

	// F3/A10: strategy must be a known trait (""/configured, lkgp, priority,
	// p2c). Unknown strategies fail config load — never silently default.
	if !strategy.ValidStrategy(cfg.Strategy) {
		return fmt.Errorf("strategy '%s' unknown (allowed: '', 'configured', 'lkgp', 'priority', 'p2c')", cfg.Strategy)
	}
	// Cache config sanity: TTL must be positive when enabled, size bounded.
	if cfg.Cache.Enabled {
		if cfg.Cache.TTLSeconds <= 0 {
			cfg.Cache.TTLSeconds = 60
		}
		if cfg.Cache.MaxSize <= 0 {
			cfg.Cache.MaxSize = 512
		}
	}
	if cfg.Budget.Enabled {
		if cfg.Budget.WindowSeconds <= 0 {
			cfg.Budget.WindowSeconds = 60
		}
		if cfg.Budget.RequestsPerMinute < 0 {
			cfg.Budget.RequestsPerMinute = 0
		}
	}
	if cfg.Size.MaxRequestMB < 0 {
		cfg.Size.MaxRequestMB = 0
	}

	// Validate retry config
	if cfg.Retry.RouteTimeoutSeconds < 1 {
		return fmt.Errorf("retry.route_timeout_seconds must be >= 1")
	}
	if cfg.Retry.CooldownSeconds < 1 {
		return fmt.Errorf("retry.cooldown_seconds must be >= 1")
	}
	if cfg.Retry.MaxCooldownSeconds > 0 && cfg.Retry.MaxCooldownSeconds < cfg.Retry.CooldownSeconds {
		return fmt.Errorf("retry.max_cooldown_seconds must be >= cooldown_seconds")
	}
	if cfg.Retry.AllFailedWait < 1 {
		return fmt.Errorf("retry.all_failed_wait_seconds must be >= 1")
	}
	if cfg.Retry.FailureLimit < 1 {
		return fmt.Errorf("retry.failure_limit must be >= 1")
	}

	// Validate catalog refresh
	if cfg.CatalogRefreshSeconds < 60 {
		return fmt.Errorf("catalog_refresh_seconds must be >= 60")
	}

	return nil
}

var (
	requestsTotal       = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "router_requests_total", Help: "Total requests"}, []string{"provider", "model", "outcome"})
	requestDuration     = prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "router_request_duration_seconds", Help: "Request latency", Buckets: []float64{.01, .05, .1, .25, .5, 1, 2.5, 5, 10}}, []string{"provider", "model"})
	routesHealthy       = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_routes_healthy", Help: "Healthy routes per provider/model"}, []string{"provider", "model"})
	routesHardFailed    = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_routes_hard_failed", Help: "Hard-failed routes per provider/model"}, []string{"provider", "model"})
	routesInCooldown    = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_routes_in_cooldown", Help: "Routes in cooldown per provider/model"}, []string{"provider", "model"})
	keysHealthy         = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_keys_healthy", Help: "Healthy keys per provider"}, []string{"provider"})
	keysInCooldown      = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_keys_in_cooldown", Help: "Keys in cooldown per provider"}, []string{"provider"})
	keysHardFailed      = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_keys_hard_failed", Help: "Hard-failed keys per provider"}, []string{"provider"})
	catalogModels       = prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "router_catalog_models", Help: "Models in catalog per provider"}, []string{"provider"})
	preferenceFallbacks = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "router_preference_fallbacks_total", Help: "Preference fallbacks"}, []string{"from", "to"})
)

func initMetrics() {
	prometheus.MustRegister(requestsTotal, requestDuration, routesHealthy, routesHardFailed, routesInCooldown, keysHealthy, keysInCooldown, keysHardFailed, catalogModels, preferenceFallbacks)
}

func updateMetrics() {
	cfgMu.RLock()
	providers := append([]*Provider(nil), cfg.Providers...)
	cfgMu.RUnlock()

	for _, p := range providers {
		p.mu.Lock()
		for model, h := range p.routes {
			if h == nil {
				continue
			}
			// F5 (route level): three-way set so gauges never go stale.
			if h.HardFailed {
				routesHardFailed.WithLabelValues(p.Name, model).Set(1)
				routesInCooldown.WithLabelValues(p.Name, model).Set(0)
				routesHealthy.WithLabelValues(p.Name, model).Set(0)
			} else if !h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil) {
				routesHardFailed.WithLabelValues(p.Name, model).Set(0)
				routesInCooldown.WithLabelValues(p.Name, model).Set(1)
				routesHealthy.WithLabelValues(p.Name, model).Set(0)
			} else {
				routesHardFailed.WithLabelValues(p.Name, model).Set(0)
				routesInCooldown.WithLabelValues(p.Name, model).Set(0)
				routesHealthy.WithLabelValues(p.Name, model).Set(1)
			}
		}
		// F5: gauges report COUNTS (not last-write): each key contributes 1
		// to exactly one state gauge. Pre-rewrite, the loop Set(1)/Set(0)
		// per key, so only the LAST key's state survived — misleading for
		// multi-key providers.
		healthyKeys, cdKeys, hfKeys := 0, 0, 0
		for _, kh := range p.keys {
			if kh == nil {
				continue
			}
			if kh.HardFailed {
				hfKeys++
			} else if !kh.CooldownTil.IsZero() && time.Now().Before(kh.CooldownTil) {
				cdKeys++
			} else {
				healthyKeys++
			}
		}
		keysHealthy.WithLabelValues(p.Name).Set(float64(healthyKeys))
		keysInCooldown.WithLabelValues(p.Name).Set(float64(cdKeys))
		keysHardFailed.WithLabelValues(p.Name).Set(float64(hfKeys))
		catalogModels.WithLabelValues(p.Name).Set(float64(len(p.catalog)))
		p.mu.Unlock()
	}
}

func metricsHandler(w http.ResponseWriter, r *http.Request) {
	updateMetrics()
	promhttp.Handler().ServeHTTP(w, r)
}

func main() {
	initMetrics()
	setupLogging()
	loadConfig()

	// Initialize strategy registry
	strategyRegistry = strategy.NewRegistry()

	// Exact-match cache init (config-gated).
	cfgMu.RLock()
	cacheCfg := cfg.Cache
	cfgMu.RUnlock()
	if cacheCfg.Enabled {
		responseCache = NewExactMatchCache(cacheCfg.MaxSize,
			time.Duration(cacheCfg.TTLSeconds)*time.Second)
		log.Printf("exact-match cache enabled size=%d ttl=%ds",
			cacheCfg.MaxSize, cacheCfg.TTLSeconds)
	}

	// Register LKGP strategy after config is loaded
	strategyRegistry.Register("lkgp", strategy.NewLKGPStrategy(strategy.DefaultLKGPConfig()))

	go catalogLoop()
	go hardFailureRecoveryLoop()

	mux := http.NewServeMux()

	mux.HandleFunc("/health", health)
	mux.HandleFunc("/v1/models", models)
	mux.HandleFunc("/v1/chat/completions", proxy)
	mux.HandleFunc("/metrics", metricsHandler)

	addr := fmt.Sprintf(
		"%s:%d",
		cfg.Host,
		cfg.Port,
	)

	server := &http.Server{
		Addr:    addr,
		Handler: mux,
	}

	log.Printf("router listening on http://%s", addr)

	if err := server.ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
