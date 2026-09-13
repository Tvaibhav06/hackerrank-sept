"""
AI evidence-extraction layer with circuit breaker, global budget, and safe caching.

Architecture:
    cache lookup → circuit-breaker gate → budget gate → throttle → API call
    on success: validate → cache → return
    on 429:     open breaker → return quota_blocked
    on 5xx:     bounded retry (up to MAX_RETRIES_PER_CALL) → return failure
    on 4xx:     no retry → return failure
"""

import os
import json
import time
from typing import Optional, Dict, Any

from pydantic import BaseModel, Field
from PIL import Image

from config import (
    CACHE_FILE,
    IMAGES_DIR,
    DEFAULT_GEMINI_MODEL,
    MAX_TOTAL_AI_CALLS,
    MAX_RETRIES_PER_CALL,
    MIN_SECONDS_BETWEEN_CALLS,
    MAX_BACKOFF_SECONDS,
)
from circuit_breaker import CircuitBreaker
from logger import tracker


# ── Output schemas ────────────────────────────────────────────────────

class MessageFact(BaseModel):
    action: str = Field(description="One of: CONFIRM, INCREASE, REDUCE, DELAY, CANCEL, PENDING")
    amount: float = Field(description="The new or modified amount mentioned. 0.0 if none", default=0.0)
    date: str = Field(description="The new or modified date mentioned (YYYY-MM-DD). 'NONE' if none", default="NONE")
    target_event_id: str = Field(description="The related event_id if explicitly mentioned, or 'NONE'", default="NONE")


class ImageFact(BaseModel):
    extracted_amount: float = Field(description="The exact final numerical amount extracted from the image.")


# ── Result wrapper ────────────────────────────────────────────────────

def _ok(data: dict, cached: bool = False) -> dict:
    """Wrap a successful result."""
    return {"status": "success", "cached": cached, "data": data}


def _quota_blocked() -> dict:
    return {"status": "quota_blocked", "cached": False, "error": "Gemini quota exhausted"}


def _error(msg: str) -> dict:
    return {"status": "error", "cached": False, "error": msg}


# ── Main class ────────────────────────────────────────────────────────

class AIInterface:
    def __init__(self, client=None):
        """
        Args:
            client: An optional pre-built genai.Client (for dependency injection / testing).
                    If None, a real client is constructed from GEMINI_API_KEY in .env.
        """
        if client is not None:
            self.client = client
        else:
            from dotenv import load_dotenv
            load_dotenv()
            from google import genai
            self.client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

        self.model = os.environ.get('GEMINI_MODEL', DEFAULT_GEMINI_MODEL)
        self.cache: Dict[str, Any] = self._load_cache()
        self.breaker = CircuitBreaker()

        # Global budget tracking (per-process)
        self._calls_made_this_run = 0
        self._last_call_time: float = 0.0

    # ── Cache I/O ─────────────────────────────────────────────────────

    def _load_cache(self) -> Dict[str, Any]:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self):
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2)

    def save_cache(self):
        """Public method to persist cache."""
        self._save_cache()

    # ── Throttle & budget ─────────────────────────────────────────────

    def _throttle(self):
        """Enforce MIN_SECONDS_BETWEEN_CALLS."""
        if self._last_call_time > 0:
            elapsed = time.time() - self._last_call_time
            wait = MIN_SECONDS_BETWEEN_CALLS - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_call_time = time.time()

    def _budget_ok(self) -> bool:
        """Check the global call budget."""
        import config as _cfg
        return self._calls_made_this_run < _cfg.MAX_TOTAL_AI_CALLS

    # ── Gate: cache → breaker → budget → throttle ─────────────────────

    def _gate(self, cache_key: str, operation: str):
        """
        Returns (allowed: bool, cached_data: dict | None, block_result: dict | None).
        Exactly one of cached_data or block_result is non-None when allowed is False.
        """
        # 1. Cache
        if cache_key in self.cache:
            tracker.record_cache_hit()
            return False, self.cache[cache_key], None

        # 2. Circuit breaker
        if not self.breaker.allow_request():
            tracker.record_breaker_blocked()
            remaining = self.breaker.seconds_until_half_open()
            print(f"[CircuitBreaker] BLOCKED {operation} – {self.breaker.status_summary()} (retry in {remaining:.0f}s)")
            return False, None, _quota_blocked()

        # 3. Budget
        if not self._budget_ok():
            tracker.record_budget_blocked()
            print(f"[Budget] BLOCKED {operation} – {self._calls_made_this_run}/{MAX_TOTAL_AI_CALLS} calls used")
            return False, None, _error(f"Global budget exhausted ({MAX_TOTAL_AI_CALLS} calls)")

        return True, None, None

    # ── Core API call with error handling ──────────────────────────────

    def _call_gemini(self, contents, schema, operation: str) -> Dict[str, Any]:
        """
        Make a single Gemini call with transient-error retry.
        Returns a result dict: _ok(), _quota_blocked(), or _error().
        """
        from google.genai import types

        last_error = None
        attempts = 1 + MAX_RETRIES_PER_CALL  # 1 original + N retries

        for attempt in range(attempts):
            if attempt > 0:
                tracker.record_retry()

            self._throttle()
            self._calls_made_this_run += 1
            tracker.record_attempt()

            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.0
                    )
                )

                result = json.loads(response.text)

                # Track tokens
                if schema == ImageFact:
                    tracker.add_vlm_usage(
                        response.usage_metadata.prompt_token_count,
                        response.usage_metadata.candidates_token_count
                    )
                else:
                    tracker.add_llm_usage(
                        response.usage_metadata.prompt_token_count,
                        response.usage_metadata.candidates_token_count
                    )

                tracker.record_success()
                self.breaker.record_success()
                return _ok(result)

            except Exception as e:
                last_error = e

                if CircuitBreaker.is_quota_error(e):
                    # 429 – open breaker, do NOT retry
                    tracker.record_quota_error()
                    self.breaker.record_quota_error(e, model=self.model, operation=operation)
                    print(f"[CircuitBreaker] OPENED on {operation}: {e}")
                    return _quota_blocked()

                if not CircuitBreaker.is_retryable_transient_error(e):
                    # Permanent error (auth, schema, invalid request) – do NOT retry
                    tracker.record_permanent_error()
                    print(f"[AI] Permanent error on {operation}: {e}")
                    return _error(str(e))

                # Transient error – retry with bounded backoff
                tracker.record_transient_error()
                backoff = min(2 ** (attempt + 1), MAX_BACKOFF_SECONDS)
                print(f"[AI] Transient error on {operation} (attempt {attempt+1}/{attempts}): {e}")
                if attempt < attempts - 1:
                    print(f"  Retrying in {backoff}s...")
                    time.sleep(backoff)

        return _error(f"All {attempts} attempts failed: {last_error}")

    # ── Public API: Image extraction ──────────────────────────────────

    def extract_image_amount(self, image_id: str, description: str, currency: str) -> Optional[float]:
        """
        Extract amount from an image. Returns the float amount on success/cache,
        or None on failure.
        """
        cache_key = f"img_{image_id}"
        operation = f"image:{image_id}"

        allowed, cached_data, block_result = self._gate(cache_key, operation)

        if cached_data is not None:
            return cached_data.get('extracted_amount')
        if block_result is not None:
            return None
        # allowed == True

        image_path = IMAGES_DIR / f"{image_id}.png"
        if not image_path.exists():
            print(f"Warning: Image {image_id} not found at {image_path}")
            return None

        prompt = (
            f"You are a strict data extraction tool. Look at this image representing a financial transaction "
            f"described as '{description}'. The currency is {currency}. "
            f"Extract ONLY the final, total numeric amount. Return it as a float."
        )

        img = Image.open(image_path)
        result = self._call_gemini([img, prompt], ImageFact, operation)

        if result['status'] == 'success':
            data = result['data']
            data['_metadata'] = {'description': description, 'currency': currency}
            self.cache[cache_key] = data
            self._save_cache()
            return float(data.get('extracted_amount', 0))

        return None

    # ── Public API: Message interpretation ────────────────────────────

    def interpret_message(self, message_id: str, text: str, source_type: str) -> Optional[Dict[str, Any]]:
        """
        Interpret a financial message. Returns the parsed fact dict on success/cache,
        or None on failure.
        """
        cache_key = f"msg_{message_id}"
        operation = f"message:{message_id}"

        allowed, cached_data, block_result = self._gate(cache_key, operation)

        if cached_data is not None:
            return cached_data
        if block_result is not None:
            return None
        # allowed == True

        prompt = f"""
You are a strict data extraction tool for financial messages.
Source type: {source_type}
Message text: {text}

Extract the underlying fact. The action must be EXACTLY one of: 
CONFIRM, INCREASE, REDUCE, DELAY, CANCEL, PENDING.

- CONFIRM: Solidifies a pending date/amount.
- INCREASE: Raises a recurring amount.
- REDUCE / TEMPORARY: Lowers a recurring amount. Use REDUCE.
- DELAY: Shifts an expected date.
- CANCEL: Terminates a recurring contract/expense.
- PENDING: Warns that an expected credit is not yet final.

Return the action, the new amount (if specified), the new date (if specified in YYYY-MM-DD), and the target_event_id if mentioned.
"""

        result = self._call_gemini(prompt, MessageFact, operation)

        if result['status'] == 'success':
            data = result['data']
            data['_metadata'] = {'text': text[:100] + '...'}
            self.cache[cache_key] = data
            self._save_cache()
            return data

        return None

    # ── Preprocessing entrypoint ──────────────────────────────────────

    def preprocess_all(self, events_needing_images: list, messages: list) -> Dict[str, Any]:
        """
        Run AI extraction over all items that need it.
        Returns a summary dict with counts.

        This is the ONLY place that should call Gemini in a production run.
        After this method completes, the financial engine can operate
        entirely from cache with zero API calls.
        """
        summary = {
            'images_processed': 0, 'images_cached': 0, 'images_failed': 0,
            'messages_processed': 0, 'messages_cached': 0, 'messages_failed': 0,
        }

        # Images
        for e in events_needing_images:
            cache_key = f"img_{e.image_id}"
            if cache_key in self.cache:
                summary['images_cached'] += 1
                continue
            amt = self.extract_image_amount(e.image_id, e.description, e.currency)
            if amt is not None:
                summary['images_processed'] += 1
            else:
                summary['images_failed'] += 1

        # Messages
        for m in messages:
            cache_key = f"msg_{m.message_id}"
            if cache_key in self.cache:
                summary['messages_cached'] += 1
                continue
            result = self.interpret_message(m.message_id, m.message_text, m.source_type)
            if result is not None:
                summary['messages_processed'] += 1
            else:
                summary['messages_failed'] += 1

        self._save_cache()
        return summary
