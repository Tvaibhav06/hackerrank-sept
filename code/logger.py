import datetime
from pathlib import Path
from config import LOG_FILE, USAGE_REPORT_FILE, EVALUATION_DIR


class UsageTracker:
    def __init__(self):
        # ── Successful API calls (tokens counted) ──
        self.vlm_calls = 0
        self.vlm_input_tokens = 0
        self.vlm_output_tokens = 0

        self.llm_calls = 0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0

        # ── Reliability counters ──
        self.total_attempted_calls = 0   # every time we try the API (incl. retries)
        self.successful_calls = 0        # calls that returned a valid response
        self.cache_hits = 0              # returned from cache, no API call made
        self.quota_errors = 0            # 429 / RESOURCE_EXHAUSTED
        self.transient_errors = 0        # 5xx / network
        self.permanent_errors = 0        # 4xx non-quota (auth, schema, etc.)
        self.retries = 0                 # retry attempts for transient errors
        self.budget_blocked = 0          # blocked by global budget cap
        self.breaker_blocked = 0         # blocked by circuit breaker

        # Approximate costs for Gemini Flash
        # $0.075 per 1M input tokens, $0.30 per 1M output tokens (for context < 128k)
        self.INPUT_COST_PER_M = 0.075
        self.OUTPUT_COST_PER_M = 0.30

    # ── Token tracking (only on success) ──────────────────────────────

    def add_vlm_usage(self, input_tokens: int, output_tokens: int):
        self.vlm_calls += 1
        self.vlm_input_tokens += input_tokens
        self.vlm_output_tokens += output_tokens

    def add_llm_usage(self, input_tokens: int, output_tokens: int):
        self.llm_calls += 1
        self.llm_input_tokens += input_tokens
        self.llm_output_tokens += output_tokens

    # ── Reliability tracking ──────────────────────────────────────────

    def record_attempt(self):
        """Record that an API call attempt was made."""
        self.total_attempted_calls += 1

    def record_success(self):
        self.successful_calls += 1

    def record_cache_hit(self):
        """A cache hit – no API call made."""
        self.cache_hits += 1

    def record_quota_error(self):
        self.quota_errors += 1

    def record_transient_error(self):
        self.transient_errors += 1

    def record_permanent_error(self):
        self.permanent_errors += 1

    def record_retry(self):
        self.retries += 1

    def record_budget_blocked(self):
        self.budget_blocked += 1

    def record_breaker_blocked(self):
        self.breaker_blocked += 1

    # ── Report generation ─────────────────────────────────────────────

    def generate_report(self, total_requests: int):
        EVALUATION_DIR.mkdir(parents=True, exist_ok=True)

        total_input = self.vlm_input_tokens + self.llm_input_tokens
        total_output = self.vlm_output_tokens + self.llm_output_tokens
        total_model_calls = self.vlm_calls + self.llm_calls

        cost = ((total_input / 1_000_000) * self.INPUT_COST_PER_M
                + (total_output / 1_000_000) * self.OUTPUT_COST_PER_M)

        avg_tokens_per_request = (total_input + total_output) / max(1, total_requests)
        avg_cost_per_request = cost / max(1, total_requests)

        report = f"""# Token Usage Report

## Models Used
- **VLM (Images):** Gemini Flash
- **LLM (Messages):** Gemini Flash

## Usage Totals
- **Total Model Calls:** {total_model_calls} ({self.vlm_calls} VLM, {self.llm_calls} LLM)
- **Total Input Tokens:** {total_input:,} ({self.vlm_input_tokens:,} VLM, {self.llm_input_tokens:,} LLM)
- **Total Output Tokens:** {total_output:,} ({self.vlm_output_tokens:,} VLM, {self.llm_output_tokens:,} LLM)
- **Total Tokens:** {total_input + total_output:,}

## Averages
- **Total Requests Evaluated:** {total_requests}
- **Average Tokens per Request:** {avg_tokens_per_request:,.1f}
- **Estimated Average Cost per Request:** ${avg_cost_per_request:.6f}

## Estimated Cost
- **Total Estimated Cost:** ${cost:.6f}

## Reliability Metrics
- **Total API Attempts:** {self.total_attempted_calls}
- **Successful Calls:** {self.successful_calls}
- **Cache Hits:** {self.cache_hits}
- **429 / Quota Errors:** {self.quota_errors}
- **Transient Errors (5xx/network):** {self.transient_errors}
- **Permanent Errors (4xx non-quota):** {self.permanent_errors}
- **Retries:** {self.retries}
- **Blocked by Budget Cap:** {self.budget_blocked}
- **Blocked by Circuit Breaker:** {self.breaker_blocked}
"""
        with open(USAGE_REPORT_FILE, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Usage report generated at {USAGE_REPORT_FILE}")


tracker = UsageTracker()


def log_agent_turn(summary: str, actions: list, tool_name: str = "Antigravity IDE"):
    """Append a per-turn entry to AGENTS.md log.txt."""
    timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat()

    actions_str = "\\n".join([f"* {a}" for a in actions])

    entry = f"""
## [{timestamp}] Agent Action

Agent Response Summary:
{summary}

Actions:
{actions_str}

Context:
tool={tool_name}
branch=main
repo_root={LOG_FILE.parent.absolute()}
worktree=main
parent_agent=none
"""
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(entry)
