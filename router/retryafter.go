package main

import (
	"net/http"
	"strconv"
	"strings"
	"time"
)

// parseRetryAfter parses a Retry-After header: integer seconds OR HTTP-date
// (RFC 7231). Returns 0 when absent/unparseable. Values are sanitized to
// [0, 60]s per LiteLLM's box (utils.py:8210-8267) — absurd upstream values
// must not poison our cooldowns.
func parseRetryAfter(header string) int {
	h := strings.TrimSpace(header)
	if h == "" {
		return 0
	}
	if secs, err := strconv.Atoi(h); err == nil {
		return clampRetryAfter(secs)
	}
	if t, err := http.ParseTime(h); err == nil {
		d := int(time.Until(t).Seconds())
		if d < 0 {
			d = 0
		}
		return clampRetryAfter(d)
	}
	return 0
}

func clampRetryAfter(secs int) int {
	if secs < 0 {
		return 0
	}
	if secs > 60 {
		return 60
	}
	return secs
}
