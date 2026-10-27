"""
Request Reconstruction - Reconstructs requests from event stream

Parses JSONL events and reconstructs complete request flows including
fallback chains, retries, and final outcomes.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from collections import defaultdict


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


class RequestStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ReconstructedRequest:
    """Reconstructed request from event stream"""
    request_id: str
    preference: str
    attempts: Tuple
    status: str
    outcome: str
    final_provider: Optional[str] = None
    final_model: Optional[str] = None
    total_latency_ms: Optional[int] = None
    fallback_chain: Tuple[str, ...] = ()
    preference_fallback: bool = False
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    is_complete: bool = False


class RequestReconstructor:
    """
    Reconstructs complete requests from event stream.
    
    Groups events by request_id, identifies attempts, tracks fallback chains,
    and determines final outcomes.
    """
    
    def __init__(self):
        pass
    
    def reconstruct_all(self, events: List[dict]) -> List:
        """Reconstruct all requests from event list.
        
        Groups by request_id when present. Events WITHOUT request_id
        (legacy/streaming events) are grouped into synthetic requests:
        a route_attempt starts a request, and the sequence of attempts
        until the next fresh attempt+success boundary forms one request.
        """
        requests = defaultdict(list)
        unattributed = []

        for event in events:
            if not event:
                continue
            req_id = event.get('request_id')
            if req_id:
                requests[req_id].append(event)
            else:
                unattributed.append(event)

        # Reconstruct attributed requests
        reconstructed = []
        for req_id, evs in requests.items():
            req = self._reconstruct_request(req_id, evs)
            if req:
                reconstructed.append(req)

        # Unattributed events: chunk into synthetic requests at each
        # route_attempt that follows a completed (result-seen) sequence.
        if unattributed:
            chunk = []
            for ev in unattributed:
                is_attempt = ev.get('event') == 'route_attempt'
                if is_attempt and chunk:
                    req = self._reconstruct_request('unattributed', chunk)
                    if req:
                        reconstructed.append(req)
                    chunk = []
                chunk.append(ev)
            if chunk:
                req = self._reconstruct_request('unattributed', chunk)
                if req:
                    reconstructed.append(req)

        return reconstructed
    
    def _reconstruct_request(self, request_id: str, events: List[dict]) -> Optional[object]:
        """Reconstruct a single request from its events."""
        # Sort events by timestamp
        events.sort(key=lambda e: e.get('timestamp', ''))
        
        if not events:
            return None
        
        # Extract preference from first event
        preference = events[0].get('preference', 'unknown')
        
        # Group into attempts
        attempts = []
        current_attempt = None
        
        for event in events:
            event_type = event.get('event', '')
            
            if event_type == 'route_attempt':
                # Start new attempt
                if current_attempt:
                    attempts.append(current_attempt)
                current_attempt = {
                    'provider': event.get('provider'),
                    'model': event.get('model'),
                    'event_type': 'route_attempt',
                    'status_code': None,
                    'timestamp': event.get('timestamp'),
                    'latency_ms': None,
                    'error_message': None,
                }
            elif event_type == 'route_result':
                if current_attempt:
                    # Production events use 'status'; tolerate both spellings
                    current_attempt['status_code'] = event.get('status', event.get('status_code'))
                    current_attempt['latency_ms'] = event.get('latency_ms')
                    current_attempt['error_message'] = event.get('error')
                    current_attempt['outcome'] = event.get('outcome', 'success')
                    attempts.append(current_attempt)
                    current_attempt = None
            elif event_type in ('route_fallback', 'route_cooldown', 'route_quarantined', 'route_recovered'):
                if current_attempt:
                    # Add event info to current attempt
                    if 'extra' not in current_attempt:
                        current_attempt['extra'] = []
                    current_attempt['extra'].append({
                        'type': event_type,
                        'data': event
                    })
        
        if current_attempt:
            attempts.append(current_attempt)
        
        # Build reconstructed attempt objects
        recon_attempts = []
        for attempt in attempts:
            recon_attempts.append(ReconstructedAttempt(
                provider=attempt.get('provider', 'unknown'),
                model=attempt.get('model', 'unknown'),
                event_type=attempt.get('event_type', 'unknown'),
                status_code=attempt.get('status_code'),
                timestamp=attempt.get('timestamp'),
                latency_ms=attempt.get('latency_ms'),
                error_message=attempt.get('error_message'),
                raw_event={}
            ))
        
        # Determine outcome
        status = RequestStatus.FAILED
        outcome = 'failed'
        final_provider = None
        final_model = None
        total_latency = None
        fallback_chain = []
        
        if attempts:
            last = attempts[-1]
            if last.get('status_code') == 200:
                status = RequestStatus.COMPLETED
                outcome = 'success'
            else:
                outcome = last.get('outcome', 'failed')
            
            final_provider = last.get('provider')
            final_model = last.get('model')
            total_latency = last.get('latency_ms')
            
            # Build fallback chain
            seen = set()
            for a in attempts:
                p = a.get('provider')
                if p and p not in seen:
                    fallback_chain.append(p)
                    seen.add(p)
        
        # Check completion
        is_complete = any(a.get('status_code') == 200 for a in attempts)
        
        return ReconstructedRequest(
            request_id=request_id,
            preference=preference,
            attempts=tuple([ReconstructedAttempt(
                provider=a.get('provider', 'unknown'),
                model=a.get('model', 'unknown'),
                event_type=a.get('event_type', 'unknown'),
                status_code=a.get('status_code'),
                timestamp=a.get('timestamp'),
                latency_ms=a.get('latency_ms'),
                error_message=a.get('error_message'),
                raw_event={}
            ) for a in attempts]),
            status=status,
            outcome=outcome,
            final_provider=final_provider,
            final_model=final_model,
            total_latency_ms=total_latency,
            fallback_chain=tuple(fallback_chain),
            preference_fallback=len(fallback_chain) > 1,
            first_timestamp=events[0].get('timestamp') if events else None,
            last_timestamp=events[-1].get('timestamp') if events else None,
            is_complete=is_complete
        )
    
    def get_last_completed_request(self, events: List[dict]) -> Optional[dict]:
        """Get the last completed request from events."""
        requests = self.reconstruct_all(events)
        completed = [r for r in requests if r.is_complete]
        if not completed:
            return None
        # Return most recent
        completed.sort(key=lambda r: r.last_timestamp or '', reverse=True)
        return {
            'request_id': completed[0].request_id,
            'preference': completed[0].preference,
            'attempts': completed[0].attempts,
            'status': completed[0].status,
            'outcome': completed[0].outcome,
            'final_provider': completed[0].final_provider,
            'final_model': completed[0].final_model,
            'total_latency_ms': completed[0].total_latency_ms,
            'fallback_chain': completed[0].fallback_chain,
            'preference_fallback': completed[0].preference_fallback,
            'first_timestamp': completed[0].first_timestamp,
            'last_timestamp': completed[0].last_timestamp,
            'is_complete': True
        }


from collections import defaultdict
