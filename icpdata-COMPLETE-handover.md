# ~/icpdata — MHC Lead Pipeline: Complete Handover Document
**Every claim below verified by direct file reads on 2026-10-20. Supersedes all earlier drafts.**
Location: `/data/data/com.termux/files/home/icpdata` · Python 3.13 · Termux/Android · X account @PreciousNwosu_
DB: `data/mhc_registry.db` (SQLite; table `mhc_registry`, 47 cols, 99 rows; + `job_runs` ledger)
Git: 7 commits, HEAD `1365103` ("mhc master command + interactive mhc-cron menu")

---

## 1. What this system is

An X/Twitter lead-gen machine for CraftedWorkflows (automation agency, offers: Jumpstart $1,497 / Revenue $3,497 / Ops $4,997/mo). It finds agency founders in pain, scores them 0–100 against the ICP, drafts a personalized DM in one of five psychological "flows," sends at human-safe rates, follows up (DM + email), syncs to Notion, and counts a **reply** as the win. Because X error 476 blocks unverified senders from DMing non-followers, the real front door is **reply-first**: genuine public reply + follow, DM only after follow-back.

---

## 2. Lifecycle: entry → bagged lead

```
NIGHTLY (cron 02:00, jobs.json id 310983658b10) — ONLY enabled job
  nightly_ingest_wrapper → nightly_ingest.run_nightly_batch()
    Phase 1: twscrape (CLI subprocess) × 13 authority follower scrapes
             + 39 pain/growth search queries
    Phase 2: enrichment via twitter_mcp get_user_info (3 concurrent, backoff)
  → mhc_registry rows (score, tags, route, source, tweet text)

CLASSIFY: mhc_classifier.classify() = classify_v5 (BIO-ONLY regex)
  ≥72 auto | 35–71 human | <35 drop
  ⚠ v6 classify_with_evidence exists but is ONLY called by tally_poller
    (Tally forms) and the classifier's own __main__ tests — no X path uses it.

ROUTE: flow_router.route_to_flow() (v2 if evidence present, else v1)
  → one of 5 flows; generate_dm() writes personalized first-touch text

TOUCH:
  auto tier  → auto_sender.main() — 5 DMs/day cap
               1. twitter_validator pre-check (fail → failed_perm)
               2. follows_us() 476 gate — non-follower → dm_flow='awaiting_followback', stays queued
               3. send → on_dm_sent() (registry + Notion "DM Sent")
               errors: classify_failure() → hard(404/user-not-found)=failed_perm never retry
                       soft(429/timeout)=retry · 349/476=privacy_block →
                       privacy_block_fallback(): score≥70 only → follow + like recent tweet
                       + public reply (pitchy canned text — see §6 bug list) → retry DM next run
  human tier → review_cli.py interactive gate: [a]pprove [e]dit($EDITOR) [s]kip [q]uit
               same failure handling incl. fallback
  everyone   → reply_first.py (THE FRONT DOOR): pick trigger tweet (stored URL →
               fuzzy match → best-recent), guards (promo/job/link-only/short tweets),
               draft_reply() pure reaction, post reply + follow, 6/day, 60–90s pacing,
               unique string per batch, deterministic sha256 variants

FOLLOWUP: followup_engine.py — per-flow DM follow-ups on day keys (2/5/9 etc.)
  + email sequences (6 templates; per-flow 5-email chains; days 1/3/5/8/12)
  via Composio GMAIL_SEND_EMAIL (curl JSON-RPC) — ⚠ broken, see §6

SYNC: notion_sync.py → Leads DB (status New → DM Sent → Replied), tracking Task row,
  notion_page_id written back. Wrapper syncs ≤30 unsynced rows per 2h.

WIN: reply detected → reply_at set, Notion "Replied" → human sales conversation.
```

**dm_flow state machine:** NULL → (awaiting_followback | retry | privacy_fallback_pending) → flow_id on success (founder_mirror/permission_direct/value_first/conversational/risk_audit) | failed_perm (terminal) | skipped (human declined). `unpark` resets failed_perm → NULL.

---

## 3. File-by-file (all 30 code files + docs read directly)

### Discovery / ingest
| File | What it actually does |
|---|---|
| `nightly_ingest.py` | Phase 1: `run_twscrape()` shells out to the `twscrape` CLI for 13 authority-follower scrapes (`user_by_login` → `followers <id> --limit 100`) + 39 queries (SEARCH_QUERIES/GROWTH_TRIGGERS/PAIN_REPLIES). Upserts via `ON CONFLICT(handle)`. Phase 2: async enrichment (batches of 10, semaphore 3, 60/120/240s backoff on 429). **Bugs:** dedup step reads `item[9]` on 9-tuples → IndexError whenever twscrape returns any duplicate handle; tuple slots 7–8 (pain_signals/growth_triggers JSON) are hardcoded `None` so the score boost is always +0; "falls back to twitter_mcp" claim is false — failures just return `[]` silently. **Root cause of zero new leads since Sep 14: `twscrape` binary lives only in `~/.hermes/hermes-agent/venv/bin/`, not on PATH → every call fails → wrapper marks "ok" on an empty batch.** |
| `nightly_ingest_wrapper.py` | job_ledger guard (`once_per_day`), marks ok/error. Marks "ok" even when the inner batch produced nothing. |
| `run_nightly_ingest.sh` | `exec python3.13 scripts/nightly_ingest.py` — no venv, no PYTHONPATH, no LD_PRELOAD → also breaks Phase 2's twitter_mcp import under cron. |
| `quick_batch.py` | 19 queries × 20 tweets, sync, INSERT OR IGNORE (note: ignores instead of upserting — stale scores stay). Stores `source_post_url` (fixes reply_first fuzzy matching) but source_post_author is set to the prospect's own handle. |
| `comment_mine_v3.py` | 10 authority SOURCE_HANDLES; `search "from:<handle>"` → `tweet_replies` per tweet; qualifies commenters via `is_qualified_commenter()` + hard-no list; scores via v5 + 2×pain keywords; upsert takes max score, never downgrades route from auto. |
| `manual_trigger.py` | CLI dispatcher (ingest/quick/mine/classify/enrich/auto/followup/review/status/parked/unpark/all). Its own `ingest` runs the same 19 queries inline. `enrich` uses `twscrape user_tweets <handle>` — known-broken (needs numeric id). |

### Classification
| File | What it actually does |
|---|---|
| `mhc_classifier.py` | `classify_v5(bio)`: ~35 weighted regex patterns; firm_owner +4.2, owner +1.6, revenue +1.0, industry +0.8, industry negatives −1.4…−3.5 (recruiting/personal-brand/tech-founder/service-biz/SEO-tool/web3/consultant/saas/company), corporate/dev/product/freelance/personal/student penalties with `CORP_EXEMPT` guard ("founder of X" etc.). Score = `scr*100/4.5` clamped 0–100. `classify_with_evidence()` (v6): adds enriched-profile signals (+2.0 owner headline, +1.5 firm, +1.0 company, +1.5 verified, +0.8 >10k followers), source-authority boosts (KarlSakas 3×0.5…), comment buying signals (+2.5), comment pain (+1.5), comment ownership (+2.0). **`classify()` = v5 bio-only — this is what all X ingest calls.** `__main__` self-tests against `data/labeled_bios.jsonl` (49 labels; prints P/R/F1). |
| `mhc_classifier_v6.py` | Byte-identical duplicate (diff -q: IDENTICAL). Only tally_poller imports it. |
| `account_size.py` | larger (verified or >10k)/similar (≥1k)/smaller (<1k) → strategy table (pre-engage days, opener type, flow hint). **Never imported by any pipeline script** — dead reference module. |
| `twitter_validator.py` | `get_user_info` with 5s timeout; invalid only for not_found/protected/suspended; timeouts optimistic. `validate_handle_sync` juggles event loops (ThreadPoolExecutor + asyncio.run). |
| `data/labeled_bios.jsonl` | 49 hand-labeled bios (label 1 = agency founder). Eval set only. |

### Routing / messaging
| File | What it actually does |
|---|---|
| `flow_router.py` (45KB, v2 since Sept 24) | `extract_context()`: company regex with BAD_COMPANY_PATTERNS (gov/EFCC/FBI/UN… blocklist) + generic-descriptor filter; pain map (8 categories); revenue regex ($Nk/m, N-figure, N+ clients); product extraction from bio lines ("X — Product $Nk/m"); location (flag emojis + city words); tech stack; source angle; authority→flow bias map. `_quote_from_tweet()`: sentence scorer (pain +3, numbers +2, first-person +2, generic −3; needs ≥3). `_h()`/`handle_seed()`: sha256-deterministic variant picks so preview == sent. `_easy_closer()`: **binary-closer doctrine** — A-or-B questions from THEIR signals, never open diagnosis. 5 flows: founder_mirror (empathy + their quote), permission_direct (status respect + permission ask), value_first (reciprocity, pain-anchored open question), conversational (peer tone), risk_audit (loss aversion: "what breaks when you step away?"). `route_to_flow` v2 = keyword scoring per flow w/ evidence boosts (verified +3, >50k followers +2, authority biases, comment-pain boosts); falls back to v1 when no evidence. Also CASE_STUDIES dict (8 fictional-agency proof points — marketing copy, not real clients). `.bak-20260924` = v1 (481-line diff: quote extraction, opener variation, binary closers, product extraction). |
| `lead_magnet.py` | Hardcoded REAL Gumroad inventory scraped 2026-09-11 (29 products: 8 premium, 9 near-free, 12 free magnets with permalinks). `free_product_dm()` template. This exists because of the fabricated-permalink incident — never write product links from memory. |
| `emotion_scheduler.py` | Weekly emotion map (fear Mon/Tue/Thu/Sat, quiet_desperation Wed/Sun, anger Fri) + 6-niche rotation → writes `data/daily_emotion.json`. **Nothing imports/reads that state file — stub.** Also `DB_PATH` points at a `.py` file (unused but a landmine). |

### Sending
| File | What it actually does |
|---|---|
| `auto_sender.py` | Cap 5/day (`date(dm_sent_at)=today`, route=auto). Selection: unsent auto, excludes `failed_%`, prioritizes reply-first'd rows, top score/conf. Per prospect: validator → 476 gate (`follows_us()` via get_user_info, 8s timeout, optimistic on failure) → generate DM via flow_router (bio-only v5 tags) → `send_dm` (twitter_mcp) → `on_dm_sent` (registry + Notion) → pacing sleep 180–360s. `classify_failure()`: privacy_block list (349 strings **and 476** — docstring above it wrongly says 476 is hard-fail; code comment says "NOT a burn… reply-first owns this"), hard (404/page-does-not-exist), soft default. `privacy_block_fallback()`: only score≥70; follow → fetch recent tweets → like + reply (4 canned templates that PITCH CraftedWorkflows — doctrine violation) → `privacy_fallback_pending`, DM retried next run. |
| `auto_sender_wrapper.py` | job_ledger every 2h; on exception alerts Telegram via `hermes send --to telegram:<TELEGRAM_HOME_CHANNEL from ~/.hermes/.env>`; after success alerts hard-fail summary (so "ok" doesn't bury burns). |
| `reply_first.py` | Front door. Candidates: unsent, unreplied, route auto/human, not failed_%, top score. Trigger resolution: stored URL id → fuzzy token-overlap match (≥0.6) against their recent 20 → `pick_best_tweet()` substance bar (≥40 chars, pain/claim/numbers + RELEVANT_KW topicality, skips RTs and their replies). Guards: `_is_promo_tweet` (price+offer words), `_is_job_posting`, `_is_link_only` (<20 chars sans URLs). `draft_reply()`: 5 reaction tiers (empathy for first-person struggle / congrats for launches / spicy-but-not-endorsing for hot takes / number-anchored for analysis / generic honest). sha256 variant picks; batch-unique strings with 4-alternative overflow; reply → capture real reply tweet id from send result → follow → record 4 columns. 6/day, 60–90s pacing. `--dry-run`, `--limit N`. |
| `review_cli.py` | Human gate for route=human, unsent, by score. Full-context display; a/e/s/q; edit via $EDITOR temp file; send path identical to auto_sender incl. fallback; logs every decision. |
| `daily_review.py` | **BROKEN — imports `FLOWS` (doesn't exist; it's `FLOWS_META`) and `find_lead_by_registry_id` (doesn't exist in notion_sync) → crashes on import.** Superseded by review_cli + dashboard; treat as dead code. |

### Follow-up
| File | What it actually does |
|---|---|
| `followup_engine.py` | DM_FOLLOWUPS per flow: founder_mirror {2,5,9}, permission_direct {2,6,10}, value_first {2,4,7}, conversational {1,4,7}, risk_audit {2,5,10}. EMAIL_SEQUENCES: 6 templates (pattern_interrupt, soft_sell, hyper_specific, social_proof, ill_do_it_free, breakup) — several use em dashes and "Scale to 6-figures/month" claims; flow-mapped 5-email chains, triggers [1,3,5,8,12]. Candidates: dm_sent, no reply, not failed_perm. Email gate requires non-empty `email` column (tally leads only) AND then extracts address from bio — disjoint logic; bio-extracted emails bypass the column gate. **Send path is broken twice:** (1) curl `-H "Authorization: Bearer ***"` — literal masked string, auth can never succeed; (2) `dm_sent` counter double-increments on each send. Also sync `asyncio.run()` inside a loop per DM. |
| Sequence docs (5 .md) | content_signal (2-touch), ghosted_recovery (3-touch), hiring_signal (3-touch), risk_audit (3-touch), warm_intro (2-touch). Playbook doctrine: trigger hook → proof → scarcity soft-close, reply keywords ("sprint"/"audit"/"workshop"). **Not wired into followup_engine** — future sequences; trigger tags listed but no code path consumes them. |

### Infrastructure
| File | What it actually does |
|---|---|
| `notion_sync.py` | Direct Notion REST (integration token hardcoded-fallback + env override; 2022-06-28 API version). 5 hardcoded DB ids. Leads props incl. ICP Score number, Flow Assigned select, Status select (New/DM Sent/Replied), "Skipped/Sent" select. `sync_prospect_to_notion()`: find-by-handle → update status or create + tracking task; writes page id back. `on_dm_sent`/`on_reply_received` update Notion. `sync_all_unsynced` scans everything (wrapper does the bounded version). |
| `notion_sync_wrapper.py` | every 2h; syncs ≤30 rows where notion_page_id IS NULL; stamps notion_synced_at. |
| `tally_poller.py` | Tally form 3jrBM1 (8 question ids). Builds pseudo-bio from form answers → **v6** classify → Notion sync + registry upsert (handle=email, email/name/queue_position/tally_submission_id columns). Duplicated function defs ×3 (get_queue_pos) / ×2 (state, NOTION consts) — last def wins; works but sloppy. `main()` is `pass` — library only. |
| `tally_poller_wrapper.py` | `fetch_tally_via_composio()` is literally `pass` (dead). Falls back to direct Tally API needing `TALLY_API_TOKEN` env — never set → every run marks "skipped: no composio". Dead cron. |
| `watchdog.py` | Every 15m: gateway alive = real process (`pgrep -f 'gateway run --force'`, skips bash-source wrappers) AND API 8642 /v1/models answers (200/401). Restart via `gateway-start`, waits ≤120s for bind, Telegram alerts. Then **catch-up runner** re-fires due jobs (nightly_ingest, notion_sync once/day; auto_sender every 2h) by importing wrappers from `~/.hermes/scripts`. |
| `dashboard.py` | Read-only funnel (ingested → replies → DMs → DM replies → Notion), queue states, today's caps (x/6, x/5), recent errors, follow-back watchlist, last 5 touches. The single best "where are we" command. |
| `daily_loop.py` | One command = full day: ingest → reply_first → auto_sender → followups → notion_sync_wrapper → dashboard. Sets PYTHONPATH to twitter-mcp, TWITTER_COOKIES, LD_PRELOAD libpython3.13.so; 30-min/stage timeout; noise-filtered output. **This is the correct env template that run_nightly_ingest.sh lacks.** |
| `job_ledger.py` | `job_runs` table; `should_run(conn, name, every_hours=, once_per_day=)` + `mark_run`. The dedup/self-heal spine. |
| `install_cron.py` | Legacy: installs 10 crontab entries incl. references to **nonexistent scripts** (dream100.py, follow_manager.py) — never use; Hermes jobs.json is the real scheduler. |
| `composio_env.py` | Loads COMPOSIO_API_KEY/USER_ID/MCP_URL from config/composio.env into os.environ (idempotent). |
| `schema_patch.sql` | Additive ALTERs for evidence/enrichment/qualification/notion columns + 4 indexes. |
| CLI (`cli/`) | `mhc` (interactive menu + status/run/review/cron/logs), `mhc-run` (daily_loop), `mhc-review`, `mhc-status` (dashboard), `mhc-logs`, `mhc-cron` (menu; hardcodes the 5 real job ids — verified to match jobs.json — plus enable/disable-all). All export LD_PRELOAD; some also PYTHONPATH/TWITTER_COOKIES. |

### Config/data/logs
| File | What it is |
|---|---|
| `data/mhc_registry.db` | The one DB that matters. |
| `accounts.db` (root) | twscrape account store (1 account). |
| `data/twscrape.db` | 0 bytes, unused. |
| `config/composio.env` | Composio key (secret). |
| `config/mhc_registry.db` | 0 bytes vestigial. |
| `logs/auto_sender_run.log` | Production log; last live run Sep 14 (3 DMs OK). |
| `.gitignore` | pycache/logs/accounts.db/twscrape.db. |

---

## 4. Cron reality (Hermes `~/.hermes/cron/jobs.json`)

| id | Job | Schedule | Enabled |
|---|---|---|---|
| 310983658b10 | MHC Nightly Ingest | 02:00 daily | ✅ **only one live** |
| 0b7e0cfa9d41 | MHC Reply-First Daily | 10:00 | ❌ |
| cd79ed8912db | MHC Auto-Sender (476-gated) | 11:00 | ❌ |
| f3e9e1df8744 | MHC Followup Engine | 12:00 | ❌ |
| 00d7df34ef46 | MHC Notion Sync | every 120m | ❌ |
| 72857d5019d7 | CW SIM overnight monitor | — | ❌ (sim leftovers) |

Note the job ids here are the Hermes job ids; the wrapper scripts use the job_ledger names (nightly_ingest, auto_sender, notion_sync, tally_poller) inside `job_runs`. The `telegram_gate` row in job_runs (`No module named 'telegram_gate_wrapper'`) came from watchdog catch-up importing a wrapper that doesn't exist in ~/.hermes/scripts — harmless but shows watchdog catch-up imports from a different directory than ~/icpdata/scripts.

---

## 5. Verified current state (2026-10-20)

99 leads · 32 auto / 67 human · 20 DMs sent (last Oct 14) · **0 replies** · 16 failed_perm (mostly 476/349) · 3 reply-first actions (Sep 25) · 0 email sequences · notion_sync last ran Oct 14 ("ok"). Nightly ingest "ok" today but newest lead is Sep 14.

---

## 6. Bug list — ranked by pipeline impact (all verified in source)

1. **Ingest is dead: `twscrape` not on PATH.** Binary is at `~/.hermes/hermes-agent/venv/bin/twscrape`; every script calls bare `twscrape` → FileNotFoundError → `[]` → "ok". Nothing has entered the registry from search/follower tracks since Sep 14. Fix: absolute path or PATH export in wrappers + daily_loop env.
2. **nightly_ingest dedup IndexError:** `seen[handle][9]` on 9-element tuples (indexes 0–8). Crashes the batch as soon as twscrape works AND returns duplicates. Latent — becomes live the moment #1 is fixed.
3. **nightly_ingest pain/growth extraction never runs:** tuple positions 7–8 hardcoded None; the +2/pain +1.5/growth score boost is always 0. extract_pain_signals/extract_growth_triggers defined but uncalled.
4. **followup_engine email auth broken:** sends literal header `Authorization: Bearer ***` (masked string committed to code) — every email fails auth. Plus `dm_sent` double-count per send; plus disjoint email-source logic (column gate vs bio extraction).
5. **daily_review.py dead:** imports `FLOWS` and `find_lead_by_registry_id` which don't exist → ImportError at launch.
6. **auto_sender docstring lies:** claims 476 is hard-fail/never-retry; code (correctly) treats 476 as privacy_block → fallback. Anyone reading the docstring re-burns leads via unpark logic assumptions.
7. **privacy_block_fallback reply templates pitch hard** ("Happy to share what works", "Happy to walk through the approach", "Our systems replace the hiring cycle") with em dashes — violates the flow_router doctrine (no pitch in public replies, no em dashes).
8. **tally_poller_wrapper dead end:** Composio path is `pass`; TALLY_API_TOKEN never set → "skipped: no composio" forever.
9. **run_nightly_ingest.sh lacks env:** no LD_PRELOAD/PYTHONPATH/TWITTER_COOKIES → even with twscrape fixed, Phase 2 enrichment (twitter_mcp import) fails under cron. daily_loop.py shows the correct pattern.
10. **manual_trigger `enrich` uses known-broken twscrape user_tweets-by-handle** (needs numeric id).
11. **emotion_scheduler stub:** writes state file nothing reads; DB_PATH points to a .py.
12. Minor: account_size.py imported by nothing; mhc_classifier_v6.py duplicate; install_cron.py references nonexistent scripts (legacy — jobs.json is the scheduler); review_cli source_post_author = prospect's own handle (quirky but harmless).

---

## 7. Gotchas (hard-won, do not relearn)

1. twscrape `user_tweets` needs NUMERIC id, not handle. Use `search "from:<handle>"`.
2. Never double-quote phrases in search queries — returns 0. Unquoted.
3. Error 476 = no follow-back yet, never a burn. 349 = DMs closed → fallback lane. 404/user-not-found = burn (failed_perm, never retry).
4. Never write Gumroad/product links from memory — use lead_magnet.py's scraped table (fabrication incident).
5. Any script importing twitter_mcp needs: LD_PRELOAD=/data/data/com.termux/files/usr/lib/libpython3.13.so, PYTHONPATH=~/.hermes/twitter-mcp, TWITTER_COOKIES=~/.config/twitter-mcp/cookies.json, and the Hermes venv python (cryptography Rust symbol breaks fresh processes).
6. Hard caps: 5 auto DMs/day, 6 public replies/day. Pacing 180–360s (DM), 60–90s (replies). These protect a 2024-era account against the 476 wall — don't raise casually.
7. X reply fetching is heavily rate-limited; comment mining yields ~6 qualified/profile — volume comes from search ingest.
8. reply_first's deterministic sha256 variants: approved dry-run preview == live output. Preserve `_h()`/`handle_seed()` if you ever touch the code.

---

## 8. First week for the new operator

1. `mhc-status` — ground truth funnel.
2. **Fix ingest (bug #1):** point PATH at the venv bin (or replace `twscrape` calls with the absolute path), and fix the dedup `item[9]` crash + missing pain extraction in the same pass — otherwise fixing #1 alone makes #2 live. Also add the env trio to run_nightly_ingest.sh. Then `mhc-run` or manual `ingest 3` and verify new rows appear.
3. `python3.13 scripts/reply_first.py --dry-run` — review the drafted replies, then live with `--limit 3`.
4. Run the human-gate backlog: `mhc-review` (67 human-tier leads waiting).
5. Re-enable crons one at a time via `mhc-cron` as each stage proves out.
6. Fix followup_engine's Bearer bug before ever expecting email touches; until then the email stage is decorative.
7. Watch `mhc-logs` + dashboard after every run; `job_runs` ledger is your first stop on any "did it really run?" question.
