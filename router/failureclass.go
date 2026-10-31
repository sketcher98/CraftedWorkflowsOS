package main

import (
	"net/http"
	"strings"
)

// FailureClass is the merged Bifrost/LiteLLM taxonomy: every upstream status
// or transport error maps to exactly one class which decides retry vs key
// rotation vs fallback vs hard-fail.
type FailureClass int

const (
	FailureUnknown FailureClass = iota
	FailureSuccess
	FailureTransient      // 5xx, network, DNS, reset
	FailureRateLimit      // 429 + Retry-After
	FailureQuota          // 402 / quota markers (OpenRouter-style)
	FailureCredential     // 401 / key invalid
	FailureModelAccess    // 403 model-level denial
	FailureModelGone      // 404 model not found
	FailureCallerFault    // 400/409/413/415/422 + provider body (client's fault)
	FailureTimeout        // 408 / client timeout
	FailureCancelled      // client disconnected
	FailureRegionBlocked   // 451 / geo markers
)

func (f FailureClass) String() string {
	switch f {
	case FailureSuccess:
		return "success"
	case FailureTransient:
		return "transient"
	case FailureRateLimit:
		return "rate_limit"
	case FailureQuota:
		return "quota"
	case FailureCredential:
		return "credential"
	case FailureModelAccess:
		return "model_access"
	case FailureModelGone:
		return "model_gone"
	case FailureCallerFault:
		return "caller_fault"
	case FailureTimeout:
		return "timeout"
	case FailureCancelled:
		return "cancelled"
	case FailureRegionBlocked:
		return "region_blocked"
	}
	return "unknown"
}

// transientServerStatusCodes: exact list from Bifrost failureclass.go:24.
var transientServerStatusCodes = map[int]bool{
	500: true, 502: true, 503: true, 504: true, 529: true,
}

// rateLimitStatusCodes: 429; quota markers from Bifrost quotaMarkers:56.
var rateLimitStatusCodes = map[int]bool{
	429: true,
}

var quotaMarkers = []string{
	"insufficient_quota", "quota_exceeded", "quota exceeded",
	"billing_hard_limit_reached", "out_of_quota", "usage_limit_reached",
	"resource_exhausted", "402",
}

var credentialStatusCodes = map[int]bool{
	401: true,
}

var credentialMarkers = []string{
	"invalid_api_key", "invalid api key", "unauthorized",
	"authentication_error", "api_key_invalid", "invalid_token",
	"permission_denied", "invalid x-api-key", "incorrect api key",
	"apikey not found", "invalid key",
}

var modelAccessStatusCodes = map[int]bool{
	403: true,
}

var modelAccessMarkers = []string{
	"model_access_denied", "access_denied", "not_allowed",
	"model_not_allowed", "forbidden", "no_permission",
}

var modelGoneMarkers = []string{
	"model_not_found", "model not found", "unknown model",
	"invalid_model", "model_does_not_exist", "not_found",
	"no such model",
}

var callerFaultStatusCodes = map[int]bool{
	400: true, 409: true, 413: true, 415: true, 422: true,
}

var regionBlockedStatusCodes = map[int]bool{
	451: true,
}

var regionBlockedMarkers = []string{
	"region", "geo", "not available in your region", "country",
	"unsupported_region", "blocked in",
}

// classifyFailureBody returns the FailureClass for a status + optional body.
// The body is only consulted for 4xx classification where a provider body
// distinguishes caller-fault from model/credential/region errors.
func classifyFailureBody(status int, body string) FailureClass {
	if status >= 200 && status < 300 {
		return FailureSuccess
	}
	low := strings.ToLower(body)
	hasMarker := func(markers []string) bool {
		for _, m := range markers {
			if strings.Contains(low, m) {
				return true
			}
		}
		return false
	}

	if transientServerStatusCodes[status] {
		return FailureTransient
	}
	if status == http.StatusServiceUnavailable && hasMarker(quotaMarkers) {
		return FailureQuota
	}
	if rateLimitStatusCodes[status] {
		// 429 with a quota marker is a quota block, not a plain rate limit.
		if hasMarker(quotaMarkers) {
			return FailureQuota
		}
		return FailureRateLimit
	}
	if status == http.StatusPaymentRequired { // 402
		return FailureQuota
	}
	if credentialStatusCodes[status] || hasMarker(credentialMarkers) {
		return FailureCredential
	}
	if status == http.StatusForbidden {
		if hasMarker(modelAccessMarkers) {
			return FailureModelAccess
		}
		if hasMarker(regionBlockedMarkers) {
			return FailureRegionBlocked
		}
		return FailureModelAccess
	}
	if status == http.StatusNotFound {
		if hasMarker(modelGoneMarkers) || hasMarker(modelAccessMarkers) {
			return FailureModelGone
		}
		return FailureModelGone
	}
	if status == http.StatusRequestTimeout {
		return FailureTimeout
	}
	if callerFaultStatusCodes[status] {
		return FailureCallerFault
	}
	if status >= 400 && status < 500 {
		return FailureCallerFault
	}
	// Unexpected 2xx/3xx: treat as transient so the route can retry.
	return FailureTransient
}

// retryable reports whether a class merits a retry/fallback attempt.
// Mirrors LiteLLM should_retry: 408/409/429/5xx only — permanent client
// faults (400/401/403/404) never retry. Credential is the one exception:
// a 401 merits a RETRY WITH A DIFFERENT KEY (rotation), not a provider
// fallback — the caller decides which.
func (f FailureClass) retryable() bool {
	switch f {
	case FailureTransient, FailureRateLimit, FailureQuota, FailureTimeout,
		FailureCredential:
		return true
	case FailureModelAccess, FailureModelGone, FailureCallerFault,
		FailureRegionBlocked, FailureSuccess, FailureCancelled:
		return false
	}
	return false
}

// keyRotationEligible: a credential failure may justify trying a different
// key pool; the caller decides based on key count.
func (f FailureClass) keyRotationEligible() bool {
	return f == FailureCredential || f == FailureRateLimit
}
