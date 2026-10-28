"""
Request Models

EventModel -- parsed telemetry event from JSONL
ReconstructedRequest -- reconstructed request from event stream
RequestStatus -- status of a reconstructed request
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple, List
from enum import Enum


class RequestStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(Enum):
    ROUTE_ATTEMPT = "route_attempt"
    ROUTE_RESULT = "route_result"
    ROUTE_FALLBACK = "route_fallback"
    ROUTE_COOLDOWN = "route_cooldown"
    ROUTE_QUARANTINED = "route_quarantined"
    ROUTE_RECOVERED = "route_recovered"
    PREFERENCE_FALLBACK = "preference_fallback"
    PROVIDER_HEALTH = "provider_health"


@dataclass(frozen=True)
class EventModel:
    """Parsed telemetry event from JSONL"""
    timestamp: datetime
    event_type: str
    request_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    preference: Optional[str] = None
    outcome: Optional[str] = None
    status: Optional[int] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    cooldown_seconds: Optional[int] = None
    cooldown_until: Optional[datetime] = None
    from_preference: Optional[str] = None
    to_preference: Optional[str] = None
    context: str = "request"
    key_env: Optional[str] = None
    # GAP-001..005 fields
    requested_model: Optional[str] = None
    error_kind: Optional[str] = None
    build_id: Optional[str] = None
    first_chunk_latency_ms: Optional[int] = None
    fallback_depth: Optional[int] = None
    fallback_chain: tuple = ()
    retry_after_seconds: Optional[int] = None
    key_id: Optional[str] = None

    @classmethod
    def from_json(cls, data: dict) -> 'EventModel':
        ts_str = data.get('timestamp', '')
        try:
            timestamp = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            # Convert UTC to local timezone for display
            timestamp = timestamp.astimezone()
        except (ValueError, AttributeError):
            timestamp = datetime.now()
        
        return cls(
            timestamp=timestamp,
            event_type=data.get('event', ''),
            request_id=data.get('request_id'),
            provider=data.get('provider'),
            model=data.get('model'),
            preference=data.get('preference'),
            outcome=data.get('outcome'),
            status=data.get('status', data.get('status_code')),
            latency_ms=data.get('latency_ms'),
            error=data.get('error') or data.get('message'),
            cooldown_seconds=data.get('cooldown_seconds'),
            cooldown_until=None,
            from_preference=data.get('from_preference'),
            to_preference=data.get('to_preference'),
            context=data.get('context', 'request'),
            key_env=data.get('key_env'),
            requested_model=data.get('requested_model'),
            error_kind=data.get('error_kind'),
            build_id=data.get('build_id'),
            first_chunk_latency_ms=data.get('first_chunk_latency_ms'),
            fallback_depth=data.get('fallback_depth'),
            fallback_chain=tuple(data.get('fallback_chain') or ()),
            retry_after_seconds=data.get('retry_after_seconds'),
            key_id=data.get('key_id'),
        )


@dataclass(frozen=True)
class ReconstructedAttempt:
    """Single attempt in a reconstructed request"""
    provider: str
    model: str
    event_type: str
    status_code: Optional[int]
    timestamp: str
    latency_ms: Optional[int]
    error_message: Optional[str]
    raw_event: dict


@dataclass(frozen=True)
class ReconstructedRequest:
    """Reconstructed request from event stream"""
    request_id: str
    preference: str
    attempts: Tuple[ReconstructedAttempt, ...]
    status: RequestStatus
    outcome: str
    final_provider: Optional[str] = None
    final_model: Optional[str] = None
    total_latency_ms: Optional[int] = None
    fallback_chain: Tuple[str, ...] = ()
    preference_fallback: bool = False
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    is_complete: bool = False


# Alias for backward compatibility
RequestModel = ReconstructedRequest
