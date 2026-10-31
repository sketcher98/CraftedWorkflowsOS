package main

import (
	"regexp"
	"strings"
)

// isForbiddenUpstreamHeaderName implements the canonical upstream denylist
// (OmniRoute upstreamHeaders.ts:12-70): hop-by-hop, origin-IP disclosure, and
// any auth-bearing header. Names are normalized (trim + lowercase) before
// compare. The router's own Authorization is added AFTER copying, so it is
// never stripped.
func isForbiddenUpstreamHeaderName(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	switch n {
	case "host", // replaced by URL host
		"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
		"te", "trailer", "upgrade", "transfer-encoding", // hop-by-hop RFC 7230 §6.1
		"authorization", "x-api-key", "x-goog-api-key",
		"api-key", "cookie", // auth-bearing
		"x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
		"x-real-ip", "forwarded", "via", "cf-connecting-ip", "true-client-ip",
		"client-ip": // origin-IP disclosure
		return true
	}
	return false
}

// sanitizeHeaderValue rejects CR/LF injection in copied header values.
func sanitizeHeaderValue(v string) string {
	if strings.ContainsAny(v, "\r\n") {
		return ""
	}
	return v
}

// credentialPatterns mirror OmniRoute errorSanitization.ts redactions:
// strong token patterns, labeled credential assignments, and URL userinfo.
var (
	reTokenPattern   = regexp.MustCompile(`(?i)(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|xoxb-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,})`)
	reCredLabel      = regexp.MustCompile(`(?i)(api_key|apikey|token|password|secret|access_key|client_secret)\s*[=:]\s*[^\s,;]+`)
	reBearerVal      = regexp.MustCompile(`(?i)bearer\s+[A-Za-z0-9._~+/=-]+`)
	rePEMBlock       = regexp.MustCompile(`-----BEGIN [A-Z ]+-----[\s\S]*?-----END [A-Z ]+-----`)
	reURLUserInfo    = regexp.MustCompile(`(?i)(https?://)[^/\s:@]+:[^/\s@]+@`)
	reQuerySensitive = regexp.MustCompile(`(?i)([?&](?:sig|token|key|awsaccesskeyid|apikey|secret)=)[^&\s]+`)
	reStackTail      = regexp.MustCompile(`(?m)(\n\s*at\s+[\w.$/\\]+\s*\(.+?:[0-9]+\)|(?:^|\s)at\s+\S+\.go:[0-9]+|\n\s+File ".*", line \d+|\n\s+goroutine \d+ \[.*\])`)
)

// sanitizeErrorForClient redacts anything secret-looking and caps length.
// The raw internal error is never replaced — this runs only on text that
// leaves the process.
func sanitizeErrorForClient(s string) string {
	if len(s) > 4096 {
		s = s[:4096]
	}
	s = reStackTail.ReplaceAllString(s, "")
	s = reTokenPattern.ReplaceAllString(s, "[REDACTED]")
	s = reBearerVal.ReplaceAllString(s, "Bearer [REDACTED]")
	s = rePEMBlock.ReplaceAllString(s, "[REDACTED PEM]")
	s = reURLUserInfo.ReplaceAllString(s, "${1}[REDACTED]@")
	s = reQuerySensitive.ReplaceAllString(s, "${1}[REDACTED]")
	s = reCredLabel.ReplaceAllString(s, "${1}=[REDACTED]")
	return s
}
