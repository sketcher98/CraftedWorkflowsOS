package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"errors"
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
	Keys        map[string]*KeyHealth
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
	FailureLimit          int `json:"failure_limit"`
	CooldownSeconds       int `json:"cooldown_seconds"`
	MaxCooldownSeconds    int `json:"max_cooldown_seconds"`
	AllFailedWait         int `json:"all_failed_wait_seconds"`
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
	Host                  string       `json:"host"`
	Port                  int          `json:"port"`
	CatalogRefreshSeconds int          `json:"catalog_refresh_seconds"`
	Retry                 RetryConfig  `json:"retry"`
	Preferences           []Preference `json:"preferences"`
	Providers             []*Provider  `json:"providers"`
}

var (
	cfg   Config
	cfgMu sync.RWMutex
)

// StrategyRegistry holds all routing strategies
var strategyRegistry *strategy.Registry

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

	return !h.HardFailed && time.Now().After(h.CooldownTil)
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
	extraFailures := h.Failures - limit
	for i := 0; i < extraFailures; i++ {
		cooldown *= 2
		if cfg.Retry.MaxCooldownSeconds > 0 &&
			cooldown >= cfg.Retry.MaxCooldownSeconds {
			cooldown = cfg.Retry.MaxCooldownSeconds
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
		Event:            "route_cooldown",
		Provider:         p.Name,
		Model:            model,
		Outcome:          "cooldown",
		CooldownSeconds:  cooldown,
		CooldownUntil:    h.CooldownTil.Format(time.RFC3339),
	})
}

func providerSucceeded(p *Provider, model string, preferenceName string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := routeHealthLocked(p, model)
	wasUnhealthy := h.Failures > 0 || !h.CooldownTil.IsZero() || h.HardFailed

	h.Failures = 0
	h.CooldownTil = time.Time{}
	h.HardFailed = false

	if wasUnhealthy {
		logRouteEvent(RouteEvent{
			Event:      "route_recovered",
			Provider:   p.Name,
			Model:      model,
			Outcome:    "recovered",
			Preference: preferenceName,
		})
	}
}

func providerKeyQuarantined(p *Provider, env string) {
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

	h.HardFailed = true
	h.CooldownTil = time.Time{}
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
}

func selectProviderKey(p *Provider) (string, string, bool) {
	now := time.Now()

	p.mu.Lock()
	defer p.mu.Unlock()

	if p.keys == nil {
		p.keys = make(map[string]*KeyHealth)
	}

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

		return env, key, true
	}

	return "", "", false
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
			p.mu.Lock()
			for model, h := range p.routes {
				if h == nil || !h.HardFailed {
					continue
				}

				// Try to recover
				if tryRecoverHardFailed(p, model) {
					h.HardFailed = false
					h.Failures = 0
					h.CooldownTil = time.Time{}

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
				}
			}
			p.mu.Unlock()
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
			continue
		}

		status := resp.StatusCode

		if status == http.StatusUnauthorized {
			resp.Body.Close()
			providerKeyFailed(p, keyEnv)
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
		"error":             "router_exhausted",
		"retry_after_secs":  strconv.Itoa(retryAfterSec),
	})

	// Recovery continues in hardFailureRecoveryLoop() background - no sleep here
}

func classifyFailure(status int, err error) (transient bool, permanent bool) {
	if err != nil {
		return true, false
	}

	switch {
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
	case status == http.StatusForbidden:
		return false, true
	default:
		return false, false
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
		for _, value := range values {
			if strings.EqualFold(key, "Host") {
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
	firstEventTimer := time.NewTimer(firstEventTimeout)
	defer firstEventTimer.Stop()

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

		case <-firstEventTimer.C:
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
			if !firstEventTimer.Stop() {
				select {
				case <-firstEventTimer.C:
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
) (success bool, retryable bool, err error) {
	started := time.Now()
	statusCode := 0
	routeOutcome := "failure"

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

	keyEnv, key, ok := selectProviderKey(p)
	if !ok {
		return false, false, fmt.Errorf("no usable API key for provider %s", p.Name)
	}

	defer func() {
		outcome := routeOutcome
		e := RouteEvent{
			Event:            "route_result",
			RequestID:        requestID,
			Provider:         p.Name,
			Model:            route.Model,
			Preference:       preferenceName,
			Outcome:          outcome,
			LatencyMS:        time.Since(started).Milliseconds(),
			Status:           statusCode,
			KeyEnv:           keyEnv,
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

		providerFailed(p, route.Model)
		if routeTimedOut {
			routeOutcome = "timeout"
		}
		return false, true, err
	}

	// Keep the request context alive while streaming.
	// It is canceled when tryRoute returns.
	defer cancel()

	transient, permanent := classifyFailure(resp.StatusCode, nil)

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

	// streamResponse performs SSE preflight before downstream commitment.
	routeOutcome = "success"
	err = streamResponse(w, resp, r.Context(), func() {
		providerKeySucceeded(p, keyEnv)
		providerSucceeded(p, route.Model, preferenceName)
	})

	if err != nil {
		if errors.Is(err, errFirstEventTimeout) {
			// Pre-commit abort: nothing was sent downstream — fully retryable.
			routeOutcome = "transient"
			statusCode = http.StatusGatewayTimeout
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

		// LKGP Strategy: try pinned provider first — recomputed PER PREFERENCE so a
		// preference fallback re-orders the NEXT preference's routes, not the old ones.
		orderedRoutes := preference.Routes
		if strategyRegistry != nil {
			if lkgpStrat, ok := strategyRegistry.Get("lkgp"); ok {
				if lkgpStrat, ok := lkgpStrat.(*strategy.LKGPStrategy); ok {
					// Build dummy targets for the strategy
					dummyTargets := make([]strategy.Target, len(preference.Routes))
					for i, rt := range preference.Routes {
						dummyTargets[i] = strategy.Target{Provider: rt.Provider, Model: rt.Model}
					}

					orderedStrategyTargets := lkgpStrat.Apply(r.Context(), dummyTargets, &strategy.Request{
						Preference: preference.Name,
					})

					// Convert back to Route slice
					orderedRoutes = make([]Route, len(orderedStrategyTargets))
					for i, t := range orderedStrategyTargets {
						orderedRoutes[i] = Route{Provider: t.Provider, Model: t.Model}
					}
				}
			}
		}

		for _, route := range orderedRoutes {
			p := providerByName(route.Provider)
			if p == nil {
				log.Printf(
					"route provider=%s not configured",
					route.Provider,
				)
				continue
			}

			// Check if the route is hard-failed BEFORE health check
			p.mu.Lock()
			h := p.routes[route.Model]
			if h != nil && h.HardFailed {
				skippedHardFailed = true
				p.mu.Unlock()
				continue
			}
			p.mu.Unlock()

			if !providerHealthy(p, route.Model) {
				continue
			}

			if !routeAvailable(p, route.Model) {
				log.Printf(
					"skip provider=%s model=%s catalog=absent",
					p.Name,
					route.Model,
				)
				continue
			}

			attempted = true

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
								Event:           "preference_fallback",
								RequestID:       requestID,
								Provider:        "",
								Model:           "",
								Preference:      "",
								Outcome:         "fallback",
								FromPreference:  current,
								ToPreference:    next.Name,
								Context:         "request",
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
		// C (hold-and-serve): park and wait for a cooldown to expire,
		// then re-walk every preference from the top. Only emit the
		// legacy 503 if the hold window itself is exceeded.
		if holdAndServe(w, r) {
			preference = firstPreference()
			continue
		}
		return
	}
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

		for _, h := range p.routes {
			if h == nil {
				continue
			}

			failures += h.Failures

			if h.HardFailed {
				hardFailed = true
				healthy = false
			}

			if !h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil) {
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
	Outcome    string `json:"outcome,omitempty"`
	Status     int    `json:"status,omitempty"`
	LatencyMS  int64  `json:"latency_ms,omitempty"`
	Error      string `json:"error,omitempty"`
	KeyEnv     string `json:"key_env,omitempty"`
	CooldownSeconds int    `json:"cooldown_seconds,omitempty"`
	CooldownUntil   string `json:"cooldown_until,omitempty"`
	FromPreference  string `json:"from_preference,omitempty"`
	ToPreference    string `json:"to_preference,omitempty"`
	Context         string `json:"context,omitempty"` // "request" or "background"
}

func logRouteEvent(e RouteEvent) {
	eventMu.Lock()
	defer eventMu.Unlock()

	if eventFileHandle == nil {
		return
	}

	e.Timestamp = time.Now().UTC().Format(time.RFC3339Nano)
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
	log.SetOutput(io.MultiWriter(os.Stderr, f))
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}

func loadConfig() {
	data, err := os.ReadFile("config.json")
	if err != nil {
		log.Fatal(err)
	}

	if err := json.Unmarshal(data, &cfg); err != nil {
		log.Fatal(err)
	}

	if cfg.Port == 0 {
		cfg.Port = 8643
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
			if h.HardFailed {
				routesHardFailed.WithLabelValues(p.Name, model).Set(1)
			} else {
				routesHardFailed.WithLabelValues(p.Name, model).Set(0)
			}
			if !h.CooldownTil.IsZero() && time.Now().Before(h.CooldownTil) {
				routesInCooldown.WithLabelValues(p.Name, model).Set(1)
			} else {
				routesInCooldown.WithLabelValues(p.Name, model).Set(0)
				routesHealthy.WithLabelValues(p.Name, model).Set(1)
			}
		}
		for _, kh := range p.keys {
			if kh == nil {
				continue
			}
			if kh.HardFailed {
				keysHardFailed.WithLabelValues(p.Name).Inc()
			} else if !kh.CooldownTil.IsZero() && time.Now().Before(kh.CooldownTil) {
				keysInCooldown.WithLabelValues(p.Name).Inc()
			} else {
				keysHealthy.WithLabelValues(p.Name).Inc()
			}
		}
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
