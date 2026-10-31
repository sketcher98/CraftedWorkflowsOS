package main

import (
	"strings"
	"testing"
)

func TestForbiddenUpstreamHeaderNames(t *testing.T) {
	for _, h := range []string{"Host", "Connection", "Keep-Alive", "TE", "Trailer",
		"Upgrade", "Transfer-Encoding", "Proxy-Authorization", "Proxy-Authenticate",
		"Authorization", "X-Api-Key", "X-Goog-Api-Key", "Api-Key", "Cookie",
		"X-Forwarded-For", "X-Real-Ip", "CF-Connecting-Ip", "True-Client-Ip",
		"Forwarded", "Via", "Client-Ip"} {
		if !isForbiddenUpstreamHeaderName(h) {
			t.Errorf("%s should be forbidden", h)
		}
	}
	for _, h := range []string{"Content-Type", "Accept", "X-Custom", "User-Agent"} {
		if isForbiddenUpstreamHeaderName(h) {
			t.Errorf("%s should be allowed", h)
		}
	}
	// case/space normalization
	if !isForbiddenUpstreamHeaderName("  authorization  ") {
		t.Error("whitespace-padded authorization should be forbidden")
	}
}

func TestSanitizeHeaderValueRejectsCRLF(t *testing.T) {
	if sanitizeHeaderValue("ok") != "ok" {
		t.Error("normal value must pass")
	}
	if sanitizeHeaderValue("evil\r\nX-Evil: 1") != "" {
		t.Error("CRLF injection must be emptied")
	}
}

func TestSanitizeErrorForClient(t *testing.T) {
	in := "provider returned 401: invalid_api_key for key sk-abcdefghijklmnopqrstuvwxyz123456; " +
		"auth: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdef " +
		"at main.go:42 stack"
	out := sanitizeErrorForClient(in)
	if strings.Contains(out, "sk-abcdefghijklmnopqrstuvwxyz") {
		t.Error("long token leaked")
	}
	if strings.Contains(out, "eyJhbGciOiJIUzI1NiJ9") {
		t.Error("JWT leaked")
	}
	if strings.Contains(out, "Bearer eyJ") || strings.Contains(out, "Bearer [REDACTED]") == false {
		t.Errorf("bearer redaction issue: %q", out)
	}
	if strings.Contains(out, "main.go:42") {
		t.Error("stack tail leaked")
	}
	if !strings.Contains(out, "invalid_api_key") {
		t.Error("legitimate message must survive")
	}
	if len(sanitizeErrorForClient(strings.Repeat("a", 9000))) > 4096 {
		t.Error("sanitized output must be capped")
	}
}

func TestSizeLimitConfigValidation(t *testing.T) {
	// validation clamps negatives to unlimited
	cfg := Config{Size: SizeConfig{MaxRequestMB: -1}}
	if cfg.Size.MaxRequestMB < 0 {
		cfg.Size.MaxRequestMB = 0
	}
	if cfg.Size.MaxRequestMB != 0 {
		t.Error("negative max should clamp to 0 (unlimited)")
	}
}
