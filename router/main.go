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
	// ContinuationMax: auto-continue length-truncated responses up to N times
	// (0 disables stitching). Tool-call truncations are NEVER stitched.
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
)

// lkgp tracks the last-known-good provider per preference name for LKGP
// routing (try the provider that last succeeded first).
var (
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

// --- Truncation telemetry + continuation stitching (ported from test-env 2026-10-24) ---

// ssePayloadOf extracts the first data-payload from an SSE event (nil if none).
func ssePayloadOf(event []byte) []byte {
	lines := bytes.Split(event, []byte("\n"))
	for _, line := range lines {
		line = bytes.TrimSuffix(line, []byte("\r"))
		if bytes.HasPrefix(line, []byte("data:")) {
			payload := bytes.TrimPrefix(line, []byte("data:"))
			payload = bytes.TrimPrefix(payload, []byte(" "))
			return payload
		}
	}
	return nil
}

// isDONEEvent reports whether a held SSE event is the [DONE] sentinel.
func isDONEEvent(event []byte) bool {
	return event != nil && bytes.Equal(bytes.TrimSpace(ssePayloadOf(event)), []byte("[DONE]"))
}

// sseTerminalInfo is what we extract from one SSE data payload.
type sseTerminalInfo struct {
	FinishReason    string
	PromptTokens    int
	ComplTokens     int
	ReasoningTokens int
	Content         string
	SawToolCall     bool
}

// parseSSEPayload inspects one SSE data payload for finish_reason/usage/content.
func parseSSEPayload(payload []byte) (sseTerminalInfo, bool) {
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

// StreamTelemetry accumulated across a whole client response (all segments).
type StreamTelemetry struct {
	FinishReason    string
	ReasoningTokens int
	ComplTokens     int
	PromptTokens    int
	SawToolCall     bool
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
		MaxTokens      *int `json:"max_tokens"`
		MaxComplTokens *int `json:"max_completion_tokens"`
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

// intPtr returns a pointer for positive values, nil otherwise.
func intPtr(n int) *int {
	if n > 0 {
		return &n
	}
	return nil
}

// streamResult reports what happened serving one request's response.
type streamResult struct {
	Committed     bool
	Continuations int
	Tel           StreamTelemetry
}

// buildContinuationBody appends partial assistant content + a continuation
// instruction to the original request messages (FIX-2b).
// Increases max_tokens on each continuation so reasoning doesn't consume the
// entire budget again (prevents infinite truncation loop).
func buildContinuationBody(originalBody []byte, assistantContent string, continuationNum int) ([]byte, error) {
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
	// Increase max_tokens on each continuation so reasoning has room to produce visible text.
	if mt, ok := payload["max_tokens"].(float64); ok && mt > 0 {
		payload["max_tokens"] = mt + float64(continuationNum*64)
	}
	return json.Marshal(payload)
}

// eventFinishReason extracts finish_reason from a held SSE event ("" if none).
func eventFinishReason(event []byte) string {
	payload := ssePayloadOf(event)
	if payload == nil {
		return ""
	}
	info, _ := parseSSEPayload(payload)
	return info.FinishReason
}

// rewriteTerminalUsage rewrites usage numbers in a held terminal SSE event so
// the client sees totals across stitched segments (FIX-2b).
func rewriteTerminalUsage(event []byte, promptTokens, complTokens, reasoningTokens int) []byte {
	payload := ssePayloadOf(event)
	if payload == nil {
		return event
	}
	var m map[string]interface{}
	if json.Unmarshal(payload, &m) != nil {
		return event
	}
	usage, ok := m["usage"].(map[string]interface{})
	if !ok {
		return event
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

// maxNonStreamBytes caps buffered non-streaming bodies (64 MiB safety valve).
const maxNonStreamBytes = 64 << 20

// readUpstreamBody reads an entire upstream response body with fail-fast
// timers (first-event + idle). Per-read private buffers — never reuse across
// reads (data race). Used by the non-streaming path; caller owns resp.Body.
func readUpstreamBody(resp *http.Response, firstEventTimeout, idleTimeout time.Duration, requestID string) ([]byte, error) {
	full := []byte{}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

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
			go read()
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

// --- LKGP (last-known-good-provider) primitives (FIX-3) ---

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
// provider first (stable order otherwise). MUST be called inside the proxy
// outer loop — preference advances on preference_fallback.
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

// routingHeaders is the set of X-Router-* headers set before the upstream
// call. They must survive copyHeaders(w, resp.Header).
type routingHeaders struct {
	RequestID         string
	Provider          string
	Model             string
	Preference         string
	Fallback          string
	FallbackCount     string
	FallbackFrom      string
	PrefFallback      string
	PrefFrom          string
	PrefTo            string
	SkippedHardFailed string
	FinishReason      string
	MaxTokens         string
	ReasoningTokens   string
	CompletionTokens  string
	Continuations     string
	LKGP              string
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
		LKGP:              h.Get("X-Router-LKGP"),
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
	onCommit          func()

	flusher   http.Flusher
	canFlush  bool
	committed bool
}

func (s *serveStreamState) commit(resp *http.Response, data []byte) error {
	if s.committed {
		return nil
	}
	s.w.WriteHeader(resp.StatusCode)
	if len(data) > 0 {
		if _, werr := s.w.Write(data); werr != nil {
			return werr
		}
		if s.canFlush {
			s.flusher.Flush()
		}
	}
	s.committed = true
	if s.onCommit != nil {
		s.onCommit()
	}
	return nil
}

// forward writes bytes downstream, committing on first write (deferred commit).
func (s *serveStreamState) forward(b []byte, resp *http.Response) error {
	if len(b) == 0 {
		return nil
	}
	return s.commit(resp, b)
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

// pumpResult reports what one upstream segment produced. The last two
// complete SSE events are held back for the stitching decision.
type pumpResult struct {
	held1   []byte
	held2   []byte
	pending []byte
	content string
	tel     StreamTelemetry
	sawDONE bool
}

// pumpSegment reads one upstream SSE response to EOF with prod's fail-fast
// timers, performing SSE PREFLIGHT before downstream commitment: app-error
// events abort retryably, the first meaningful event commits (including all
// buffered bytes), and every subsequent event is forwarded except the last
// two (hold-window for stitching). Uses prod's findSSEEventEnd/inspectSSEEvent.
func pumpSegment(state *serveStreamState, resp *http.Response) (pumpResult, error) {
	defer resp.Body.Close()
	res := pumpResult{}
	requestID := state.requestID

	// Capture routing headers, copy upstream headers, restore ours.
	requestID2 := state.w.Header().Get("X-Request-ID")
	_ = requestID2
	// (Header copy happens once in streamResponse, before the first pump.)

	const maxPreflightBytes = 1 << 20
	preflight := make([]byte, 0, 32*1024)
	scanOffset := 0

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

	idleTimer := time.NewTimer(state.idleTimeout)
	defer idleTimer.Stop()
	firstEventTimer := time.NewTimer(state.firstEventTimeout)
	defer firstEventTimer.Stop()

	log.Printf(
		"streamResponse: fail-fast timers armed for request %s, first_event=%s idle=%s",
		requestID, state.firstEventTimeout, state.idleTimeout,
	)

	for {
		select {
		case <-state.r.Context().Done():
			log.Printf("streamResponse: request context canceled for request %s", requestID)
			return res, state.r.Context().Err()

		case <-idleTimer.C:
			if !state.committed {
				log.Printf(
					"STREAM IDLE TIMEOUT (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, state.idleTimeout,
				)
				return res, errFirstEventTimeout
			}
			log.Printf("STREAM IDLE TIMEOUT FIRED for request %s after %s", requestID, state.idleTimeout)
			state.writeStreamError("stream idle timeout: no data for " + state.idleTimeout.String())
			return res, fmt.Errorf("stream idle timeout: no data for %s", state.idleTimeout)

		case <-firstEventTimer.C:
			if !state.committed {
				log.Printf(
					"FIRST-EVENT TIMEOUT FIRED (pre-commit) for request %s after %s — aborting upstream, will fail over",
					requestID, state.firstEventTimeout,
				)
				return res, errFirstEventTimeout
			}
			idleTimer = time.NewTimer(state.idleTimeout)

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
				if !state.committed {
					// PREFLIGHT: buffer and inspect complete events until a
					// meaningful one commits the response.
					preflight = append(preflight, result.data...)
					for scanOffset < len(preflight) {
						eventEnd := findSSEEventEnd(preflight[scanOffset:])
						if eventEnd < 0 {
							break
						}
						eventEnd += scanOffset

						meaningful, appErr := inspectSSEEvent(preflight[scanOffset:eventEnd])
												if appErr != nil {
													log.Printf(
														"streamResponse: upstream SSE application error request=%s code=%d message=%q",
														requestID, appErr.Code, appErr.Message,
													)
													return res, appErr
												}
												if meaningful {
													// Commit: headers + everything buffered so far.
													if cerr := state.commit(resp, preflight); cerr != nil {
														return res, cerr
													}
													// Re-slice the committed bytes into the hold-window:
													// all complete events except the last two become
													// forwarded; last two stay held.
													absoluteEnd := eventEnd  // already includes scanOffset
													if absoluteEnd > len(preflight) {
														log.Printf(
															"streamResponse: BOUNDS FIX absoluteEnd=%d len(preflight)=%d — clamping",
															absoluteEnd, len(preflight),
														)
														absoluteEnd = len(preflight)
													}
													splitIntoHold(state, &res, preflight[:absoluteEnd])
													rest := preflight[absoluteEnd:]
													preflight = preflight[:0]
													scanOffset = 0
													res.pending = append(res.pending, rest...)
													break
												}
						scanOffset = eventEnd
					}
					if !state.committed && len(preflight) >= maxPreflightBytes {
						log.Printf(
							"streamResponse: preflight cap reached request=%s bytes=%d; committing opaque stream",
							requestID, len(preflight),
						)
						if cerr := state.commit(resp, preflight); cerr != nil {
							return res, cerr
						}
						splitIntoHold(state, &res, preflight)
						preflight = preflight[:0]
						scanOffset = 0
					}
				} else {
					// Post-commit: append to pending and split events out.
					res.pending = append(res.pending, result.data...)
					for {
						_, consumed := splitSSEvent(res.pending)
						if consumed == 0 {
							break
						}
						evCopy := append([]byte(nil), res.pending[:consumed]...)
						res.pending = res.pending[consumed:]
						processHeldEvent(state, &res, evCopy)
					}
				}
			}

			if result.err == io.EOF {
				if !state.committed && len(preflight) > 0 {
					if cerr := state.commit(resp, preflight); cerr != nil {
						return res, cerr
					}
					splitIntoHold(state, &res, preflight)
				}
				log.Printf("streamResponse: EOF for request %s", requestID)
				return res, nil
			}
			if result.err != nil {
				log.Printf("streamResponse: read error for request %s: %v", requestID, result.err)
				return res, result.err
			}

			idleTimer = time.NewTimer(state.idleTimeout)
			go read()
		}
	}
}

// splitSSEvent splits the first complete SSE event off b (uses prod's
// findSSEEventEnd). Returns the event INCLUDING terminator + bytes consumed.
func splitSSEvent(b []byte) (event []byte, consumed int) {
	end := findSSEEventEnd(b)
	if end < 0 {
		return nil, 0
	}
	return b[:end], end
}

// processHeldEvent folds one complete upstream event into the hold-window:
// forwards everything except the newest two; accumulates telemetry/content.
func processHeldEvent(state *serveStreamState, res *pumpResult, ev []byte) {
	if res.held1 != nil {
		if ferr := state.forward(res.held1, nil); ferr != nil {
			// Downstream write error: drop silently; pump loop's caller
			// observes via resp write errors on next flush.
			log.Printf("streamResponse: downstream write error for request %s: %v", state.requestID, ferr)
		}
	}
	res.held1 = res.held2
	res.held2 = ev
	if payload := ssePayloadOf(ev); payload != nil {
		if bytes.Equal(bytes.TrimSpace(payload), []byte("[DONE]")) {
			res.sawDONE = true
		} else {
			info, _ := parseSSEPayload(payload)
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

// splitIntoHold feeds an already-committed byte region through the hold-window
// (used at preflight-commit time so held semantics stay uniform).
func splitIntoHold(state *serveStreamState, res *pumpResult, data []byte) {
	rest := data
	for {
		ev, consumed := splitSSEvent(rest)
		if consumed == 0 {
			break
		}
		evCopy := append([]byte(nil), ev...)
		rest = rest[consumed:]
		processHeldEvent(state, res, evCopy)
	}
	// Incomplete tail stays pending.
	res.pending = append(res.pending, rest...)
}

// streamResponse serves the client response for one successful route: SSE
// preflight (app-error detection, deferred commit), hold-window event
// forwarding, truncation telemetry (FIX-1), and transparent continuation
// stitching on length-truncation (FIX-2b). Signature preserved from prod.
func streamResponse(
	w http.ResponseWriter,
	resp *http.Response,
	ctx context.Context,
	onCommit func(),
	state *serveStreamState,
) (streamResult, error) {
	state.w = w
	state.onCommit = onCommit
	if state.requestID == "" {
		state.requestID = w.Header().Get("X-Request-ID")
	}
	requestID := state.requestID
	log.Printf("streamResponse: ENTERED for request %s", requestID)

	// Capture routing headers, copy upstream headers, restore ours.
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

		// FIX-2b: stitch on length-truncation — never across tool calls,
		// never when [DONE] is not the last held event.
		if eventFinishReason(seg.held1) == "length" &&
			(!seg.sawDONE || isDONEEvent(seg.held2)) &&
			!tel.SawToolCall &&
			state.continuationMax > 0 &&
			continuations < state.continuationMax {
			contBody, cerr := buildContinuationBody(state.body, content, continuations+1)
			if cerr == nil {
				req2, rerr := buildRequest(state.r, contBody, state.route, state.provider, state.key)
				if rerr == nil {
					resp2, derr := state.client.Do(req2)
					if derr == nil {
						if resp2.StatusCode/100 == 2 {
							continuations++
							log.Printf(
								"continuation #%d for request %s (stitched %d content bytes so far)",
								continuations, requestID, len(content),
							)
							seg, err = pumpSegment(state, resp2)
							continue
						}
						io.Copy(io.Discard, resp2.Body)
						resp2.Body.Close()
					}
				}
			}
			log.Printf("continuation unavailable for request %s — forwarding truncation as-is", requestID)
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
		MaxTokens: func() *int {
			if mt, has := requestMaxTokens(body); has {
				return &mt
			}
			return nil
		}(),
		LKGP: lkgpPinned(preferenceName) == p.Name,
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

	// FIX-1/2b: build the shared stream-serving state.
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
		sres, err = streamResponse(w, resp, r.Context(), func() {
			providerKeySucceeded(p, keyEnv)
			providerSucceeded(p, route.Model, preferenceName)
		}, state)

		if err == nil || sres.Committed {
			// FIX-1: emit stream telemetry once the client response settled.
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

			if sseErr, ok := err.(*sseApplicationError); ok {
				routeOutcome = "transient"
				if sseErr.Code > 0 {
					statusCode = sseErr.Code
				}
				providerFailed(p, route.Model)
				evictLKGP(preferenceName, p.Name) // FIX-3

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
			contBody, cerr := buildContinuationBody(body, content, continuations+1)
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
			promptTokens = info2.PromptTokens
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
	// Body may have been rewritten (stitched) — Content-Length must match.
	w.Header().Set("Content-Length", strconv.Itoa(len(respBody)))

	if continuations > 0 || finishReason != "" {
		e := RouteEvent{
			Event:           "stream_result",
			RequestID:       requestID,
			Provider:        p.Name,
			Model:           route.Model,
			Preference:      preferenceName,
			FinishReason:    finishReason,
			ReasoningTokens: intPtr(reasoningTokens),
			ComplTokens:     intPtr(complTokens),
			Continuations:   continuations,
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

	// FIX-1: pre-flight request telemetry headers (visible on every outcome).
	if mt, has := requestMaxTokens(body); has {
		w.Header().Set("X-Router-Max-Tokens", strconv.Itoa(mt))
	}
	// FIX-3: expose whether this attempt rides the LKGP pin.
	if pin := lkgpPinned(preference.Name); pin != "" {
		w.Header().Set("X-Router-LKGP", pin)
	}

	// Keep cycling until a route succeeds or the client cancels.
	if err := r.Context().Err(); err != nil {
		return
	}
	// Preference fallback state must persist across the outer loop iterations
	preferenceFallback := false
	preferenceFrom := ""
	preferenceTo := ""

	// LKGP (last-known-good-provider): UNVERIFIED — authored but never tested.
	// Neutralized+removed 2026-10-24 (was: type error RouteSpec, zero test
	// coverage, and its route-slice hoist caused the preference-fallback
	// regression). Re-enable only via the test-env orderedPreferenceRoutes
	// implementation after verification. See go-router-infrastructure.
	// NOTE: routes MUST be recomputed inside the outer loop — `preference`
	// advances on preference_fallback; a hoisted slice re-walks the old
	// preference's routes.

	for {
		attempted := false
		fallbackCount := 0
		fallbackFrom := ""
		skippedHardFailed := false

		// FIX-3: LKGP ordering — pinned provider's route first, ladder intact.
		// Recomputed per outer-loop iteration (preference advances on fallback).
		for _, route := range orderedPreferenceRoutes(preference) {
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
							lkgpMu.Lock()
							lkgp[preference.Name] = p.Name
							lkgpMu.Unlock()
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
		// Return 503 immediately and let background recovery continue.
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
	FinishReason    string `json:"finish_reason,omitempty"`
	MaxTokens       *int   `json:"max_tokens,omitempty"`
	ReasoningTokens *int   `json:"reasoning_tokens,omitempty"`
	ComplTokens     *int   `json:"completion_tokens,omitempty"`
	Continuations   int    `json:"continuations,omitempty"`
	LKGP            bool   `json:"lkgp,omitempty"`
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
