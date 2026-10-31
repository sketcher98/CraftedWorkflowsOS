# key_accounts — the account-layout declaration

`key_accounts` is an OPTIONAL per-provider field aligned 1:1 with
`key_envs`. It tells the router (and the TUI) whether a provider's keys
belong to one account or several. The router itself does not use it for
routing — it exists so the UI can show an honest disclaimer instead of
guesswork.

## Values

- One account: `["acct-A", "acct-A", "acct-A"]`
  → TUI: "Keys share one account — spreading them is resilience, not capacity."
- Several accounts: `["acct-A", "acct-B", "acct-C"]`
  → TUI: "Keys span accounts — rotation can expand capacity."
- Omitted / wrong length (validation rejects wrong length):
  → TUI: "Account layout not declared — treating keys as same-account (safe default)."

## Why it matters

- Same-account keys share quota and rate limits. Balancing them spreads
  load but does NOT increase capacity — it buys resilience (a revoked or
  throttled key does not kill the provider).
- Different-account keys are independent entitlements: rotation can
  actually multiply available capacity.

## Current providers (as of 2026-10-31)

| Provider | key_envs | Account layout |
|----------|----------|----------------|
| nvidia | NVIDIA_API_KEY, NVIDIA_API_KEY_2, NVIDIA_API_KEY_3 | **UNKNOWN** — 3 keys with distinct hashes, but whether they belong to one account or several has not been confirmed. Do NOT invent an answer. |
| zydit-v1..v4 | 3 env names each | Effectively SAME — the three env values are byte-identical copies of one key. |

## Future setup wizard

The setup wizard (in development) will ask a plain-language question per
provider — "are your keys from one account or different ones?" — and
write `key_accounts` from the answer. That is where the truth gets
captured, not by static guessing.
