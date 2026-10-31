package main

import (
	"testing"
	"time"
)

// ---- failure classifier ladder (Bifrost failureclass_test.go:308 style) ----

func TestFailureClassLadder(t *testing.T) {
	cases := []struct {
		status int
		body   string
		want   FailureClass
	}{
		{200, "", FailureSuccess},
		{201, "", FailureSuccess},
		{500, "", FailureTransient},
		{502, "", FailureTransient},
		{504, "", FailureTransient},
		{529, "", FailureTransient},
		{429, "rate limit reached", FailureRateLimit},
		{429, "insufficient_quota", FailureQuota},
		{402, "", FailureQuota},
		{401, "invalid_api_key", FailureCredential},
		{401, "unauthorized", FailureCredential},
		{403, "model_access_denied", FailureModelAccess},
		{403, "not available in your region", FailureRegionBlocked},
		{404, "model_not_found", FailureModelGone},
		{404, "{}", FailureModelGone},
		{408, "", FailureTimeout},
		{400, "bad request json", FailureCallerFault},
		{413, "", FailureCallerFault},
		{422, "", FailureCallerFault},
		{409, "", FailureCallerFault},
		{418, "teapot", FailureCallerFault},
		{300, "", FailureTransient}, // unexpected 3xx → transient
	}
	for _, c := range cases {
		got := classifyFailureBody(c.status, c.body)
		if got != c.want {
			t.Errorf("classifyFailureBody(%d, %q) = %v, want %v", c.status, c.body, got, c.want)
		}
	}
}

func TestFailureClassRetryable(t *testing.T) {
	ok := map[FailureClass]bool{
		FailureTransient: true, FailureRateLimit: true, FailureQuota: true,
		FailureTimeout: true,
		FailureCredential: true, // key rotation path
		FailureCallerFault: false, FailureModelGone: false,
		FailureModelAccess: false, FailureRegionBlocked: false,
		FailureSuccess: false, FailureCancelled: false,
	}
	for cls, want := range ok {
		if cls.retryable() != want {
			t.Errorf("retryable(%v) = %v, want %v", cls, cls.retryable(), want)
		}
	}
}

// ---- Retry-After parser (LiteLLM utils.py:8210 style) ----

func TestParseRetryAfter(t *testing.T) {
	if got := parseRetryAfter(""); got != 0 {
		t.Errorf("empty = %d, want 0", got)
	}
	if got := parseRetryAfter("30"); got != 30 {
		t.Errorf("30 = %d, want 30", got)
	}
	if got := parseRetryAfter("9999"); got != 60 {
		t.Errorf("9999 = %d, want clamped 60", got)
	}
	if got := parseRetryAfter("-5"); got != 0 {
		t.Errorf("-5 = %d, want 0", got)
	}
	if got := parseRetryAfter("Wed, 21 Oct 2026 07:28:00 GMT"); got > 60 || got < 0 {
		t.Errorf("HTTP-date = %d, want 0..60", got)
	}
	if got := parseRetryAfter("not-a-date"); got != 0 {
		t.Errorf("garbage = %d, want 0", got)
	}
}

// ---- exact-match cache (Bifrost direct-hash + in_memory TTL) ----

func TestCacheKeyDeterministicAndCanonical(t *testing.T) {
	a := map[string]interface{}{
		"model": "m3", "messages": []interface{}{
			map[string]interface{}{"role": "user", "content": "hi"},
		},
		"stream": true, // router-owned, must be excluded
	}
	b := map[string]interface{}{
		"messages": []interface{}{
			map[string]interface{}{"content": "hi", "role": "user"},
		},
		"model": "m3", "stream": false,
	}
	if cacheKey(a) != cacheKey(b) {
		t.Errorf("canonical keys differ: %s vs %s", cacheKey(a), cacheKey(b))
	}
}

func TestCacheHitExpiryEviction(t *testing.T) {
	c := NewExactMatchCache(2, 50*time.Millisecond)
	body := []byte(`{"ok":true}`)
	c.Put("k1", body, 200)
	c.Put("k2", body, 200)
	if _, ok := c.Get("k1"); !ok {
		t.Fatal("k1 should hit")
	}
	c.Put("k3", body, 200) // evicts oldest expiry
	if len(c.Keys()) > 2 {
		t.Errorf("cap exceeded: %v", c.Keys())
	}
	time.Sleep(60 * time.Millisecond)
	if _, ok := c.Get("k3"); ok {
		t.Error("k3 should have expired")
	}
}

// ---- weighted key pool rotation (Bifrost weightedrandom + used/dead) ----

func TestSelectProviderKeyRotationAndExhaustion(t *testing.T) {
	p := &Provider{KeyEnv: "K1", KeyEnvs: []string{"K1", "K2", "K3"}}
	t.Setenv("K1", "k1")
	t.Setenv("K2", "k2")
	t.Setenv("K3", "k3")

	used := map[string]bool{}
	seen := map[string]bool{}
	for i := 0; i < 3; i++ {
		env, key, ok := selectProviderKey(p, used)
		if !ok {
			t.Fatal("healthy key expected")
		}
		seen[env] = true
		if key == "" {
			t.Errorf("empty key for %s", env)
		}
	}
	if len(seen) != 3 {
		t.Errorf("rotation should visit all 3 keys, saw %v", seen)
	}
	// All used → pool resets, still serves
	env, _, ok := selectProviderKey(p, used)
	if !ok || env == "" {
		t.Errorf("pool reset should still serve, got %q ok=%v", env, ok)
	}
	// All keys cooling → nothing healthy
	for _, e := range []string{"K1", "K2", "K3"} {
		p.mu.Lock()
		if p.keys == nil {
			p.keys = map[string]*KeyHealth{}
		}
		p.keys[e] = &KeyHealth{CooldownTil: time.Now().Add(time.Hour)}
		p.mu.Unlock()
	}
	if _, _, ok := selectProviderKey(p, map[string]bool{}); ok {
		t.Error("all-cooled pool should return not-ok")
	}
}

// ---- sliding-window budget (OmniRoute 2-bucket, LiteLLM refund) ----

func TestWindowCounterReserveRefund(t *testing.T) {
	w := NewWindowCounter(60 * time.Second)
	if got := w.Reserve(1); got != 1 {
		t.Errorf("reserve(1) = %d, want 1", got)
	}
	if got := w.Value(); got != 1 {
		t.Errorf("value = %d, want 1", got)
	}
	w.Refund(1)
	if got := w.Value(); got != 0 {
		t.Errorf("after refund = %d, want 0", got)
	}
}

// ---- health check no-op classes ----

func TestSuccessClassNotTransientPermanent(t *testing.T) {
	cls := classifyFailureBody(200, "")
	if cls.retryable() {
		t.Error("success must not be retryable")
	}
}
