"""
RouteOps Configuration and Constants

All configuration values for RouteOps v0.1.
No secrets, no credentials, only operational parameters.
"""

from pathlib import Path


def get_hermes_home() -> Path:
    """Get Hermes home directory. Uses HERMES_HOME env or default ~/.hermes"""
    import os
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def get_router_dir() -> Path:
    """Get router directory under CraftedWorkflowsOS."""
    return Path.home() / "CraftedWorkflowsOS" / "router"


def get_router_endpoint() -> tuple:
    """Read host/port from config.json (single source of truth).

    Returns (base_url, host, port). Falls back to 127.0.0.1:8643 if the
    config is missing or unreadable — the TUI never hardcodes a port as a
    displayed fact, it falls back and says so.
    """
    import json
    cfg_path = get_router_dir() / "config.json"
    host, port = "127.0.0.1", 8643
    try:
        data = json.loads(cfg_path.read_text())
        host = str(data.get("host") or host)
        port = int(data.get("port") or port)
    except Exception:
        pass
    return f"http://{host}:{port}", host, port


def get_hermes_agent_root() -> Path:
    """Path to the Hermes agent source tree (for liveness detection)."""
    return Path.home() / ".hermes" / "hermes-agent"


# Telemetry file paths
ROUTER_EVENTS_JSONL = get_router_dir() / "router-events.jsonl"
ROUTER_CONFIG_JSON = get_router_dir() / "config.json"
ROUTER_LOG = get_router_dir() / "router.log"

# Router HTTP endpoints — host/port read from config.json (source of truth)
ROUTER_BASE_URL, ROUTER_HOST, ROUTER_PORT = get_router_endpoint()
ROUTER_HEALTH_URL = f"{ROUTER_BASE_URL}/health"
ROUTER_MODELS_URL = f"{ROUTER_BASE_URL}/v1/models"

# =============================================================================
# POLLING INTERVALS (seconds)
# =============================================================================

POLL_INTERVALS = {
    "jsonl": 1.5,      # JSONL tail
    "health": 2.5,     # /health
    "models": 30.0,    # /v1/models
    "config": 60.0,    # config.json
    "process": 10.0,   # process inspection
}

# HTTP timeouts
HTTP_CONNECT_TIMEOUT = 2.0
HTTP_READ_TIMEOUT = 10.0

# =============================================================================
# PRODUCTION ROUTER IDENTITY
# =============================================================================

PRODUCTION_BINARY_NAME = "router"
PRODUCTION_CWD_NAME = "router"
MIN_PRODUCTION_BINARY_SIZE = 8_000_000  # 8 MB (production ~10MB, test-env ~6MB)

# =============================================================================
# POLLING SCHEDULER
# =============================================================================

SCHEDULER_TICK = 0.1  # Main loop tick (seconds)
MAX_QUEUE_DRAIN_PER_TICK = 100  # Max messages to process per tick

# =============================================================================
# UI DEFAULTS
# =============================================================================

DEFAULT_REFRESH_INTERVAL = 2  # seconds
DEFAULT_AUTO_REFRESH = True
DEFAULT_EVENT_DENSITY = "normal"  # compact, normal, verbose
DEFAULT_SHOW_DISCLAIMERS = True

# Terminal
MIN_TERMINAL_WIDTH = 80
MIN_TERMINAL_HEIGHT = 24

# =============================================================================
# JSONL BUFFER
# =============================================================================

JSONL_MAX_BUFFER = 1000  # Max events to keep in memory

# =============================================================================
# SECURITY
# =============================================================================

# Paths that must NEVER be read
FORBIDDEN_PATHS = frozenset([
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials", "auth.json", "auth.lock", "mcp-tokens",
])

# Environment variables that must NEVER be read
FORBIDDEN_ENV_VARS = frozenset([
    "NVIDIA_API_KEY", "HERMES_CUSTOM_API_ZYDIT_IN_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
    "AUTHORIZATION", "API_KEY", "SECRET", "TOKEN", "PASSWORD",
])

# Known safe terms that should NOT be redacted
SAFE_TERMS = frozenset([
    "token count", "token usage", "token limit", "token budget",
    "tokens used", "prompt tokens", "completion tokens",
    "token bucket", "token rate", "token window",
])

# =============================================================================
# DISCLAIMER TEXT
# =============================================================================

DISCLAIMER_TEXT = "ROUTEOPS — external observer, read-only. Source: /health · /v1/models · config.json · router-events.jsonl"

# =============================================================================
# PROCESS IDENTITY
# =============================================================================

ROUTER_IDENTITY_PRODUCTION = "PRODUCTION"
ROUTER_IDENTITY_TEST = "TEST"
ROUTER_IDENTITY_WRONG = "WRONG"
ROUTER_IDENTITY_NOT_RUNNING = "NOT_RUNNING"

MIN_PRODUCTION_BINARY_SIZE = 8_000_000  # 8MB

# =============================================================================
# CONFIG MODEL EXPORTS
# =============================================================================

from routeops.models import ConfigModel

__all__ = [
    "get_hermes_home",
    "get_router_dir",
    "ROUTER_EVENTS_JSONL",
    "ROUTER_CONFIG_JSON",
    "ROUTER_LOG",
    "ROUTER_HOST",
    "ROUTER_PORT",
    "ROUTER_BASE_URL",
    "ROUTER_HEALTH_URL",
    "ROUTER_MODELS_URL",
    "POLL_INTERVALS",
    "HTTP_CONNECT_TIMEOUT",
    "HTTP_READ_TIMEOUT",
    "PRODUCTION_BINARY_NAME",
    "PRODUCTION_CWD_NAME",
    "MIN_PRODUCTION_BINARY_SIZE",
    "SCHEDULER_TICK",
    "MAX_QUEUE_DRAIN_PER_TICK",
    "DEFAULT_REFRESH_INTERVAL",
    "DEFAULT_AUTO_REFRESH",
    "DEFAULT_EVENT_DENSITY",
    "DEFAULT_SHOW_DISCLAIMERS",
    "MIN_TERMINAL_WIDTH",
    "MIN_TERMINAL_HEIGHT",
    "JSONL_MAX_BUFFER",
    "FORBIDDEN_PATHS",
    "FORBIDDEN_ENV_VARS",
    "SAFE_TERMS",
    "DISCLAIMER_TEXT",
    "ROUTER_IDENTITY_PRODUCTION",
    "ROUTER_IDENTITY_TEST",
    "ROUTER_IDENTITY_WRONG",
    "ROUTER_IDENTITY_NOT_RUNNING",
    "MIN_PRODUCTION_BINARY_SIZE",
    "ConfigModel",
]
