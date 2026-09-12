import datetime
from pathlib import Path
from config import LOG_FILE, USAGE_REPORT_FILE, EVALUATION_DIR

class UsageTracker:
    def __init__(self):
        self.vlm_calls = 0
        self.vlm_input_tokens = 0
        self.vlm_output_tokens = 0
        
        self.llm_calls = 0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0
        
        # Approximate costs for Gemini 2.5 Flash
        # $0.075 per 1M input tokens, $0.30 per 1M output tokens (for context < 128k)
        self.INPUT_COST_PER_M = 0.075
        self.OUTPUT_COST_PER_M = 0.30

    def add_vlm_usage(self, input_tokens: int, output_tokens: int):
        self.vlm_calls += 1
        self.vlm_input_tokens += input_tokens
        self.vlm_output_tokens += output_tokens

    def add_llm_usage(self, input_tokens: int, output_tokens: int):
        self.llm_calls += 1
        self.llm_input_tokens += input_tokens
        self.llm_output_tokens += output_tokens

    def generate_report(self, total_requests: int):
        EVALUATION_DIR.mkdir(parents=True, exist_ok=True)
        
        total_input = self.vlm_input_tokens + self.llm_input_tokens
        total_output = self.vlm_output_tokens + self.llm_output_tokens
        total_calls = self.vlm_calls + self.llm_calls
        
        cost = (total_input / 1_000_000) * self.INPUT_COST_PER_M + (total_output / 1_000_000) * self.OUTPUT_COST_PER_M
        
        avg_tokens_per_request = (total_input + total_output) / max(1, total_requests)
        avg_cost_per_request = cost / max(1, total_requests)
        
        report = f"""# Token Usage Report

## Models Used
- **VLM (Images):** Gemini 2.5 Flash
- **LLM (Messages):** Gemini 2.5 Flash

## Usage Totals
- **Total Model Calls:** {total_calls} ({self.vlm_calls} VLM, {self.llm_calls} LLM)
- **Total Input Tokens:** {total_input:,} ({self.vlm_input_tokens:,} VLM, {self.llm_input_tokens:,} LLM)
- **Total Output Tokens:** {total_output:,} ({self.vlm_output_tokens:,} VLM, {self.llm_output_tokens:,} LLM)
- **Total Tokens:** {total_input + total_output:,}

## Averages
- **Total Requests Evaluated:** {total_requests}
- **Average Tokens per Request:** {avg_tokens_per_request:,.1f}
- **Estimated Average Cost per Request:** ${avg_cost_per_request:.6f}

## Estimated Cost
- **Total Estimated Cost:** ${cost:.6f}
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
