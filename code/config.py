import os
from pathlib import Path
from decimal import getcontext

# Configure decimal precision
getcontext().prec = 28

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / 'dataset'
CODE_DIR = PROJECT_ROOT / 'code'

# Input files
REQUESTS_FILE = DATASET_DIR / 'requests.csv'
PROFILES_FILE = DATASET_DIR / 'financial_profiles.csv'
EVENTS_FILE = DATASET_DIR / 'financial_events.csv'
RATES_FILE = DATASET_DIR / 'exchange_rates.csv'
PAYMENT_OPTIONS_FILE = DATASET_DIR / 'request_payment_options.csv'
MESSAGES_FILE = DATASET_DIR / 'messages.csv'
IMAGES_FILE = DATASET_DIR / 'images.csv'
IMAGES_DIR = DATASET_DIR / 'media' / 'images'

# Output files
OUTPUT_FILE = PROJECT_ROOT / 'output.csv'
LOG_FILE = PROJECT_ROOT / 'log.txt'
EVALUATION_DIR = CODE_DIR / 'evaluation'
USAGE_REPORT_FILE = EVALUATION_DIR / 'usage_report.md'
CACHE_FILE = CODE_DIR / 'ai_cache.json'

# Financial constants
FORECAST_DAYS = 90
MAX_SPENDING_CHANGES = 3

# Allowed Enums (per problem_statement.md)
AFFORDABILITY_STATUS = {
    'NOW': 'affordable_now',
    'WITH_PLAN': 'affordable_with_plan',
    'LATER': 'affordable_later',
    'NOT_AFFORDABLE': 'not_affordable'
}

PAYMENT_METHODS = {
    'FULL': 'full_payment',
    'PARTIAL': 'partial_payment',
    'INSTALLMENTS': 'installments',
    'WAIT': 'wait',
    'NOT_RECOMMENDED': 'not_recommended'
}

# ── AI / Gemini configuration ──────────────────────────────────────────
# Model (configurable via .env override GEMINI_MODEL)
DEFAULT_GEMINI_MODEL = 'gemini-3.6-flash'

# Global request budget
MAX_TOTAL_AI_CALLS = 300        # hard cap across entire run
MAX_RETRIES_PER_CALL = 2        # retries only for transient 5xx/network errors
MIN_SECONDS_BETWEEN_CALLS = 4   # minimum gap between any two API calls
MAX_BACKOFF_SECONDS = 120       # cap on exponential backoff

# Circuit-breaker
CIRCUIT_BREAKER_FILE = CODE_DIR / 'circuit_breaker_state.json'
CIRCUIT_BREAKER_SAFETY_MARGIN = 10  # seconds added to Retry-After
CIRCUIT_BREAKER_DEFAULT_COOLDOWN = 120  # seconds when no Retry-After provided

# Cache
CACHE_FILE = CODE_DIR / 'ai_cache.json'  # (already defined above, kept for clarity)

