package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"sort"
	"sync"
	"time"
)

// ExactMatchCache is a deterministic direct-hash response cache (Bifrost
// semanticcache direct path + LiteLLM canonical param hash). Non-stream
// requests only; TTL-bounded; in-memory.

type CacheEntry struct {
	Body      []byte
	Status    int
	StoredAt  time.Time
	ExpiresAt time.Time
}

type ExactMatchCache struct {
	mu    sync.RWMutex
	items map[string]*CacheEntry
	cap   int
	ttl   time.Duration
}

func NewExactMatchCache(cap int, ttl time.Duration) *ExactMatchCache {
	if cap <= 0 {
		cap = 512
	}
	if ttl <= 0 {
		ttl = 60 * time.Second
	}
	return &ExactMatchCache{
		items: make(map[string]*CacheEntry),
		cap:   cap,
		ttl:   ttl,
	}
}

// cacheKey builds a deterministic hash from the payload's canonical JSON:
// deep-sorted keys, model + messages + params. Never includes router-owned
// fields (stream, request id, etc.).
func cacheKey(payload map[string]interface{}) string {
	p := canonicalize(payload)
	b, _ := json.Marshal(p)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func canonicalize(v interface{}) interface{} {
	switch t := v.(type) {
	case map[string]interface{}:
		out := make(map[string]interface{}, len(t))
		for k, val := range t {
			switch k {
			case "stream", "stream_options", "request_id", "user":
				continue // router-owned / non-cacheable fields
			}
			out[k] = canonicalize(val)
		}
		return out
	case []interface{}:
		out := make([]interface{}, len(t))
		for i, val := range t {
			out[i] = canonicalize(val)
		}
		return out
	default:
		return v
	}
}

// Get returns a live entry if present and unexpired. Expired entries are
// lazily deleted (Bifrost lazy-TTL pattern).
func (c *ExactMatchCache) Get(key string) (*CacheEntry, bool) {
	c.mu.RLock()
	e, ok := c.items[key]
	c.mu.RUnlock()
	if !ok {
		return nil, false
	}
	if time.Now().After(e.ExpiresAt) {
		c.mu.Lock()
		delete(c.items, key)
		c.mu.Unlock()
		return nil, false
	}
	return e, true
}

// Put stores an entry, evicting the oldest expiry when over cap.
func (c *ExactMatchCache) Put(key string, body []byte, status int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := time.Now()
	c.items[key] = &CacheEntry{
		Body:      body,
		Status:    status,
		StoredAt:  now,
		ExpiresAt: now.Add(c.ttl),
	}
	if len(c.items) <= c.cap {
		return
	}
	// Evict earliest expiration (Bifrost in_memory_cache expiration heap).
	oldestKey := ""
	var oldest time.Time
	for k, e := range c.items {
		if oldestKey == "" || e.ExpiresAt.Before(oldest) {
			oldestKey = k
			oldest = e.ExpiresAt
		}
	}
	if oldestKey != "" {
		delete(c.items, oldestKey)
	}
}

// Keys returns the current cache keys (diagnostics).
func (c *ExactMatchCache) Keys() []string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	keys := make([]string, 0, len(c.items))
	for k := range c.items {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// teeWriter passes every write through to the real ResponseWriter while
// capturing the body + status for non-stream success caching.
type teeWriter struct {
	http.ResponseWriter
	buf    bytes.Buffer
	status int
}

func (t *teeWriter) WriteHeader(code int) {
	t.status = code
	t.ResponseWriter.WriteHeader(code)
}

func (t *teeWriter) Write(b []byte) (int, error) {
	if t.status >= 200 && t.status < 300 {
		t.buf.Write(b)
	}
	return t.ResponseWriter.Write(b)
}
