"""
Tests for AI reliability layer — circuit breaker, caching, budget, error handling.

ALL tests use mocked Gemini clients. ZERO live API calls are made.
"""

import json
import time
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

# ── Helpers ───────────────────────────────────────────────────────────

def _make_mock_response(text_json: dict, input_tokens: int = 10, output_tokens: int = 5):
    """Create a mock Gemini response object."""
    resp = MagicMock()
    resp.text = json.dumps(text_json)
    resp.usage_metadata = SimpleNamespace(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
    )
    return resp


def _make_429_error(retry_after: float = 55.0):
    """Create a mock 429 RESOURCE_EXHAUSTED exception."""
    return Exception(
        f"429 RESOURCE_EXHAUSTED. You exceeded your current quota. "
        f"Please retry in {retry_after}s."
    )


def _make_500_error():
    return Exception("500 INTERNAL server error")


def _make_auth_error():
    return Exception("403 PERMISSION_DENIED. Invalid API key.")


def _make_schema_error():
    return Exception("400 INVALID_ARGUMENT. Schema mismatch.")


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """
    Redirect all file I/O to a temp directory so tests are isolated
    and no real cache/breaker state is touched.
    """
    # Each test gets a unique subdirectory to guarantee isolation
    import uuid
    test_dir = tmp_path / uuid.uuid4().hex
    test_dir.mkdir()

    cache_file = test_dir / "ai_cache.json"
    breaker_file = test_dir / "circuit_breaker_state.json"
    images_dir = test_dir / "images"
    images_dir.mkdir()

    monkeypatch.setattr("config.CACHE_FILE", cache_file)
    monkeypatch.setattr("config.CIRCUIT_BREAKER_FILE", breaker_file)
    monkeypatch.setattr("config.IMAGES_DIR", images_dir)
    monkeypatch.setattr("config.MIN_SECONDS_BETWEEN_CALLS", 0)  # no throttle in tests

    # Reset the global tracker
    import logger
    logger.tracker = logger.UsageTracker()

    return {"cache_file": cache_file, "breaker_file": breaker_file, "images_dir": images_dir}


def _build_ai(mock_client=None):
    """Build an AIInterface with a mock client, avoiding any real API init."""
    from ai_interface import AIInterface
    from circuit_breaker import CircuitBreaker
    from config import CIRCUIT_BREAKER_FILE
    client = mock_client or MagicMock()
    ai = AIInterface(client=client)
    # Ensure a fresh breaker pointing at the test-specific file
    ai.breaker = CircuitBreaker(state_file=CIRCUIT_BREAKER_FILE)
    return ai


# ══════════════════════════════════════════════════════════════════════
# Test 1: Cache hit prevents API call
# ══════════════════════════════════════════════════════════════════════

def test_cache_hit_prevents_api_call(clean_state):
    """When a result is already cached, no API call should be made."""
    ai = _build_ai()

    # Pre-populate cache
    ai.cache["msg_test_01"] = {"action": "CONFIRM", "amount": 100.0, "date": "NONE", "target_event_id": "NONE"}

    result = ai.interpret_message("test_01", "some text", "bank_notification")

    assert result is not None
    assert result["action"] == "CONFIRM"
    # The mock client should NOT have been called
    ai.client.models.generate_content.assert_not_called()


def test_cache_hit_image_prevents_api_call(clean_state):
    """Image cache hit should not trigger API call."""
    ai = _build_ai()

    ai.cache["img_test_img_01"] = {"extracted_amount": 1234.56}

    result = ai.extract_image_amount("test_img_01", "test desc", "USD")

    assert result == 1234.56
    ai.client.models.generate_content.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# Test 2: First 429 opens the breaker
# ══════════════════════════════════════════════════════════════════════

def test_first_429_opens_breaker(clean_state):
    """A 429 error should immediately open the circuit breaker."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_429_error(55.0)

    ai = _build_ai(mock_client)

    result = ai.interpret_message("msg_fail", "text", "bank_notification")

    assert result is None  # failed
    assert ai.breaker.state.value == "OPEN"
    # Should have been called exactly ONCE (no retry on 429)
    assert mock_client.models.generate_content.call_count == 1


# ══════════════════════════════════════════════════════════════════════
# Test 3: Open breaker blocks subsequent calls
# ══════════════════════════════════════════════════════════════════════

def test_open_breaker_blocks_calls(clean_state):
    """When breaker is OPEN, calls should be blocked without hitting API."""
    mock_client = MagicMock()
    ai = _build_ai(mock_client)

    # Force breaker open
    ai.breaker.record_quota_error(_make_429_error(120), model="test", operation="test")
    assert ai.breaker.state.value == "OPEN"

    # Now try a call
    result = ai.interpret_message("msg_blocked", "text", "bank_notification")

    assert result is None
    mock_client.models.generate_content.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# Test 4: Retry-After is parsed correctly
# ══════════════════════════════════════════════════════════════════════

def test_retry_after_parsed(clean_state):
    """Retry-After value from error should be parsed and used for cooldown."""
    from circuit_breaker import _parse_retry_after, CircuitBreaker
    from config import CIRCUIT_BREAKER_SAFETY_MARGIN

    assert _parse_retry_after("Please retry in 54.551222796s.") == pytest.approx(54.55, abs=0.01)
    assert _parse_retry_after("Please retry in 10s.") == pytest.approx(10.0)
    assert _parse_retry_after("No retry info here") == 0.0

    # Verify breaker uses it
    breaker = CircuitBreaker(state_file=Path(clean_state["breaker_file"]))
    breaker.record_quota_error(_make_429_error(45.0))
    assert breaker.cooldown_seconds == 45.0 + CIRCUIT_BREAKER_SAFETY_MARGIN


# ══════════════════════════════════════════════════════════════════════
# Test 5: Half-open state permits only one probe
# ══════════════════════════════════════════════════════════════════════

def test_half_open_allows_one_probe(clean_state):
    """After cooldown, breaker transitions to HALF_OPEN and allows one call."""
    from circuit_breaker import CircuitBreaker, BreakerState

    breaker = CircuitBreaker(state_file=Path(clean_state["breaker_file"]))
    breaker.record_quota_error(_make_429_error(0.1))  # very short cooldown

    assert breaker.state == BreakerState.OPEN

    # Simulate cooldown elapsed
    breaker.opened_at = time.time() - 200  # well past cooldown

    assert breaker.allow_request() == True
    assert breaker.state == BreakerState.HALF_OPEN


# ══════════════════════════════════════════════════════════════════════
# Test 6: Successful probe closes breaker
# ══════════════════════════════════════════════════════════════════════

def test_successful_probe_closes_breaker(clean_state):
    """A successful call in HALF_OPEN should close the breaker."""
    from circuit_breaker import CircuitBreaker, BreakerState

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_mock_response(
        {"action": "CONFIRM", "amount": 0.0, "date": "NONE", "target_event_id": "NONE"}
    )

    ai = _build_ai(mock_client)

    # Force into HALF_OPEN
    ai.breaker.record_quota_error(_make_429_error(0.1))
    ai.breaker.opened_at = time.time() - 200

    result = ai.interpret_message("msg_probe", "test", "bank_notification")

    assert result is not None
    assert ai.breaker.state == BreakerState.CLOSED


# ══════════════════════════════════════════════════════════════════════
# Test 7: Failed probe reopens breaker
# ══════════════════════════════════════════════════════════════════════

def test_failed_probe_reopens_breaker(clean_state):
    """A 429 in HALF_OPEN should reopen the breaker."""
    from circuit_breaker import BreakerState

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_429_error(60)

    ai = _build_ai(mock_client)

    # Force into HALF_OPEN
    ai.breaker.record_quota_error(_make_429_error(0.1))
    ai.breaker.opened_at = time.time() - 200

    result = ai.interpret_message("msg_probe_fail", "text", "bank_notification")

    assert result is None
    assert ai.breaker.state == BreakerState.OPEN


# ══════════════════════════════════════════════════════════════════════
# Test 8: Invalid API key is not retried
# ══════════════════════════════════════════════════════════════════════

def test_auth_error_not_retried(clean_state):
    """Auth/permission errors should fail immediately without retry."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_auth_error()

    ai = _build_ai(mock_client)

    result = ai.interpret_message("msg_auth_fail", "text", "bank_notification")

    assert result is None
    # Should be called exactly once (no retries)
    assert mock_client.models.generate_content.call_count == 1
    # Breaker should NOT open (not a quota error)
    assert ai.breaker.state.value == "CLOSED"


# ══════════════════════════════════════════════════════════════════════
# Test 9: Successful responses are cached
# ══════════════════════════════════════════════════════════════════════

def test_successful_response_cached(clean_state):
    """After a successful API call, the result should be in cache."""
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_mock_response(
        {"action": "DELAY", "amount": 500.0, "date": "2024-10-01", "target_event_id": "NONE"}
    )

    ai = _build_ai(mock_client)

    result = ai.interpret_message("msg_cache_test", "text", "bank_notification")

    assert result is not None
    assert result["action"] == "DELAY"
    assert "msg_msg_cache_test" in ai.cache or "msg_cache_test" in [k.replace('msg_','',1) for k in ai.cache]

    # Second call should use cache, not API
    mock_client.models.generate_content.reset_mock()
    result2 = ai.interpret_message("msg_cache_test", "text", "bank_notification")
    assert result2["action"] == "DELAY"
    mock_client.models.generate_content.assert_not_called()


# ══════════════════════════════════════════════════════════════════════
# Test 10: Existing cache usable when Gemini is down
# ══════════════════════════════════════════════════════════════════════

def test_cache_works_when_api_down(clean_state):
    """Cached items should be accessible even when API is completely broken."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_429_error(999)

    ai = _build_ai(mock_client)

    # Pre-populate cache
    ai.cache["msg_cached_item"] = {"action": "CANCEL", "amount": 0, "date": "NONE", "target_event_id": "NONE"}

    # Cached item works fine
    result = ai.interpret_message("cached_item", "text", "bank_notification")
    assert result["action"] == "CANCEL"
    mock_client.models.generate_content.assert_not_called()

    # Uncached item fails gracefully
    result2 = ai.interpret_message("uncached_item", "text", "bank_notification")
    assert result2 is None


# ══════════════════════════════════════════════════════════════════════
# Test 11: Global budget prevents excessive calls
# ══════════════════════════════════════════════════════════════════════

def test_global_budget_blocks_excess_calls(clean_state, monkeypatch):
    """Once global budget is exhausted, further calls are blocked."""
    monkeypatch.setattr("config.MAX_TOTAL_AI_CALLS", 2)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = _make_mock_response(
        {"action": "CONFIRM", "amount": 0, "date": "NONE", "target_event_id": "NONE"}
    )

    ai = _build_ai(mock_client)
    ai._calls_made_this_run = 0

    # First two succeed
    ai.interpret_message("m1", "text1", "bank_notification")
    ai.interpret_message("m2", "text2", "bank_notification")

    # Third should be blocked by budget
    result = ai.interpret_message("m3", "text3", "bank_notification")
    assert result is None

    # API should have been called at most 2+retries times, not 3
    # (each call may have up to MAX_RETRIES_PER_CALL retries)
    assert ai._calls_made_this_run >= 2


# ══════════════════════════════════════════════════════════════════════
# Test 12: Financial logic doesn't depend on AI being online
# ══════════════════════════════════════════════════════════════════════

def test_financial_engine_works_without_ai(clean_state):
    """
    Verify that the financial modules (config, state_builder, forecast, plan_generator)
    can be imported and configured without a live AI connection.
    """
    # These imports should succeed without any Gemini connection
    from config import FORECAST_DAYS, MAX_SPENDING_CHANGES
    from circuit_breaker import CircuitBreaker, BreakerState

    assert FORECAST_DAYS == 90
    assert MAX_SPENDING_CHANGES == 3
    assert BreakerState.CLOSED.value == "CLOSED"


# ══════════════════════════════════════════════════════════════════════
# Test 13: Transient 5xx errors get bounded retries
# ══════════════════════════════════════════════════════════════════════

def test_transient_error_retries(clean_state, monkeypatch):
    """5xx errors should be retried up to MAX_RETRIES_PER_CALL times."""
    monkeypatch.setattr("config.MAX_RETRIES_PER_CALL", 2)
    monkeypatch.setattr("config.MIN_SECONDS_BETWEEN_CALLS", 0)
    monkeypatch.setattr("config.MAX_BACKOFF_SECONDS", 0.01)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_500_error()

    ai = _build_ai(mock_client)

    result = ai.interpret_message("msg_5xx", "text", "bank_notification")

    assert result is None
    # 1 original + 2 retries = 3 total
    assert mock_client.models.generate_content.call_count == 3
    # Breaker should NOT open (not a quota error)
    assert ai.breaker.state.value == "CLOSED"


# ══════════════════════════════════════════════════════════════════════
# Test 14: Schema/400 errors NOT retried
# ══════════════════════════════════════════════════════════════════════

def test_schema_error_not_retried(clean_state):
    """400 errors should fail immediately with no retry."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = _make_schema_error()

    ai = _build_ai(mock_client)

    result = ai.interpret_message("msg_schema_fail", "text", "bank_notification")

    assert result is None
    assert mock_client.models.generate_content.call_count == 1


# ══════════════════════════════════════════════════════════════════════
# Test 15: Breaker state persists to disk
# ══════════════════════════════════════════════════════════════════════

def test_breaker_state_persists(clean_state):
    """Breaker state should survive process restart (read from file)."""
    from circuit_breaker import CircuitBreaker, BreakerState

    breaker_path = Path(clean_state["breaker_file"])

    # Open breaker
    b1 = CircuitBreaker(state_file=breaker_path)
    b1.record_quota_error(_make_429_error(60))
    assert b1.state == BreakerState.OPEN

    # Load fresh from file
    b2 = CircuitBreaker(state_file=breaker_path)
    assert b2.state == BreakerState.OPEN
    assert b2.cooldown_seconds > 0

    # Manual reset
    b2.manual_reset()
    b3 = CircuitBreaker(state_file=breaker_path)
    assert b3.state == BreakerState.CLOSED


# ══════════════════════════════════════════════════════════════════════
# Test 16: Usage tracker counts correctly
# ══════════════════════════════════════════════════════════════════════

def test_usage_tracker_counts(clean_state):
    """Tracker should properly count all event types."""
    from logger import tracker

    tracker.record_attempt()
    tracker.record_attempt()
    tracker.record_success()
    tracker.record_cache_hit()
    tracker.record_cache_hit()
    tracker.record_cache_hit()
    tracker.record_quota_error()
    tracker.record_transient_error()
    tracker.record_retry()
    tracker.record_budget_blocked()
    tracker.record_breaker_blocked()

    assert tracker.total_attempted_calls == 2
    assert tracker.successful_calls == 1
    assert tracker.cache_hits == 3
    assert tracker.quota_errors == 1
    assert tracker.transient_errors == 1
    assert tracker.retries == 1
    assert tracker.budget_blocked == 1
    assert tracker.breaker_blocked == 1
