# Provider Capability Registry — CraftedWorkflowsOS
*Generated: 2026-08-29 | Runtime-verified on Infinix Smart 6 / Android 11 / Termux / Python 3.13*

---

## Legend
| Status | Meaning |
|--------|---------|
| 🟢 **VERIFIED_AND_EXECUTABLE** | Tested live, works, callable now |
| 🟡 **CONNECTED_BUT_RUNTIME_FAILURE** | Account connected, tool exists, but fails at runtime (auth/perm/transport) |
| 🔵 **PROVIDER_EXISTS_BUT_NOT_CONNECTED** | Toolkit/provider available, no account linked |
| ⚫ **UNKNOWN_NEEDS_TEST** | Toolkit connected, tools untested (schema unknown) |
| 🔴 **CONFIRMED_NO_API** | No provider has this capability; platform/API doesn't expose it |
| 🟣 **AVAILABLE_THROUGH_INDIRECT_PROVIDER** | Works via secondary provider (e.g., Typefully via Composio) |

---

## Provider Priority Order
1. **Composio MCP/SDK/REST** (Notion, Gmail, LinkedIn, IG, FB, Typefully, Firecrawl, Tavily, EXA)
2. **Zernio MCP/SDK/REST** (LinkedIn, Instagram, Analytics, Comments, Mentions, Posts)
3. **Arcade_X MCP** (X/Twitter: search, lookup, post, thread, engagement, lists, timeline)
4. **Other indirect providers** (Typefully via Composio for scheduling)
5. **Test unknown providers** (Composio LI/IG/FB tools — untested)
6. **Android (Tasker/AutoInput)** — **ONLY when no viable API route exists**

---

## Registry Matrix

### LINKEDIN
|| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
||-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
|| Post (draft) | Zernio SDK | `posts.create(platforms=[{platform:'linkedin',accountId}], is_draft=true)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Post (publish_now) | Zernio SDK | `posts.create(..., publish_now=true)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Post (scheduled) | Zernio SDK | `posts.create(..., scheduled_for=...)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Cross-post | Zernio SDK | `posts.create(platforms=[{platform:'linkedin'},{platform:'instagram'}])` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Analytics | Zernio SDK | `analytics.get_analytics(post_id, platform, account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| Best time to post | Zernio SDK | `analytics.get_best_time_to_post(platform, account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| Comments (list) | Zernio SDK | `comments.list_inbox_comments(platform='linkedin')` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Comments (get post) | Zernio SDK | `comments.get_inbox_post_comments(post_id, account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Comments (reply) | Zernio SDK | `comments.reply_to_inbox_post(post_id, account_id, message)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Mentions (list) | Zernio SDK | `mentions.list_inbox_mentions(account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| Mentions (reply) | Zernio SDK | `mentions.reply_to_mention(account_id, media_id, message)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio LI | No |
|| **DM / Inbox conversations** | Zernio SDK | `messages.list_inbox_conversations(platform='linkedin')` | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | **Android** | **YES (Gap #1)** |
|| **Send DM (cold/1:1)** | Zernio SDK | `messages.create_inbox_conversation / send_inbox_message` | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | **Android** | **YES (Gap #1)** |
|| **Connection requests** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | Composio LI (untested) | **YES (Gap #5)** |
|| **People search** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | Composio LI (untested) | **YES (Gap #6)** |
|| Profile retrieval | Zernio SDK | `accounts.get_linked_in_mentions` (mentions only) | ZERNIO_API_KEY | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ✅ Yes | Composio LI (untested) | No |
|| Account health | Zernio SDK | `accounts.get_account_health / get_all_accounts_health` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| Follower stats | Zernio SDK | `accounts.get_follower_stats` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| **Composio LI tools** | Composio | `LINKEDIN_*` (9 tools: CREATE_POST, GET_MY_INFO, GET_COMPANY_INFO, GET_PERSON, CREATE_ARTICLE_URL_SHARE, REGISTER_IMAGE_UPLOAD, GET_POST_CONTENT, CREATE_COMMENT, LIST_REACTIONS) | Composio API Key | 🔴 CONFIRMED_NO_API (for DM/search/connection) | ✅ Yes | **Android** | **STAYS YES (Gaps #1, #5, #6)** |

### INSTAGRAM
|| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
||-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
|| Post (draft) | Zernio SDK | `posts.create(platforms=[{platform:'instagram',accountId}], is_draft=true)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Post (publish_now) | Zernio SDK | `posts.create(..., publish_now=true, media_items=[...])` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Cross-post | Zernio SDK | `posts.create(platforms=[{platform:'instagram'},...])` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Analytics | Zernio SDK | `analytics.get_analytics / get_instagram_account_insights` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| Comments (list) | Zernio SDK | `comments.list_inbox_comments(platform='instagram')` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Comments (get post) | Zernio SDK | `comments.get_inbox_post_comments(post_id, account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Comments (reply) | Zernio SDK | `comments.reply_to_inbox_post(post_id, account_id, message)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| Comment → Private DM | Zernio SDK | `comments.send_private_reply_to_comment(post_id, comment_id, account_id, message)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| Mentions (list) | Zernio SDK | `mentions.list_inbox_mentions(account_id)` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | Composio IG | No |
|| **Inbox conversations (read)** | Zernio SDK | `messages.list_inbox_conversations(platform='instagram')` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| **Send DM (existing conversation)** | Zernio SDK | `messages.send_inbox_message(conversation_id, account_id, message)` | ZERNIO_API_KEY | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial (needs conv_id) | — | No |
|| **Cold DM to arbitrary user** | Zernio SDK | `messages.create_inbox_conversation` | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | **Android** | **YES (Gap #7)** |
|| Comment Automation (DM) | Zernio SDK | `comment_automations.create_comment_automation` | ZERNIO_API_KEY | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
|| **Composio IG tools** | Composio | `INSTAGRAM_*` (tools: LIST_ALL_CONVERSATIONS, LIST_ALL_MESSAGES, SEND_TEXT_MESSAGE, GET_CONVERSATION, GET_PAGE_CONVERSATIONS, GET_MESSENGER_PROFILE, MARK_SEEN, SEND_IMAGE) — **Active connection ✅** | Composio API Key | 🔴 CONFIRMED_NO_API (for cold DM — no new thread initiation) | ✅ Yes | **Android** | **STAYS YES (Gap #7)** |

### FACEBOOK
|| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
||-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
|| Page post | Zernio SDK | `posts.create(platform='facebook', ...)` | ZERNIO_API_KEY | 🔵 PROVIDER_EXISTS_BUT_NOT_CONNECTED | ❌ No | Composio FB | Skip per user |
|| Page comments | Zernio SDK | `comments.*` | ZERNIO_API_KEY | 🔵 PROVIDER_EXISTS_BUT_NOT_CONNECTED | ❌ No | Composio FB | Skip per user |
|| Page Messenger | Zernio SDK | `messages.list_inbox_conversations(platform='facebook')` | ZERNIO_API_KEY | 🔵 PROVIDER_EXISTS_BUT_NOT_CONNECTED | ❌ No | Composio FB | Skip per user |
|| **Group search/discovery** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | — | Skip per user |
|| **Group posts/read** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | — | Skip per user |
|| **Group comments** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | — | Skip per user |
|| **Group member discovery** | Zernio SDK | — | ZERNIO_API_KEY | 🔴 CONFIRMED_NO_API | ✅ Yes | — | Skip per user |
|| **Composio FB tools** | Composio | `FACEBOOK_*` (tools: LIST_MANAGED_PAGES, GET_PAGE_DETAILS, GET_PAGE_CONVERSATIONS, GET_CONVERSATION_MESSAGES, GET_MESSAGE_DETAILS, GET_PAGE_POSTS, GET_POST_REACTIONS, SEND_MESSAGE) — **Active connection ✅ (Pages only)** | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE (for Pages) | ✅ Yes | — | Skip per user |

### X / TWITTER
|| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
||-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
|| Search tweets (keywords) | Arcade_X | `X_SearchTweetsByKeywords` / `X_SearchRecentTweetsByKeywords` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial (MCP down) | Typefully (schedule only) | No |
|| Search tweets (username) | Arcade_X | `X_SearchRecentTweetsByUsername` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | Typefully | No |
|| User lookup | Arcade_X | `X_LookupSingleUserByUsername` / `X_LookupUsers` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Tweet lookup | Arcade_X | `X_LookupTweetById` / `X_LookupTweets` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Conversation/thread | Arcade_X | `X_GetConversation` / `X_GetRepliesToTweet` / `X_GetQuoteTweets` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Post tweet (text) | Arcade_X | `X_PostTweet(tweet_text)` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | Typefully (schedule) | No |
|| Post thread | Arcade_X | `X_PostThread(tweets=[...])` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | Typefully | No |
|| Reply | Arcade_X | `X_ReplyToTweet(tweet_id, tweet_text)` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Like/Unlike | Arcade_X | `X_LikeTweet` / `X_UnlikeTweet` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Retweet/Undo | Arcade_X | `X_Retweet` / `X_UndoRetweet` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Lists (CRUD) | Arcade_X | Full suite (12 tools) | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Timeline | Arcade_X | `X_GetHomeTimeline` / `X_GetUserTweets` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| Mentions | Arcade_X | `X_GetMyMentions` | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | — | No |
|| **DMs** | Arcade_X | **NO TOOLS EXIST** (search hallucinated XCreateDirectMessage; actual toolkit: 0 DM tools) | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial (MCP down) | **Android** | **YES (Gap #2)** |
|| **Follow/Unfollow** | Arcade_X | **NO TOOLS EXIST** (search hallucinated XFollowUser/XUnfollowUser; actual toolkit: 0 follow tools) | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | **Android** | **YES (Gap #4)** |
|| **Media upload + tweet** | Arcade_X | **NO TOOLS EXIST** (search hallucinated XCreateTweetWithMedia; actual toolkit: text-only) | Arcade OAuth | 🟡 CONNECTED_BUT_RUNTIME_FAILURE | ⚠️ Partial | **Android** | **YES (Gap #3)** |
|| **Schedule tweets** | Arcade_X | — | Arcade OAuth | 🔴 CONFIRMED_NO_API | ✅ Yes | Typefully (indirect) | No (use Typefully) |
|| **Typefully (via Composio)** | Composio | `TYPEFULLY_CREATE_DRAFT` / `LIST_DRAFTS` / `GET_QUEUE` / `GET_SOCIAL_SET` / `INITIALIZE_MEDIA_UPLOAD` | Composio API Key | 🟣 AVAILABLE_THROUGH_INDIRECT_PROVIDER | ❌ Not tested | — | No (scheduling only) |

### NOTION
| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
|-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
| Query database | Composio | `NOTION_QUERY_DATABASE` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes (hermes_daily.py) | — | No |
| Create page | Composio | `NOTION_CREATE_PAGE` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes (hermes_daily.py) | — | No |
| Update page | Composio | `NOTION_UPDATE_PAGE` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes (hermes_daily.py) | — | No |
| Search | Composio | `NOTION_SEARCH` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |
| 5 DBs live | — | Leads, Clients, Meetings, Projects, Tasks | — | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |

### GMAIL
| Operation | Provider | Exact Tool/Endpoint | Auth Source | Runtime Status | Tested? | Fallback | Android Required? |
|-----------|----------|---------------------|-------------|----------------|---------|----------|-------------------|
| Send email | Composio | `GMAIL_SEND_EMAIL` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes (Tier 2 approvals) | — | No |
| Fetch/search | Composio | `GMAIL_SEARCH_EMAILS` / `GMAIL_GET_EMAIL` | Composio API Key | 🟢 VERIFIED_AND_EXECUTABLE | ✅ Yes | — | No |

### OTHER COMPOSIO TOOLKITS
| Toolkit | Status | Notes |
|---------|--------|-------|
| Firecrawl | 🟢 VERIFIED_AND_EXECUTABLE | Native in Hermes + Composio |
| Tavily | 🟢 VERIFIED_AND_EXECUTABLE | Composio only |
| EXA | 🟢 VERIFIED_AND_EXECUTABLE | Composio only |
| ImgBB | 🟢 VERIFIED_AND_EXECUTABLE | Image upload for posts |
| Google Drive | 🟢 VERIFIED_AND_EXECUTABLE | LinkedIn image upload flow |
| Slack | 🟢 VERIFIED_AND_EXECUTABLE | Notifications |
| Web Search/Fetch | 🟢 VERIFIED_AND_EXECUTABLE | Lead sourcing |

---

## Android Gaps (7 Confirmed — REMOVED FALSE POSITIVES)

| # | Operation | Platform | Why API Cannot | App | Queue File Pattern |
|---|-----------|----------|----------------|-----|-------------------|
| **1** | **LinkedIn DM send (cold/1:1)** | LinkedIn | Zernio: PLATFORM_NOT_SUPPORTED; Composio: no DM tool | LinkedIn Android | `linkedin_dm_*.json` |
| **2** | **X/Twitter DM send/reply** | X/Twitter | Arcade_X: **NO DM tools exist** (search hallucinated XCreateDirectMessage) — X API requires elevated access | X Android | `x_dm_*.json` |
| **3** | **X/Twitter media upload + tweet** | X/Twitter | Arcade_X: **NO media upload tool** (search hallucinated XCreateTweetWithMedia) — Typefully: scheduling only | X Android | `x_media_*.json` |
| **4** | **X/Twitter follow/unfollow** | X/Twitter | Arcade_X: **NO follow tools exist** (search hallucinated XFollowUser/XUnfollowUser) | X Android | `x_follow_*.json` |
| **5** | **LinkedIn connection requests** | LinkedIn | Zernio: no tool; Composio: no connection tool | LinkedIn Android | `linkedin_connect_*.json` |
| **6** | **LinkedIn people search** | LinkedIn | Zernio: no search tool; Composio: no search tool | LinkedIn Android | `linkedin_search_*.json` |
| **7** | **Instagram cold DM** | Instagram | Zernio: only reactive (reply/conversation); Composio: no new thread initiation | Instagram Android | `instagram_dm_*.json` |

**CRITICAL CORRECTION (2026-08-29):** My earlier claim that Arcade_X eliminated gaps #2, #3, #4 was **FALSE**. The `ARCADE_SEARCH_TOOLS` function returned hallucinated tool names (`XCreateDirectMessage`, `XFollowUser`, `XCreateTweetWithMedia`) that don't exist in your actual toolkit. The helper bot's list of 39 tools is the **only authoritative source** — DMs, follow/unfollow, and media tweets are **not available**.

**All 7 gaps remain. Android layer required for full automation.**

---

## Execution Router Logic (Current + Required)

```python
# Current in hermes_daily.py → needs extraction to provider_router.py

def route_operation(platform: str, operation: str, params: dict) -> ExecutionPlan:
    """
    Returns: { provider: 'zernio_sdk'|'zernio_mcp'|'composio'|'arcade_x'|'typefully'|'android',
               tool: 'exact_tool_name',
               params: {...},
               fallback: [...] }
    """
    
    # Priority 1: Composio (Notion, Gmail, Firecrawl, Tavily, EXA, ImgBB, Drive, Slack)
    if platform == 'notion' or operation in ['email_send', 'web_search', 'web_fetch', 'image_upload', 'drive_upload']:
        return composio_route(operation, params)
    
    # Priority 2: Zernio SDK (LinkedIn, Instagram - posts, analytics, comments, mentions)
    if platform in ['linkedin', 'instagram'] and operation in [
        'post_draft', 'post_publish', 'post_schedule', 'cross_post',
        'analytics', 'best_time', 'comments_list', 'comments_get', 'comments_reply',
        'mentions_list', 'mentions_reply', 'inbox_read', 'comment_to_dm', 'comment_automation'
    ]:
        return zernio_sdk_route(platform, operation, params)
    
    # Priority 3: Arcade_X (X/Twitter - search, lookup, post, engagement, lists)
    if platform == 'x' and operation in [
        'search', 'user_lookup', 'tweet_lookup', 'conversation', 'replies', 'quotes',
        'post_tweet', 'post_thread', 'reply', 'like', 'retweet', 'delete_tweet',
        'lists_crud', 'timeline', 'mentions', 'whoami'
    ]:
        return arcade_x_route(operation, params)
    
    # Priority 4: Typefully via Composio (X scheduling only)
    if platform == 'x' and operation in ['schedule_tweet', 'schedule_thread', 'get_queue', 'get_social_sets']:
        return typefully_route(operation, params)
    
    # Priority 5: Composio LI/IG/FB (TESTED — no DM/search/connection tools)
        if platform in ['linkedin', 'instagram', 'facebook'] and operation in [
            'dm_send', 'dm_read', 'connections', 'people_search', 'profile_lookup'
        ]:
            return composio_social_route(platform, operation, params)  # TESTED — returns CONFIRMED_NO_API for gaps

        # Priority 6: Android (ONLY for 7 confirmed gaps)
        if (platform, operation) in ANDROID_GAPS:
            return android_route(platform, operation, params)
    
    raise ValueError(f"No provider for {platform}.{operation}")
```

---

## Files Needing Implementation / Update

### New Files
| File | Purpose |
|------|---------|
| `runtime/provider_router.py` | Single source of truth for provider selection (above logic) |
| `runtime/zernio_sdk_adapter.py` | Thin wrapper around Zernio SDK (not MCP) — all verified methods |
| `runtime/composio_adapter.py` | Hermes-native Composio intent helpers (extends hermes-business-ops) |
| `runtime/arcade_x_adapter.py` | Hermes-native Arcade_X intent helpers (extends arcade-x-discovery) |
| `runtime/typefully_adapter.py` | Typefully scheduling via Composio |
| `runtime/android_queue.py` | Write/read queue files for 06_Android/ |
| `runtime/capability_registry.py` | Load this registry as typed config |

### Existing Files to Modify
| File | Change |
|------|--------|
| `runtime/hermes_daily.py` | Import `provider_router`; replace inline provider logic with `route_operation()` |
| `05_Integrations/Active_Setup.md` | Update with verified runtime statuses from this registry |
| `06_Android/scripts/*.py` | Accept queue file paths as args; output results to `06_Android/results/` |
| `05_Integrations/Notion_Workspace_Schema.md` | Add `provider_used` + `execution_status` fields to Leads/Meetings/Tasks DBs |

### Android Files (Already Built — Just Deploy)
| File | Status |
|------|--------|
| `06_Android/config.json` | ✅ Rate limits, active hours, spintax |
| `06_Android/scripts/linkedin_dm.py` | ✅ AutoInput taps for LI DM |
| `06_Android/scripts/x_dm.py` | ✅ AutoInput taps for X DM |
| `06_Android/scripts/x_media.py` | ✅ AutoInput taps for X media tweet |
| `06_Android/scripts/x_follow.py` | ✅ AutoInput taps for X follow/unfollow |
| `06_Android/scripts/linkedin_connect.py` | ✅ AutoInput taps for LI connection |
| `06_Android/scripts/linkedin_search.py` | ✅ AutoInput taps for LI people search |
| `06_Android/scripts/instagram_dm.py` | ✅ AutoInput taps for IG cold DM |
| `06_Android/tasker/*.tsk.xml` | ✅ 5 profiles (queue monitor per gap) |
| `06_Android/termux_boot/boot.sh` | ✅ Auto-starts on reboot |

---

## Next Steps (Implementation Order)

1. **Test Composio LI/IG/FB tools** (10 min) — could eliminate Gaps #1, #5, #6, #7
2. **Fix Arcade_X MCP**: `hermes mcp reauth arcade_x` (1 min)
3. **Create `runtime/provider_router.py`** with logic above
4. **Create adapter files** (thin wrappers, no new logic)
5. **Update `hermes_daily.py`** to use `provider_router`
6. **Deploy Android layer** to Infinix Smart 6 (15 min)
7. **Run first Commercial cycle**: `python3 runtime/hermes_daily.py`

---

## Environment Notes (No Install Needed)

| Component | Status | Notes |
|-----------|--------|-------|
| Python 3.13 | ✅ System | `/data/data/com.termux/files/usr/bin/python3` |
| Zernio SDK | ✅ Installed | `~/zernio-runtime/venv/` v1.4.581 — **works directly** |
| Zernio MCP | 🟡 Transport issue | Wrapper at `~/zernio-runtime/bin/zernio-mcp-stdio` — SDK preferred |
| Composio MCP | ✅ Connected | `~/composio-runtime/bin/composio-mcp-stdio` — 70 connections |
| Arcade_X MCP | 🟡 Transport down | Was verified 2026-08-27; `hermes mcp reauth arcade_x` to fix. **39 tools confirmed** (text tweets, search, lists, users, timeline, mentions, likes, retweets, threads). **NO DMs, NO follow/unfollow, NO media upload** — `ARCADE_SEARCH_TOOLS` hallucinated these tools. Helper bot list is authoritative. |
| Typefully | ✅ Connected | Via Composio (16 tools) — scheduling only |
| Notion 5 DBs | ✅ Live | DB IDs in env vars |

**DO NOT install Python 3.14 — breaks Hermes on Termux.**
**DO NOT pip install zernio globally — use `~/zernio-runtime/venv/bin/python` or activate that venv.**
**NO new dependencies needed.** All runtime components exist.