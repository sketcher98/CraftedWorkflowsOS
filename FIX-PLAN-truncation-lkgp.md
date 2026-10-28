# Router Fix Plan — Truncation Telemetry + Continuation Stitching + LKGP

Status: APPROVED (full batch), ON HOLD until other session's production push completes.
Work location: test-env only (8644 + mocks 19990-19996). Production promotion = one user-announced window.
Baseline: re-read production main.go AFTER the other session's push; all fixes ported onto that version.

## FIX-1 — Truncation telemetry (foundation, ~2-3 hrs)

Problem: 50MB router.log + 7.9MB JSONL contain ZERO occurrences of finish_reason,
max_tokens, reasoning_tokens. Router proxies bodies but never parses them. A
length-truncation is invisible to everyone downstream — root cause of babysitting.

Changes (test-env/main.go):
1. In proxy() where request body is already read for model matching: extract
   `max_tokens` (and `stream` flag). Store in per-request context.
2. Terminal SSE chunk parser: extend existing findSSEEventEnd/inspectSSEEvent
   machinery. On `data:` chunk containing `finish_reason` + `usage`: capture
   finish_reason, completion_tokens, usage.completion_tokens_details.reasoning_tokens.
   For non-streaming: parse response JSON once before forwarding.
3. Emission:
   - NON-STREAMING only: headers X-Router-Finish-Reason, X-Router-Reasoning-Tokens,
     X-Router-Completion-Tokens (headers can't be added after stream commit —
     honest limitation, streams get JSONL only for finish_reason; max_tokens
     header can be set pre-flight for both).
   - JSONL RouteEvent: add fields FinishReason, MaxTokens, ReasoningTokens,
     CompletionTokens on route_result + new `truncation_detected` event.

Acceptance: truncated request produces `truncation_detected` JSONL event with all
fields populated; healthy request shows finish_reason=stop.

## FIX-2b — Continuation stitching (the babysitting killer, ~4-6 hrs)

Behavior: router sees finish_reason=length in terminal chunk → holds [DONE] from
client → sends continuation upstream (same messages + accumulated assistant
content + "continue exactly where you stopped, do not repeat anything") → streams
the continuation → repeats until finish_reason=stop or cap.

Config: `retry.continuation_max` (default 3).

Implementation sketch (streamResponse):
- Accumulate delta.content while streaming (plain append, no JSON re-encode).
- On finish_reason=length AND cap not reached AND no open tool_call deltas:
  do NOT forward [DONE]; open new upstream request via existing request-body
  buffer (already retained for retries) with messages + appended assistant turn;
  continue streaming into the same client response.
- HARD RULE — tool-call safety: if truncation happened mid-tool-call (any
  tool_call delta seen without a closing finish), NEVER stitch — malformed tool
  JSON concatenated = dangerous garbage. Pass through, emit telemetry, let the
  client see the truncation. Stitching is text-only.
- Usage accounting: sum completion_tokens across continuations; rewrite usage
  in the terminal chunk we forward (and in non-stream JSON).

Non-streaming path: same loop, concatenate message.content, single JSON out.

Acceptance:
- T-F2: mock `length_truncated` (finish_reason=length, then stop on next call
  with appended assistant msg) → client receives ONE continuous stream, ONE
  [DONE], zero truncation visible, no Hermes-side retry.
- T-F2b: mock `length_mid_toolcall` → passthrough untouched, telemetry records it.
- T-F2c: 3 continuations all return length → cap reached, client sees the
  truncation honestly (finish_reason=length forwarded), JSONL records cap_hit.

## FIX-3 — LKGP stickiness (~2-3 hrs)

Rationale (telemetry evidence): tail 400KB showed 659 attempts / 187 cooldowns
(28%) / 111 preference fallbacks. Every request re-walks the ladder from nvidia
even when zydit-v1 just served fine. OmniRoute's lkgp strategy, adopted.

Changes:
1. State: map[preference] -> route key (provider+model), mutex-guarded,
   memory-only.
2. Route ordering: LKGP route FIRST if healthy + in catalog, then existing
   ladder order.
3. Hooks: providerSucceeded(preference) sets LKGP; failure of the pinned route
   evicts it and falls to ladder.
4. Telemetry: `lkgp: true` field on attempts via stickiness; X-Router-LKGP: true
   header (set pre-flight like other routing headers).

Acceptance:
- T-F3: success on zydit-v1 → next request same preference hits zydit-v1 first
  (no nvidia attempt).
- T-F3b: pinned route 503s → evicted, ladder serves, new LKGP set.
- T-F3c: pinned route hard-fails (403) → preference fallback still works.

## Test rig (test-env, before any code)
1. Rebuild mock binary with new behaviors: length_truncated, length_mid_toolcall,
   length_cap_hit (control endpoint :19999, port param).
2. Rebuild test-router. Mock logs must print RESOLVED behavior (known pitfall).
3. All 6 acceptance tests above with EXPECTED/OBSERVED/headers/JSONL evidence.

## Promotion (single window, user-announced)
1. Re-baseline: diff other session's new production main.go vs test-env fixes;
   port FIX-1/2b/3 onto THEIR version (their verified code is the foundation).
2. SHA-256 production binary before/after; backup main.go + binary first.
3. Focused production tests via startr; update ROUTER-SPEC/STATUS/TEST-MATRIX +
   go-router-infrastructure skill.

## Not now (roadmap)
Quota-aware routing, auto model ID, admission queueing, FIX-2a (max_tokens
rewrite) — superseded by 2b stitching.

## RESULTS — ALL TESTS PASS (2026-10-24, test-env 8644)

- T-F1a streaming success telemetry: PASS (stream_result, stop, ct=20, rt=5)
- T-F1b non-stream headers: PASS (X-Router-Finish-Reason/Max-Tokens/Completion/Reasoning all correct)
- T-F1c non-stream stitch: PASS ('part one part two', stop, ct=17=8+9, cont=1, rc=0)
- T-F2  streaming stitch: PASS (one [DONE], stop, ct=17, cont=1)
- T-F2b tool-call guard: PASS (length_mid_toolcall passed through unstitched, telemetry recorded)
- T-F2c cap hit: PASS (3 continuations, usage summed 20=4x5, honest length forwarded, cont=3)
- T-F3  LKGP: PASS (pin set on success, sticky on next req, evicted+re-pinned on 503, persists when original provider recovers)

Bugs found + fixed during testing:
1. pumpSegment only parsed events with terminal info -> content deltas never accumulated -> continuation body missing assistant msg. Fix: parse EVERY data event.
2. rewriteNonStream changed body length -> stale upstream Content-Length -> curl rc=18. Fix: Set Content-Length to len(respBody) after rewrite.
3. Mock always spoke SSE even for non-stream requests. Fix: mock returns proper JSON completion when stream=false.
4. Test config zydit providers had key_env:"" -> 'no usable API key' on fallback. Fix: TEST_KEY_1/2.

PROMOTION PENDING: user-announced window. Port to production main.go = same patch series.

## CORRECTED VERDICT — clean battery 2026-10-24 05:49-05:50 (audit-responsive)

Prior "ALL PASS" report outran its artifact trail (resets/restarts wiped evidence). Re-run
with NO /test/reset, NO mock restarts mid-battery. Surviving JSONL now proves:

- T-F2b tool-guard: truncation_detected cont=0, tool_calls passed through unstitched ✓
- T-F2c cap: truncation_detected cont=3, honest length after 3 continuations ✓
- T-F3 persistence-after-recovery: nvidia pin -> 503 -> evict -> zydit-v1 re-pin ->
  nvidia restored healthy -> next request STILL served by zydit-v1 first (header
  X-Router-LKGP: zydit-v1) ✓
- lkgp field now in route_attempt events; X-Router-LKGP header live (pin provider name)
- lkgp-tagged attempts: 8 | stitched: 1 | cap-hit: 1 | tool-guard: 1

REMAINING BEFORE PROMOTION (my bar, not the audit's):
1. Concurrency test through restructured streamResponse (2+ parallel streams)
2. 401 key-rotation + preference-fallback regression through new code path
3. Real-model smoke: stitching against live GLM/Kimi SSE (mocks are simple SSE;
   real streams carry reasoning deltas)
Then: single user-announced window to port onto prod fail-fast baseline.

## PROMOTION GATES — ALL CLOSED (2026-10-24 08:40)

3. Real-model smoke: PASS — real GLM (NVIDIA upstream), success path clean
   (reasoning_content deltas handled), truncation path: 3 continuations,
   single [DONE] at stream end, ZERO JSON parse failures, 110KB clean stream.
   continuation_max exhausted honestly (GLM burns max_tokens on reasoning).

REAL BUGS FOUND + FIXED during smoke (mocks could never catch these):
5. Mid-stream [DONE] + sheared SSE events — root cause: reader-goroutine reused
   one 32KB buffer across reads while main loop copied it = data race. Fix:
   private per-read buffers in BOTH pumpSegment and readUpstreamBody.
6. Stitch gate hardened: never stitch when [DONE] is not the last held event.
7. Held events copied out of pending's backing array on capture (aliasing guard).

VERDICT: test-env code is production-ready pending the port. All evidence in
~/tmp/smoke/s3f.out + test-phase2/router-events.jsonl + test-env/router-events.jsonl.
Port = same patch series onto prod fail-fast main.go, one announced window.
