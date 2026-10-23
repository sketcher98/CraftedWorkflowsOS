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
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
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

type KeyHealth struct {
	Failures    int
	CooldownTil time.Time
	HardFailed  bool
}

type RouteHealth struct {
	Failures    int
	CooldownTil time.Time
	HardFailed  bool
	Keys        map[string]*KeyHealth
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

func providerKeys(p *Provider) []string {
	var keys []string
	for _, env := range providerKeyEnvs(p) {
		if key := os.Getenv(env); key != "" {
			keys = append(keys, key)
		}
	}
	return keys
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
	HardFailedWaitSeconds int `json:"hard_failed_wait_seconds"`
	RouteTimeoutSeconds   int `json:"route_timeout_seconds"`
	// Fail-fast stream controls
	StreamIdleTimeoutSeconds int `json:"stream_idle_timeout_seconds"`
	FirstEventTimeoutSeconds int `json:"first_event_timeout_seconds"`
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
		Event:           "route_cooldown",
		Provider:        p.Name,
		Model:           model,
		Outcome:         "cooldown",
		CooldownSeconds: cooldown,
		CooldownUntil:   h.CooldownTil.Format(time.RFC3339),
		Context:         "request",
	})
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

func providerSucceeded(p *Provider, model string) {
	p.mu.Lock()
	defer p.mu.Unlock()

	h := routeHealthLocked(p, model)
	wasUnhealthy := h.Failures > 0 || !h.CooldownTil.IsZero() || h.HardFailed

	h.Failures = 0
	h.CooldownTil = time.Time{}
	h.HardFailed = false

	if wasUnhealthy {
		logRouteEvent(RouteEvent{
			Event:    "route_recovered",
			Provider: p.Name,
			Model:    model,
			Outcome:  "recovered",
			Context:  "background", // recovery is always background-initiated
		})
	}
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
		Event:      "route_quarantined",
		RequestID:  requestID,
		Provider:   p.Name,
		Model:      model,
		Preference: preferenceName,
		Outcome:    "permanent",
		Context:    "request",
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
			var hardFailedModels []string

			p.mu.Lock()
			for model, h := range p.routes {
				if h != nil && h.HardFailed {
					hardFailedModels = append(hardFailedModels, model)
				}
			}
			p.mu.Unlock()

			for _, model := range hardFailedModels {
				if !tryRecoverHardFailed(p, model) {
					continue
				}

				p.mu.Lock()
				h := p.routes[model]
				if h != nil && h.HardFailed {
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
				})
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

// errFirstEventTimeout is returned by streamResponse when no upstream byte
// arrived before the first-event deadline (pre-commit). It is retryable:
// tryRoute marks the provider failed and the proxy loop fails over.
var errFirstEventTimeout = errors.New("first event timeout: upstream sent no data before deadline")

func streamResponse(
	w http.ResponseWriter,
	resp *http.Response,
) error {
	requestID := w.Header().Get("X-Request-ID")
	log.Printf("streamResponse: ENTERED for request %s", requestID)
	defer resp.Body.Close()

	// Capture routing headers that were set before upstream call
	// We need to preserve them BEFORE copying upstream headers
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

	// Restore our routing headers (they take precedence over upstream)
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

	// Fail-fast stream controls (config-driven, with safe defaults)
	cfgMu.RLock()
	idleTimeout := time.Duration(cfg.Retry.StreamIdleTimeoutSeconds) * time.Second
	firstEventTimeout := time.Duration(cfg.Retry.FirstEventTimeoutSeconds) * time.Second
	cfgMu.RUnlock()
	if idleTimeout <= 0 {
		idleTimeout = 60 * time.Second
	}
	if firstEventTimeout <= 0 {
		firstEventTimeout = 45 * time.Second
	}

	buf := make([]byte, 32*1024)

	// DEFERRED COMMIT: headers are NOT written to the client until the first
	// upstream byte arrives. This keeps the response retryable during the
	// most common stall window (headers sent, no data yet).
	committed := false
	commit := func() {
		if committed {
			return
		}
		w.WriteHeader(resp.StatusCode)
		committed = true
	}

	// Terminal downstream error event, sent after commitment when the
	// stream dies mid-flight so the client gets an explicit signal
	// instead of a silent truncation.
	writeStreamError := func(reason string) {
		if !committed {
			return
		}
		errEvent := fmt.Sprintf(
			"data: {\"error\":{\"message\":\"upstream stream stalled: %s\",\"type\":\"stream_error\",\"code\":\"stream_stalled\"}}\n\ndata: [DONE]\n\n",
			reason,
		)
		if _, writeErr := w.Write([]byte(errEvent)); writeErr != nil {
			log.Printf("streamResponse: failed to write terminal error event for request %s: %v", requestID, writeErr)
			return
		}
		if canFlush {
			flusher.Flush()
		}
		log.Printf("streamResponse: wrote terminal stream_error event for request %s (reason=%s)", requestID, reason)
	}

	// Create a context with timeout that resets on each successful read
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Channel for read results
	type readResult struct {
		n   int
		err error
	}
	readCh := make(chan readResult, 1)

	// Start reading in a goroutine
	go func() {
		log.Printf("streamResponse: reader goroutine started for request %s", requestID)
		for {
			n, err := resp.Body.Read(buf)
			log.Printf("streamResponse: reader goroutine got n=%d err=%v for request %s", n, err, requestID)
			readCh <- readResult{n: n, err: err}
			if err != nil {
				return
			}
		}
	}()

	firstEventTimer := time.NewTimer(firstEventTimeout)
	defer firstEventTimer.Stop()

	idleTimer := time.NewTimer(idleTimeout)
	defer idleTimer.Stop()

	log.Printf(
		"streamResponse: fail-fast timers armed for request %s, first_event=%s idle=%s",
		requestID, firstEventTimeout, idleTimeout,
	)

	for {
		select {
		case result := <-readCh:
			if !idleTimer.Stop() {
				select {
				case <-idleTimer.C:
				default:
				}
			}
			if !firstEventTimer.Stop() {
				select {
				case <-firstEventTimer.C:
				default:
				}
			}
			log.Printf("streamResponse: read result n=%d err=%v for request %s", result.n, result.err, requestID)
			n, err := result.n, result.err

			if n > 0 {
				// First byte: commit downstream NOW (headers + first data)
				commit()
				if _, writeErr := w.Write(buf[:n]); writeErr != nil {
					return writeErr
				}

				if canFlush {
					flusher.Flush()
				}
			}

			if err == io.EOF {
				log.Printf("streamResponse: EOF for request %s", requestID)
				return nil
			}

			if err != nil {
				log.Printf("streamResponse: read error for request %s: %v", requestID, err)
				writeStreamError(fmt.Sprintf("read error: %v", err))
				return err
			}

			// Successful read: re-arm the post-commit idle timer only
			idleTimer = time.NewTimer(idleTimeout)
			continue

		case <-firstEventTimer.C:
			if !committed {
				log.Printf(
					"FIRST-EVENT TIMEOUT FIRED (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, firstEventTimeout,
				)
				return errFirstEventTimeout
			}
			// Already committed (shouldn't happen: timer is stopped after first
			// byte) — fall through to idle handling.
			idleTimer = time.NewTimer(idleTimeout)

		case <-idleTimer.C:
			if !committed {
				// Pre-commit silence for idleTimeout — treat like first-event
				// timeout: abort upstream, allow failover.
				log.Printf(
					"STREAM IDLE TIMEOUT (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, idleTimeout,
				)
				return errFirstEventTimeout
			}
			log.Printf("STREAM IDLE TIMEOUT FIRED for request %s after %s", requestID, idleTimeout)
			writeStreamError("stream idle timeout: no data for "+idleTimeout.String())
			return fmt.Errorf("stream idle timeout: no data for %s", idleTimeout)

		case <-ctx.Done():
			log.Printf("streamResponse: context done for request %s", requestID)
			return ctx.Err()
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
) (success bool, retryable bool, err error) {
	started := time.Now()
	statusCode := 0
	routeOutcome := "failure"

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
		}
		if err != nil {
			e.Error = err.Error()
		}
		logRouteEvent(e)
	}()

	logRouteEvent(RouteEvent{
		Event:      "route_attempt",
		RequestID:  requestID,
		Provider:   p.Name,
		Model:      route.Model,
		Preference: preferenceName,
	})

	keyEnv, key, ok := selectProviderKey(p)
	if !ok {
		return false, false, fmt.Errorf("no usable API key for provider %s", p.Name)
	}

	req, err := buildRequest(r, body, route, p, key)
	if err != nil {
		providerKeyFailed(p, keyEnv)
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
					n = 120
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
		routeTimeoutSeconds = 120
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

	// IMPORTANT:
	// Once headers are written, the request is committed.
	// A later stream failure cannot safely be replayed onto another provider.
	providerKeySucceeded(p, keyEnv)
	providerSucceeded(p, route.Model)

	routeOutcome = "success"
	err = streamResponse(w, resp)

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
	for {
		attempted := false

		for _, route := range preference.Routes {
			p := providerByName(route.Provider)
			if p == nil {
				log.Printf(
					"route provider=%s not configured",
					route.Provider,
				)
				continue
			}

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
				)

				if r.Context().Err() != nil {
					return
				}

				if success {
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

				// Emit structured preference fallback event
				logRouteEvent(RouteEvent{
					Event:          "preference_fallback",
					RequestID:      requestID,
					FromPreference: current,
					ToPreference:   next.Name,
					Context:        "request",
				})

				continue
			}
		}

		// All configured preferences have been exhausted.
		// Return 503 immediately instead of blocking the request.
		waitForRecovery(w, r)
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
	Timestamp       string `json:"timestamp"`
	Event           string `json:"event"`
	RequestID       string `json:"request_id,omitempty"`
	Provider        string `json:"provider,omitempty"`
	Model           string `json:"model,omitempty"`
	Preference      string `json:"preference,omitempty"`
	Outcome         string `json:"outcome,omitempty"`
	Status          int    `json:"status,omitempty"`
	LatencyMS       int64  `json:"latency_ms,omitempty"`
	Error           string `json:"error,omitempty"`
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

	// Also write to test-phase2 directory if CONFIG_FILE is set
	if os.Getenv("CONFIG_FILE") != "" {
		testPhase2EventFile, _ := os.OpenFile("/data/data/com.termux/files/home/CraftedWorkflowsOS/router/test-phase2/router-events.jsonl", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if testPhase2EventFile != nil {
			// We'll write to both - but for simplicity, let's just use the test-phase2 one
			// Actually, let's just use the test-phase2 one directly
			eventFileHandle.Close()
			eventFileHandle = testPhase2EventFile
		}
	}

	f, err := os.OpenFile("router.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		log.Fatal(err)
	}
	log.SetOutput(io.MultiWriter(os.Stderr, f))
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
}

func loadConfig() {
	configFile := "config.json"
	if os.Getenv("CONFIG_FILE") != "" {
		configFile = os.Getenv("CONFIG_FILE")
	}

	data, err := os.ReadFile(configFile)
	if err != nil {
		log.Fatal(err)
	}

	if err := json.Unmarshal(data, &cfg); err != nil {
		log.Fatal(err)
	}

	if cfg.Port == 0 {
		cfg.Port = 8643
	}

	log.Printf(
		"loaded %d preferences, %d providers",
		len(cfg.Preferences),
		len(cfg.Providers),
	)
}

func main() {
	setupLogging()
	loadConfig()

	go catalogLoop()
	go hardFailureRecoveryLoop()

	mux := http.NewServeMux()

	mux.HandleFunc("/health", health)
	mux.HandleFunc("/v1/models", models)
	mux.HandleFunc("/v1/chat/completions", proxy)

	// Test-only: reset all provider/key state and truncate events file
	mux.HandleFunc("/test/reset", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		cfgMu.RLock()
		providers := append([]*Provider(nil), cfg.Providers...)
		cfgMu.RUnlock()

		for _, p := range providers {
			p.mu.Lock()
			p.routes = make(map[string]*RouteHealth)
			p.keys = make(map[string]*KeyHealth)
			p.catalog = nil
			p.catalogAt = time.Time{}
			p.mu.Unlock()
		}

		// Truncate events file
		eventMu.Lock()
		if eventFileHandle != nil {
			eventFileHandle.Truncate(0)
			eventFileHandle.Seek(0, 0)
		}
		eventMu.Unlock()

		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "reset complete"})
	})

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
