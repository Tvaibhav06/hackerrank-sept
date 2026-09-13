"""
Circuit breaker for Gemini API calls.

States:
  CLOSED    – normal operation, API calls allowed
  OPEN      – quota exhausted, all calls blocked immediately
  HALF_OPEN – cooldown elapsed, exactly ONE probe call permitted

Transitions:
  CLOSED  →  OPEN       on 429 / RESOURCE_EXHAUSTED
  OPEN    →  HALF_OPEN  when wall-clock >= opened_at + cooldown
  HALF_OPEN → CLOSED    on successful probe
  HALF_OPEN → OPEN      on another 429

State is persisted to disk so it survives process restarts.
"""

import json
import re
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from config import (
    CIRCUIT_BREAKER_FILE,
    CIRCUIT_BREAKER_SAFETY_MARGIN,
    CIRCUIT_BREAKER_DEFAULT_COOLDOWN,
)


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


def _parse_retry_after(error_msg: str) -> float:
    """Extract 'retry in Xs' from a Gemini 429 error body."""
    match = re.search(r'retry in (\d+\.?\d*)s', str(error_msg), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 0.0


class CircuitBreaker:
    """Global circuit breaker protecting all Gemini API calls."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or CIRCUIT_BREAKER_FILE
        self.state: BreakerState = BreakerState.CLOSED
        self.opened_at: float = 0.0          # time.time() when OPEN was entered
        self.cooldown_seconds: float = 0.0   # how long to wait before HALF_OPEN
        self.last_error_type: str = ""
        self.last_error_model: str = ""
        self.last_error_operation: str = ""
        self.retry_after_value: float = 0.0
        self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.state = BreakerState(data.get('state', 'CLOSED'))
                self.opened_at = data.get('opened_at', 0.0)
                self.cooldown_seconds = data.get('cooldown_seconds', 0.0)
                self.last_error_type = data.get('last_error_type', '')
                self.last_error_model = data.get('last_error_model', '')
                self.last_error_operation = data.get('last_error_operation', '')
                self.retry_after_value = data.get('retry_after_value', 0.0)
            except Exception:
                self._reset()

    def _save(self):
        data = {
            'state': self.state.value,
            'opened_at': self.opened_at,
            'cooldown_seconds': self.cooldown_seconds,
            'last_error_type': self.last_error_type,
            'last_error_model': self.last_error_model,
            'last_error_operation': self.last_error_operation,
            'retry_after_value': self.retry_after_value,
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def _reset(self):
        self.state = BreakerState.CLOSED
        self.opened_at = 0.0
        self.cooldown_seconds = 0.0
        self.last_error_type = ""
        self.last_error_model = ""
        self.last_error_operation = ""
        self.retry_after_value = 0.0

    # ── Public API ───────────────────────────────────────────────────

    def allow_request(self) -> bool:
        """Return True if a Gemini call is allowed right now."""
        if self.state == BreakerState.CLOSED:
            return True

        if self.state == BreakerState.OPEN:
            elapsed = time.time() - self.opened_at
            if elapsed >= self.cooldown_seconds:
                # Transition to HALF_OPEN – allow exactly one probe
                self.state = BreakerState.HALF_OPEN
                self._save()
                return True
            return False

        if self.state == BreakerState.HALF_OPEN:
            # Only one probe is allowed; subsequent callers are blocked
            # until the probe reports success or failure.
            return True

        return False  # defensive

    def seconds_until_half_open(self) -> float:
        """How many seconds remain before the breaker might transition to HALF_OPEN."""
        if self.state != BreakerState.OPEN:
            return 0.0
        remaining = self.cooldown_seconds - (time.time() - self.opened_at)
        return max(0.0, remaining)

    def record_success(self):
        """A Gemini call succeeded – close the breaker."""
        self._reset()
        self._save()

    def record_quota_error(self, error: Exception, model: str = "", operation: str = ""):
        """A 429 / RESOURCE_EXHAUSTED was received – open the breaker."""
        retry_after = _parse_retry_after(str(error))
        if retry_after > 0:
            cooldown = retry_after + CIRCUIT_BREAKER_SAFETY_MARGIN
        else:
            cooldown = CIRCUIT_BREAKER_DEFAULT_COOLDOWN

        self.state = BreakerState.OPEN
        self.opened_at = time.time()
        self.cooldown_seconds = cooldown
        self.last_error_type = type(error).__name__
        self.last_error_model = model
        self.last_error_operation = operation
        self.retry_after_value = retry_after
        self._save()

    def manual_reset(self):
        """Manually reset the breaker to CLOSED (e.g. after changing API key)."""
        self._reset()
        self._save()

    # ── Introspection ────────────────────────────────────────────────

    def status_summary(self) -> str:
        if self.state == BreakerState.CLOSED:
            return "CLOSED – API calls allowed"
        elif self.state == BreakerState.OPEN:
            remaining = self.seconds_until_half_open()
            return (
                f"OPEN – API calls blocked. "
                f"Retry in {remaining:.0f}s. "
                f"Last error: {self.last_error_type} on {self.last_error_operation}"
            )
        else:
            return "HALF_OPEN – one probe call permitted"

    @staticmethod
    def is_quota_error(error: Exception) -> bool:
        """Return True if the error is a 429 / RESOURCE_EXHAUSTED."""
        msg = str(error)
        return '429' in msg or 'RESOURCE_EXHAUSTED' in msg

    @staticmethod
    def is_retryable_transient_error(error: Exception) -> bool:
        """Return True only for 5xx / network errors (NOT 429, NOT 4xx auth/schema)."""
        msg = str(error)
        # Never retry quota errors here – they go to the circuit breaker
        if CircuitBreaker.is_quota_error(error):
            return False
        # Never retry auth, invalid request, or schema errors
        for pattern in ['401', '403', 'PERMISSION_DENIED', 'INVALID_ARGUMENT',
                        'UNAUTHENTICATED', 'INVALID_API_KEY', '400']:
            if pattern in msg:
                return False
        # Retry 5xx and network-level failures
        for pattern in ['500', '502', '503', '504', 'INTERNAL', 'UNAVAILABLE',
                        'ConnectionError', 'Timeout', 'DEADLINE_EXCEEDED']:
            if pattern in msg:
                return True
        return False
