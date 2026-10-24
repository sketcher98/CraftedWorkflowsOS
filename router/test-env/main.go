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
	// FIX-2b: max auto-continuations on finish_reason=length (0 disables stitching)
	ContinuationMax int `json:"continuation_max"`
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
	// FIX-3 LKGP: last-known-good provider per preference (memory-only)
	lkgp   = make(map[string]string)
	lkgpMu sync.Mutex
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

// FIX-1: stream telemetry accumulated while piping.
type StreamTelemetry struct {
	FinishReason    string // from terminal chunk
	ReasoningTokens int    // usage.completion_tokens_details.reasoning_tokens (summed)
	ComplTokens     int    // usage.completion_tokens (summed)
	PromptTokens    int    // usage.prompt_tokens (first seen)
	SawToolCall     bool   // any tool_calls delta seen (stitching forbidden)
}

// sseTerminalInfo is what we extract from one SSE data event.
type sseTerminalInfo struct {
	FinishReason    string
	PromptTokens    int
	ComplTokens     int
	ReasoningTokens int
	Content         string // delta content carried by this event (FIX-2b)
	SawToolCall     bool   // tool_calls delta present (stitching forbidden)
}

// parseSSEEvent inspects one complete SSE data event (payload without the
// "data: " prefix). Returns info if the event carries finish_reason or usage.
func parseSSEEvent(payload []byte) (sseTerminalInfo, bool) {
	var ev struct {
		Choices []struct {
			Delta struct {
				ToolCalls []json.RawMessage `json:"tool_calls"`
				Content   string           `json:"content"`
			} `json:"delta"`
			FinishReason *string `json:"finish_reason"`
		} `json:"choices"`
		Usage *struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
			CompletionTokensDetails *struct {
				ReasoningTokens int `json:"reasoning_tokens"`
			} `json:"completion_tokens_details"`
		} `json:"usage"`
	}
	if json.Unmarshal(payload, &ev) != nil {
		return sseTerminalInfo{}, false
	}
	info := sseTerminalInfo{}
	found := false
	for _, ch := range ev.Choices {
		if len(ch.Delta.ToolCalls) > 0 {
			info.SawToolCall = true
		}
		if ch.Delta.Content != "" {
			info.Content += ch.Delta.Content
		}
		if ch.FinishReason != nil && *ch.FinishReason != "" {
			info.FinishReason = *ch.FinishReason
			found = true
		}
	}
	if ev.Usage != nil {
		info.PromptTokens = ev.Usage.PromptTokens
		info.ComplTokens = ev.Usage.CompletionTokens
		if ev.Usage.CompletionTokensDetails != nil {
			info.ReasoningTokens = ev.Usage.CompletionTokensDetails.ReasoningTokens
		}
		found = true
	}
	return info, found
}

// requestIsStream reports whether the client asked for an SSE stream.
func requestIsStream(body []byte) bool {
	var payload struct {
		Stream bool `json:"stream"`
	}
	if json.Unmarshal(body, &payload) != nil {
		return false
	}
	return payload.Stream
}

// requestMaxTokens extracts max_tokens/max_completion_tokens if set.
func requestMaxTokens(body []byte) (int, bool) {
	var payload struct {
		MaxTokens         *int `json:"max_tokens"`
		MaxComplTokens    *int `json:"max_completion_tokens"`
	}
	if json.Unmarshal(body, &payload) != nil {
		return 0, false
	}
	if payload.MaxTokens != nil {
		return *payload.MaxTokens, true
	}
	if payload.MaxComplTokens != nil {
		return *payload.MaxComplTokens, true
	}
	return 0, false
}

// ssePayload extracts the data payload from one SSE event. Returns nil when
// the event carries no data line (comments, keep-alives).
func ssePayload(event []byte) []byte {
	for len(event) > 0 {
		lineEnd := bytes.IndexByte(event, '\n')
		var line []byte
		if lineEnd == -1 {
			line, event = event, nil
		} else {
			line, event = event[:lineEnd], event[lineEnd+1:]
		}
		trimmed := bytes.TrimSuffix(line, []byte("\r"))
		if bytes.HasPrefix(trimmed, []byte("data:")) {
			payload := bytes.TrimPrefix(trimmed, []byte("data:"))
			payload = bytes.TrimPrefix(payload, []byte(" "))
			return payload
		}
	}
	return nil
}

// rewriteTerminalUsage rewrites usage numbers in a held terminal SSE event so
// the client sees totals across stitched segments (FIX-2b).
func rewriteTerminalUsage(event []byte, promptTokens, complTokens, reasoningTokens int) []byte {
	payload := ssePayload(event)
	if payload == nil {
		return event
	}
	var m map[string]interface{}
	if json.Unmarshal(payload, &m) != nil {
		return event
	}
	usage, ok := m["usage"].(map[string]interface{})
	if !ok {
		return event // no usage field: leave untouched
	}
	if promptTokens > 0 {
		usage["prompt_tokens"] = promptTokens
	}
	if complTokens > 0 {
		usage["completion_tokens"] = complTokens
	}
	if reasoningTokens > 0 {
		details, ok := usage["completion_tokens_details"].(map[string]interface{})
		if !ok {
			details = make(map[string]interface{})
			usage["completion_tokens_details"] = details
		}
		details["reasoning_tokens"] = reasoningTokens
	}
	newPayload, err := json.Marshal(m)
	if err != nil {
		return event
	}
	out := append([]byte("data: "), newPayload...)
	out = append(out, []byte("\n\n")...)
	return out
}

// rewriteNonStream merges accumulated content/finish/usage into the last
// segment's JSON body for the non-streaming path (FIX-2b).
func rewriteNonStream(body []byte, content, finishReason string, promptTokens, complTokens, reasoningTokens int) []byte {
	var m map[string]interface{}
	if json.Unmarshal(body, &m) != nil {
		return body
	}
	choices, ok := m["choices"].([]interface{})
	if !ok || len(choices) == 0 {
		return body
	}
	first, ok := choices[0].(map[string]interface{})
	if !ok {
		return body
	}
	if content != "" {
		if msg, ok := first["message"].(map[string]interface{}); ok {
			msg["content"] = content
		}
	}
	if finishReason != "" {
		first["finish_reason"] = finishReason
	}
	if complTokens > 0 {
		usage, ok := m["usage"].(map[string]interface{})
		if !ok {
			usage = make(map[string]interface{})
			m["usage"] = usage
		}
		if promptTokens > 0 {
			usage["prompt_tokens"] = promptTokens
		}
		usage["completion_tokens"] = complTokens
		if reasoningTokens > 0 {
			details, ok := usage["completion_tokens_details"].(map[string]interface{})
			if !ok {
				details = make(map[string]interface{})
				usage["completion_tokens_details"] = details
			}
			details["reasoning_tokens"] = reasoningTokens
		}
	}
	out, err := json.Marshal(m)
	if err != nil {
		return body
	}
	return out
}
// Supports \n\n, \r\n\r\n and \r\r boundaries. Returns the event INCLUDING its
// terminator and the number of bytes consumed. consumed==0 => need more data.
func splitSSEEvent(b []byte) (event []byte, consumed int) {
	for i := 0; i < len(b); i++ {
		if b[i] == '\n' {
			if i+2 <= len(b) && b[i+1] == '\n' {
				return b[:i+2], i + 2
			}
			continue
		}
		if b[i] == '\r' {
			if i+4 <= len(b) && b[i+1] == '\n' && b[i+2] == '\r' && b[i+3] == '\n' {
				return b[:i+4], i + 4
			}
			if i+2 <= len(b) && b[i+1] == '\r' {
				return b[:i+2], i + 2
			}
		}
	}
	return nil, 0
}

// intPtr returns a pointer for positive values, nil otherwise (omitempty zero).
func intPtr(n int) *int {
	if n > 0 {
		return &n
	}
	return nil
}

// streamResult reports what happened while serving one request's response.
type streamResult struct {
	Committed     bool // bytes were written to the client
	Continuations int  // FIX-2b: auto-continuations performed
	Tel           StreamTelemetry
}

// buildContinuationBody appends the partial assistant content and a
// continuation instruction to the original request messages (FIX-2b).
func buildContinuationBody(originalBody []byte, assistantContent string) ([]byte, error) {
	var payload map[string]interface{}
	if err := json.Unmarshal(originalBody, &payload); err != nil {
		return nil, err
	}
	msgs, _ := payload["messages"].([]interface{})
	if assistantContent != "" {
		msgs = append(msgs, map[string]interface{}{"role": "assistant", "content": assistantContent})
	}
	msgs = append(msgs, map[string]interface{}{
		"role":    "user",
		"content": "Continue your previous response exactly where it stopped. Do not repeat any text you already wrote.",
	})
	payload["messages"] = msgs
	return json.Marshal(payload)
}

// nonStreamInfo is the parsed essence of a non-streaming completion JSON.
type nonStreamInfo struct {
	Content         string
	FinishReason    string
	PromptTokens    int
	ComplTokens     int
	ReasoningTokens int
	SawToolCall     bool
}

// parseNonStreamResponse extracts truncation-relevant fields from a
// non-streaming completion response body.
func parseNonStreamResponse(body []byte) (nonStreamInfo, bool) {
	var ev struct {
		Choices []struct {
			Message struct {
				Content   string           `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"message"`
			FinishReason *string `json:"finish_reason"`
		} `json:"choices"`
		Usage *struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
			CompletionTokensDetails *struct {
				ReasoningTokens int `json:"reasoning_tokens"`
			} `json:"completion_tokens_details"`
		} `json:"usage"`
	}
	if json.Unmarshal(body, &ev) != nil || len(ev.Choices) == 0 {
		return nonStreamInfo{}, false
	}
	info := nonStreamInfo{}
	for _, ch := range ev.Choices {
		if len(ch.Message.ToolCalls) > 0 {
			info.SawToolCall = true
		}
		info.Content += ch.Message.Content
		if ch.FinishReason != nil && *ch.FinishReason != "" {
			info.FinishReason = *ch.FinishReason
		}
	}
	if ev.Usage != nil {
		info.PromptTokens = ev.Usage.PromptTokens
		info.ComplTokens = ev.Usage.CompletionTokens
		if ev.Usage.CompletionTokensDetails != nil {
			info.ReasoningTokens = ev.Usage.CompletionTokensDetails.ReasoningTokens
		}
	}
	return info, true
}

// maxNonStreamBytes caps buffered non-streaming bodies (64 MiB safety valve).
const maxNonStreamBytes = 64 << 20

// readUpstreamBody reads an entire upstream response body with fail-fast
// timers. Used by the non-streaming path; caller owns resp.Body.
func readUpstreamBody(resp *http.Response, firstEventTimeout, idleTimeout time.Duration, requestID string) ([]byte, error) {
	buf := make([]byte, 32*1024)
	_ = buf // per-read private buffers allocated in the reader goroutine (race fix)
	full := []byte{}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	type readResult struct {
		data []byte // private buffer per read — NEVER reuse across reads (data race)
		err  error
	}
	readCh := make(chan readResult, 1)
	go func() {
		for {
			rbuf := make([]byte, 32*1024)
			n, err := resp.Body.Read(rbuf)
			readCh <- readResult{data: rbuf[:n], err: err}
			if err != nil {
				return
			}
		}
	}()

	firstEventTimer := time.NewTimer(firstEventTimeout)
	defer firstEventTimer.Stop()
	idleTimer := time.NewTimer(idleTimeout)
	defer idleTimer.Stop()

	for {
		select {
		case res := <-readCh:
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
			if len(res.data) > 0 {
				full = append(full, res.data...)
				if len(full) > maxNonStreamBytes {
					return full, fmt.Errorf("non-stream response exceeds %d bytes cap", maxNonStreamBytes)
				}
			}
			if res.err == io.EOF {
				return full, nil
			}
			if res.err != nil {
				return full, res.err
			}
			idleTimer = time.NewTimer(idleTimeout)
		case <-firstEventTimer.C:
			log.Printf("non-stream first-event timeout for request %s after %s", requestID, firstEventTimeout)
			return full, errFirstEventTimeout
		case <-idleTimer.C:
			log.Printf("non-stream idle timeout for request %s after %s", requestID, idleTimeout)
			return full, errFirstEventTimeout
		case <-ctx.Done():
			return full, ctx.Err()
		}
	}
}

// --- FIX-3: LKGP (last-known-good-provider) primitives ---

func lkgpPinned(preferenceName string) string {
	lkgpMu.Lock()
	defer lkgpMu.Unlock()
	return lkgp[preferenceName]
}

func setLKGP(preferenceName, providerName string) {
	lkgpMu.Lock()
	defer lkgpMu.Unlock()
	lkgp[preferenceName] = providerName
}

func evictLKGP(preferenceName, providerName string) {
	lkgpMu.Lock()
	defer lkgpMu.Unlock()
	if lkgp[preferenceName] == providerName {
		delete(lkgp, preferenceName)
	}
}

// orderedPreferenceRoutes returns the preference's routes with the LKGP-pinned
// provider first (stable order otherwise).
func orderedPreferenceRoutes(preference *Preference) []Route {
	pinned := lkgpPinned(preference.Name)
	if pinned == "" {
		return preference.Routes
	}
	ordered := make([]Route, 0, len(preference.Routes))
	for _, rt := range preference.Routes {
		if rt.Provider == pinned {
			ordered = append(ordered, rt)
		}
	}
	for _, rt := range preference.Routes {
		if rt.Provider != pinned {
			ordered = append(ordered, rt)
		}
	}
	return ordered
}

// routingHeaders is the set of X-Router-* headers set before the upstream
// call. They must survive copyHeaders(w, resp.Header).
type routingHeaders struct {
	RequestID          string
	Provider           string
	Model              string
	Preference          string
	Fallback           string
	FallbackCount      string
	FallbackFrom       string
	PrefFallback       string
	PrefFrom           string
	PrefTo             string
	SkippedHardFailed  string
	FinishReason       string
	MaxTokens          string
	ReasoningTokens    string
	CompletionTokens   string
	Continuations      string
	LKGP               string
}

func captureRoutingHeaders(w http.ResponseWriter) routingHeaders {
	h := w.Header()
	return routingHeaders{
		RequestID:         h.Get("X-Request-ID"),
		Provider:          h.Get("X-Router-Provider"),
		Model:             h.Get("X-Router-Model"),
		Preference:        h.Get("X-Router-Preference"),
		Fallback:          h.Get("X-Router-Fallback"),
		FallbackCount:     h.Get("X-Router-Fallback-Count"),
		FallbackFrom:      h.Get("X-Router-Fallback-From"),
		PrefFallback:      h.Get("X-Router-Preference-Fallback"),
		PrefFrom:          h.Get("X-Router-Preference-From"),
		PrefTo:            h.Get("X-Router-Preference-To"),
		SkippedHardFailed: h.Get("X-Router-Skipped-Hard-Failed"),
		FinishReason:      h.Get("X-Router-Finish-Reason"),
		MaxTokens:         h.Get("X-Router-Max-Tokens"),
		ReasoningTokens:   h.Get("X-Router-Reasoning-Tokens"),
		CompletionTokens:  h.Get("X-Router-Completion-Tokens"),
		Continuations:     h.Get("X-Router-Continuations"),
	}
}

func restoreRoutingHeaders(w http.ResponseWriter, s routingHeaders) {
	h := w.Header()
	if s.RequestID != "" {
		h.Set("X-Request-ID", s.RequestID)
	}
	if s.Provider != "" {
		h.Set("X-Router-Provider", s.Provider)
	}
	if s.Model != "" {
		h.Set("X-Router-Model", s.Model)
	}
	if s.Preference != "" {
		h.Set("X-Router-Preference", s.Preference)
	}
	if s.Fallback != "" {
		h.Set("X-Router-Fallback", s.Fallback)
	}
	if s.FallbackCount != "" {
		h.Set("X-Router-Fallback-Count", s.FallbackCount)
	}
	if s.FallbackFrom != "" {
		h.Set("X-Router-Fallback-From", s.FallbackFrom)
	}
	if s.PrefFallback != "" {
		h.Set("X-Router-Preference-Fallback", s.PrefFallback)
	}
	if s.PrefFrom != "" {
		h.Set("X-Router-Preference-From", s.PrefFrom)
	}
	if s.PrefTo != "" {
		h.Set("X-Router-Preference-To", s.PrefTo)
	}
	if s.SkippedHardFailed != "" {
		h.Set("X-Router-Skipped-Hard-Failed", s.SkippedHardFailed)
	}
	if s.FinishReason != "" {
		h.Set("X-Router-Finish-Reason", s.FinishReason)
	}
	if s.MaxTokens != "" {
		h.Set("X-Router-Max-Tokens", s.MaxTokens)
	}
	if s.ReasoningTokens != "" {
		h.Set("X-Router-Reasoning-Tokens", s.ReasoningTokens)
	}
	if s.CompletionTokens != "" {
		h.Set("X-Router-Completion-Tokens", s.CompletionTokens)
	}
	if s.Continuations != "" {
		h.Set("X-Router-Continuations", s.Continuations)
	}
	if s.LKGP != "" {
		h.Set("X-Router-LKGP", s.LKGP)
	}
}

// serveStreamState carries everything needed to serve one client response,
// including automatic continuations on length-truncation (FIX-2b).
type serveStreamState struct {
	w                 http.ResponseWriter
	r                 *http.Request
	body              []byte // original client request body
	route             Route
	provider          *Provider
	client            *http.Client
	key               string
	requestID         string
	firstEventTimeout time.Duration
	idleTimeout       time.Duration
	continuationMax   int

	flusher   http.Flusher
	canFlush  bool
	committed bool
}

func (s *serveStreamState) commit(resp *http.Response) {
	if s.committed {
		return
	}
	s.w.WriteHeader(resp.StatusCode)
	s.committed = true
}

// forward writes bytes downstream, committing on first write (deferred commit).
// Validates that forwarded SSE events are complete (terminator intact) —
// corruption here means the hold-window sliced an event mid-payload.
func (s *serveStreamState) forward(b []byte, resp *http.Response) error {
	if len(b) == 0 {
		return nil
	}
	if ln := len(b); ln >= 2 && (b[ln-1] != '\n' || b[ln-2] != '\n') {
		if !bytes.HasSuffix(b, []byte("\n\n")) && !bytes.HasSuffix(b, []byte("\r\n\r\n")) && !bytes.HasSuffix(b, []byte("\r\r")) {
			log.Printf("SSE-CORRUPTION: request %s forwarding %d bytes WITHOUT event terminator: %q...%q",
				s.requestID, ln, b[:min(40, ln)], b[max(0, ln-40):])
		}
	}
	s.commit(resp)
	if _, werr := s.w.Write(b); werr != nil {
		return werr
	}
	if s.canFlush {
		s.flusher.Flush()
	}
	return nil
}

// writeStreamError sends a terminal error event after commitment so the
// client gets an explicit signal instead of a silent truncation.
func (s *serveStreamState) writeStreamError(reason string) {
	if !s.committed {
		return
	}
	errEvent := fmt.Sprintf(
		"data: {\"error\":{\"message\":\"upstream stream stalled: %s\",\"type\":\"stream_error\",\"code\":\"stream_stalled\"}}\n\ndata: [DONE]\n\n",
		reason,
	)
	if _, writeErr := s.w.Write([]byte(errEvent)); writeErr != nil {
		log.Printf("streamResponse: failed to write terminal error event for request %s: %v", s.requestID, writeErr)
		return
	}
	if s.canFlush {
		s.flusher.Flush()
	}
	log.Printf("streamResponse: wrote terminal stream_error event for request %s (reason=%s)", s.requestID, reason)
}

// pumpResult reports what one upstream segment produced.
type pumpResult struct {
	held1   []byte // second-to-last complete SSE event (held back)
	held2   []byte // last complete SSE event (held back)
	pending []byte // bytes after the last complete event (incomplete tail)
	content string // accumulated delta content
	tel     StreamTelemetry
	sawDONE bool
}

// isDONEEvent reports whether a held SSE event is the [DONE] sentinel.
func isDONEEvent(event []byte) bool {
	return event != nil && bytes.Equal(bytes.TrimSpace(event), []byte("data: [DONE]"))
}

// eventFinishReason extracts finish_reason from a held SSE event ("" if none).
func eventFinishReason(event []byte) string {
	payload := ssePayload(event)
	if payload == nil {
		return ""
	}
	info, ok := parseSSEEvent(payload)
	if !ok {
		return ""
	}
	return info.FinishReason
}

// pumpSegment reads one upstream SSE response to EOF, forwarding every event
// except the last two (held back for the stitching decision). Timer
// semantics match the pre-FIX streamResponse: first-event deadline before
// the first upstream byte, idle deadline between upstream reads.
func pumpSegment(state *serveStreamState, resp *http.Response) (pumpResult, error) {
	defer resp.Body.Close()
	res := pumpResult{}
	requestID := state.requestID

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	type readResult struct {
		data []byte // private buffer per read — NEVER reuse across reads (data race)
		err  error
	}
	readCh := make(chan readResult, 1)
	go func() {
		for {
			rbuf := make([]byte, 32*1024)
			n, err := resp.Body.Read(rbuf)
			readCh <- readResult{data: rbuf[:n], err: err}
			if err != nil {
				return
			}
		}
	}()

	firstEventTimer := time.NewTimer(state.firstEventTimeout)
	defer firstEventTimer.Stop()
	idleTimer := time.NewTimer(state.idleTimeout)
	defer idleTimer.Stop()

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
			if len(result.data) > 0 {
				res.pending = append(res.pending, result.data...)
				for {
					ev, consumed := splitSSEEvent(res.pending)
					if consumed == 0 {
						break
					}
					// COPY the event out of pending's backing array: held events
					// outlive multiple append generations of res.pending, and any
					// in-place append reuse of that array corrupts aliased slices
					// (observed as mid-JSON splices against real NVIDIA SSE).
					evCopy := append([]byte(nil), ev...)
					res.pending = res.pending[consumed:]
					if res.held1 != nil {
						if ferr := state.forward(res.held1, resp); ferr != nil {
							return res, ferr
						}
					}
					res.held1 = res.held2
					res.held2 = evCopy
					if payload := ssePayload(ev); payload != nil {
						if string(payload) == "[DONE]" {
							res.sawDONE = true
						} else {
							// Parse EVERY data event: content deltas carry neither
							// finish_reason nor usage, but MUST accumulate for
							// continuation stitching (FIX-2b).
							info, _ := parseSSEEvent(payload)
							if info.SawToolCall {
								res.tel.SawToolCall = true
							}
							res.content += info.Content
							if info.FinishReason != "" {
								res.tel.FinishReason = info.FinishReason
							}
							if info.PromptTokens > 0 && res.tel.PromptTokens == 0 {
								res.tel.PromptTokens = info.PromptTokens
							}
							res.tel.ComplTokens += info.ComplTokens
							res.tel.ReasoningTokens += info.ReasoningTokens
						}
					}
				}
			}

			if result.err == io.EOF {
				return res, nil
			}
			if result.err != nil {
				return res, result.err
			}

			idleTimer = time.NewTimer(state.idleTimeout)

		case <-firstEventTimer.C:
			if !state.committed {
				log.Printf(
					"FIRST-EVENT TIMEOUT FIRED (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, state.firstEventTimeout,
				)
				return res, errFirstEventTimeout
			}
			idleTimer = time.NewTimer(state.idleTimeout)

		case <-idleTimer.C:
			if !state.committed {
				log.Printf(
					"STREAM IDLE TIMEOUT (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, state.idleTimeout,
				)
				return res, errFirstEventTimeout
			}
			log.Printf("STREAM IDLE TIMEOUT FIRED for request %s after %s", requestID, state.idleTimeout)
			state.writeStreamError("stream idle timeout: no data for "+state.idleTimeout.String())
			return res, fmt.Errorf("stream idle timeout: no data for %s", state.idleTimeout)

		case <-ctx.Done():
			return res, ctx.Err()
		}
	}
}

// streamResponse serves the client response for one successful route: pipes
// the upstream SSE stream with fail-fast timers, collects truncation
// telemetry (FIX-1), and transparently continues length-truncated responses
// up to continuationMax times (FIX-2b).
func streamResponse(
	w http.ResponseWriter,
	resp *http.Response,
	state *serveStreamState,
) (streamResult, error) {
	state.w = w
	if state.requestID == "" {
		state.requestID = w.Header().Get("X-Request-ID")
	}
	log.Printf("streamResponse: ENTERED for request %s", state.requestID)

	// Capture routing headers, copy upstream headers, restore ours (they
	// take precedence) — identical to the pre-FIX behavior.
	saved := captureRoutingHeaders(w)
	copyHeaders(w, resp.Header)
	restoreRoutingHeaders(w, saved)

	state.flusher, state.canFlush = w.(http.Flusher)

	out := streamResult{}
	tel := StreamTelemetry{}
	content := ""
	continuations := 0

	seg, err := pumpSegment(state, resp)

	for {
		if err != nil {
			break
		}

		// Merge segment telemetry into request totals.
		content += seg.content
		if seg.tel.SawToolCall {
			tel.SawToolCall = true
		}
		tel.ComplTokens += seg.tel.ComplTokens
		tel.ReasoningTokens += seg.tel.ReasoningTokens
		if seg.tel.PromptTokens > 0 && tel.PromptTokens == 0 {
			tel.PromptTokens = seg.tel.PromptTokens
		}
		if seg.tel.FinishReason != "" {
			tel.FinishReason = seg.tel.FinishReason
		}

		// FIX-2b: stitch on length-truncation — never across tool calls. If
		// events arrived AFTER upstream's [DONE] (trailing garbage/heartbeats),
		// the DONE already rolled out mid-stream — do not stitch on top of a
		// stream the client already saw closed.
		if eventFinishReason(seg.held1) == "length" &&
			(!seg.sawDONE || isDONEEvent(seg.held2)) &&
			!tel.SawToolCall &&
			state.continuationMax > 0 &&
			continuations < state.continuationMax {
			// The terminal length-event (held1) is intentionally DROPPED — the
			// continuation segment re-emits a proper terminal chunk. held2 (the
			// segment's [DONE]) must never reach the client here.
			contBody, cerr := buildContinuationBody(state.body, content)
			if cerr == nil {
				req2, rerr := buildRequest(state.r, contBody, state.route, state.provider, state.key)
				if rerr == nil {
					resp2, derr := state.client.Do(req2)
					if derr == nil {
						if resp2.StatusCode/100 == 2 {
							continuations++
							log.Printf(
								"continuation #%d for request %s (stitched %d content bytes so far)",
								continuations, state.requestID, len(content),
							)
							seg, err = pumpSegment(state, resp2)
							continue
						}
						io.Copy(io.Discard, resp2.Body)
						resp2.Body.Close()
					}
				}
			}
			log.Printf("continuation unavailable for request %s — forwarding truncation as-is", state.requestID)
		}

		// No stitching: flush held events + tail in order.
		held1 := seg.held1
		if continuations > 0 {
			held1 = rewriteTerminalUsage(seg.held1, tel.PromptTokens, tel.ComplTokens, tel.ReasoningTokens)
		}
		if ferr := state.forward(held1, resp); ferr != nil {
			err = ferr
			break
		}
		if ferr := state.forward(seg.held2, resp); ferr != nil {
			err = ferr
			break
		}
		if ferr := state.forward(seg.pending, resp); ferr != nil {
			err = ferr
		}
		break
	}

	out.Committed = state.committed
	out.Continuations = continuations
	out.Tel = tel

	if err != nil {
		if errors.Is(err, errFirstEventTimeout) {
			return out, err
		}
		if state.committed {
			state.writeStreamError(fmt.Sprintf("read error: %v", err))
		}
		return out, err
	}

	return out, nil
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
		MaxTokens:  func() *int {
			if mt, has := requestMaxTokens(body); has {
				return &mt
			}
			return nil
		}(),
		LKGP: lkgpPinned(preferenceName) == p.Name,
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
	setLKGP(preferenceName, p.Name) // FIX-3: pin this provider for the preference

	routeOutcome = "success"

	// Build the shared stream-serving state (FIX-1/2b).
	cfgMu.RLock()
	idleTO := time.Duration(cfg.Retry.StreamIdleTimeoutSeconds) * time.Second
	firstTO := time.Duration(cfg.Retry.FirstEventTimeoutSeconds) * time.Second
	contMax := cfg.Retry.ContinuationMax
	cfgMu.RUnlock()
	if idleTO <= 0 {
		idleTO = 60 * time.Second
	}
	if firstTO <= 0 {
		firstTO = 45 * time.Second
	}

	state := &serveStreamState{
		r:                 r,
		body:              body,
		route:             route,
		provider:          p,
		client:            client,
		key:               key,
		requestID:         requestID,
		firstEventTimeout: firstTO,
		idleTimeout:       idleTO,
		continuationMax:   contMax,
	}

	if requestIsStream(body) {
		var sres streamResult
		sres, err = streamResponse(w, resp, state)

		if err == nil || sres.Committed {
			// FIX-1: emit stream telemetry once the client response is settled.
			e := RouteEvent{
				Event:           "stream_result",
				RequestID:       requestID,
				Provider:        p.Name,
				Model:           route.Model,
				Preference:      preferenceName,
				FinishReason:    sres.Tel.FinishReason,
				ReasoningTokens: intPtr(sres.Tel.ReasoningTokens),
				ComplTokens:     intPtr(sres.Tel.ComplTokens),
				Continuations:   sres.Continuations,
				ToolCallActive:  sres.Tel.SawToolCall,
			}
			if sres.Tel.FinishReason == "length" {
				e.Event = "truncation_detected"
			}
			logRouteEvent(e)
		}

		if err != nil {
			if errors.Is(err, errFirstEventTimeout) {
				// Pre-commit abort: nothing was sent downstream — fully retryable.
				routeOutcome = "transient"
				statusCode = http.StatusGatewayTimeout
				providerFailed(p, route.Model)
				evictLKGP(preferenceName, p.Name) // FIX-3
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
					// Return success=false so proxy doesn't exit; retryable=false
					// so we don't retry the same route. This breaks the inner loop,
					// then attempted=true triggers preference fallback to next model.
					providerFailed(p, route.Model)
					log.Printf(
						"provider=%s model=%s stream ended after commitment: %v (cooldown applied, will preference-fallback)",
						p.Name,
						route.Model,
						err,
					)
				}

				return false, false, err
	}

	// --- Non-streaming path (FIX-1 + FIX-2b) ---
	defer resp.Body.Close()

	respBody, readErr := readUpstreamBody(resp, firstTO, idleTO, requestID)
	if readErr != nil && len(respBody) == 0 {
		if errors.Is(readErr, errFirstEventTimeout) {
			routeOutcome = "transient"
			statusCode = http.StatusGatewayTimeout
			providerFailed(p, route.Model)
			evictLKGP(preferenceName, p.Name)
			return false, true, readErr
		}
		routeOutcome = "stream_error"
		providerFailed(p, route.Model)
		return true, false, readErr
	}

	info, parsed := parseNonStreamResponse(respBody)
	content := ""
	finishReason := ""
	promptTokens, complTokens, reasoningTokens := 0, 0, 0
	continuations := 0

	if parsed {
		content = info.Content
		finishReason = info.FinishReason
		promptTokens = info.PromptTokens
		complTokens = info.ComplTokens
		reasoningTokens = info.ReasoningTokens

		for contMax > 0 && continuations < contMax &&
			finishReason == "length" && !info.SawToolCall {
			contBody, cerr := buildContinuationBody(body, content)
			if cerr != nil {
				break
			}
			req2, rerr := buildRequest(r, contBody, route, p, key)
			if rerr != nil {
				break
			}
			resp2, derr := client.Do(req2)
			if derr != nil {
				break
			}
			if resp2.StatusCode/100 != 2 {
				io.Copy(io.Discard, resp2.Body)
				resp2.Body.Close()
				break
			}
			body2, rerr2 := readUpstreamBody(resp2, firstTO, idleTO, requestID)
			resp2.Body.Close()
			if rerr2 != nil {
				break
			}
			info2, ok2 := parseNonStreamResponse(body2)
			if !ok2 {
				break
			}
			continuations++
			content += info2.Content
			if info2.FinishReason != "" {
				finishReason = info2.FinishReason
			}
			promptTokens = info2.PromptTokens // last segment's prompt (includes history)
			complTokens += info2.ComplTokens
			reasoningTokens += info2.ReasoningTokens
			if info2.SawToolCall {
				break
			}
			if info2.FinishReason != "length" {
				break
			}
			info = info2
		}

		if continuations > 0 {
			respBody = rewriteNonStream(respBody, content, finishReason, promptTokens, complTokens, reasoningTokens)
		}
	}

	// FIX-1 headers (safe: non-streaming, nothing committed yet).
	w.Header().Set("X-Router-Finish-Reason", finishReason)
	if continuations > 0 {
		w.Header().Set("X-Router-Continuations", strconv.Itoa(continuations))
	}
	if reasoningTokens > 0 {
		w.Header().Set("X-Router-Reasoning-Tokens", strconv.Itoa(reasoningTokens))
	}
	if complTokens > 0 {
		w.Header().Set("X-Router-Completion-Tokens", strconv.Itoa(complTokens))
	}

	copyHeaders(w, resp.Header)
	// Body may have been rewritten (stitched) — Content-Length must match
	// what we actually write or clients report transfer errors.
	w.Header().Set("Content-Length", strconv.Itoa(len(respBody)))

	if continuations > 0 || finishReason != "" {
		e := RouteEvent{
			Event:           "stream_result",
			RequestID:       requestID,
			Provider:         p.Name,
			Model:            route.Model,
			Preference:       preferenceName,
			FinishReason:    finishReason,
			ReasoningTokens: intPtr(reasoningTokens),
			ComplTokens:     intPtr(complTokens),
			Continuations:   continuations,
			ToolCallActive:  info.SawToolCall,
		}
		if finishReason == "length" {
			e.Event = "truncation_detected"
		}
		logRouteEvent(e)
	}

	_, werr := w.Write(respBody)
	if werr != nil {
		routeOutcome = "stream_error"
		return true, false, werr
	}

	return true, false, nil
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

	// FIX-1: pre-flight request telemetry header (visible on every outcome).
	if mt, has := requestMaxTokens(body); has {
		w.Header().Set("X-Router-Max-Tokens", strconv.Itoa(mt))
	}
	// FIX-3: expose whether this attempt rides the LKGP pin.
	if lkgpPinned(preference.Name) != "" {
		w.Header().Set("X-Router-LKGP", lkgpPinned(preference.Name))
	}

	// Keep cycling until a route succeeds or the client cancels.
	if err := r.Context().Err(); err != nil {
		return
	}
	for {
		attempted := false

		// FIX-3: LKGP ordering — pinned provider's route first, ladder intact.
		for _, route := range orderedPreferenceRoutes(preference) {
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

				evictLKGP(preference.Name, p.Name) // FIX-3: failed provider loses the pin
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
	FinishReason    string `json:"finish_reason,omitempty"`
	MaxTokens       *int   `json:"max_tokens,omitempty"`
	ReasoningTokens *int   `json:"reasoning_tokens,omitempty"`
	ComplTokens     *int   `json:"completion_tokens,omitempty"`
	Continuations   int    `json:"continuations,omitempty"`
	ToolCallActive  bool   `json:"tool_call_active,omitempty"` // truncation arrived mid-tool-call (stitching forbidden)
	LKGP            bool   `json:"lkgp,omitempty"`             // attempt used the pinned provider
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

		// FIX-3: clear LKGP pins on reset
		lkgpMu.Lock()
		lkgp = make(map[string]string)
		lkgpMu.Unlock()

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
