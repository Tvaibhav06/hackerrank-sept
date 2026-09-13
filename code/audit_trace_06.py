"""
Trace the exact code path in PlanGenerator for request_06.
"""
import datetime, json, sys, os, csv
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader, Request
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from plan_generator import PlanGenerator
from ai_resolver import AIResolver

loader = DataLoader()
engine = ExchangeRateEngine()
builder = StateBuilder(engine)
detector = RecurrenceDetector()

uid = 'user_06'

with open('ai_cache.json', 'r') as f:
    ai_cache = json.load(f)
ai_facts = {}
for req_hash, resp in ai_cache.items():
    if isinstance(resp, dict) and 'action' in resp:
        if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
            ai_facts[resp['target_event_id']] = resp

resolver = AIResolver(loader.messages, loader.events_by_user)
resolved = resolver.resolve(ai_facts)

# Load actual sample request data
with open('../dataset/sample_requests.csv', encoding='utf-8') as f:
    samples = list(csv.DictReader(f))

s = [x for x in samples if x['request_id'] == 'request_06'][0]

req_date = datetime.date.fromisoformat(s['request_date'])
request = Request(
    request_id=s['request_id'],
    user_id=s['user_id'],
    request_date=req_date,
    request_type=s['request_type'],
    requested_amount=Decimal(s['requested_amount']),
    desired_completion_date=datetime.date.fromisoformat(s['desired_completion_date']),
    allows_partial_payment=(s['allows_partial_payment'].lower() == 'true'),
    request_text=s['request_text']
)

profile = loader.profiles[uid]
events = loader.events_by_user.get(uid, [])
opts = loader.payment_options.get('request_06', [])

print(f"Request date: {req_date}")
print(f"Requested amount: {request.requested_amount}")
print(f"Desired completion: {request.desired_completion_date}")
print(f"Allows partial: {request.allows_partial_payment}")
print(f"Payment methods user considers: {profile.payment_methods_user_will_consider}")
print(f"Categories willing to stop: {profile.expense_categories_user_is_willing_to_stop}")
print(f"Categories willing to reduce: {profile.expense_categories_user_is_willing_to_reduce}")
print()

state = builder.build(profile, events, resolved, req_date)
detector.apply_recurrences(state, uid)
f_engine = ForecastEngine(state, debits_first=False)

print(f"Engine: amount_safe_to_pay({request.requested_amount}) = {f_engine.amount_safe_to_pay(request.requested_amount)}")
print(f"Engine: earliest_date_for_full_payment({request.requested_amount}) = {f_engine.earliest_date_for_full_payment(request.requested_amount)}")
print()

gen = PlanGenerator(f_engine, request, profile, opts)
print(f"PlanGenerator baseline_amount_safe = {gen.baseline_amount_safe}")
print(f"PlanGenerator baseline_earliest_date = {gen.baseline_earliest_date}")
print()

plan = gen.generate_best_plan()
print(f"Final plan: {plan}")
print()

# The expected values
print(f"Expected amount_safe_to_pay: {s['amount_safe_to_pay']}")
print(f"Expected affordability_status: {s['affordability_status']}")
print(f"Expected method: {s['recommended_payment_method']}")
print(f"Expected spending_changes: {s['spending_changes_needed']}")
print(f"Expected payment_plan: {s['payment_plan']}")
print()

# Key question: our engine says amount_safe >= requested_amount,
# so it should be affordable_now. But ground truth says affordable_with_plan
# and amount_safe_to_pay = 603.30 < 620.40.
#
# This means the ground truth engine gets a LOWER amount_safe_to_pay.
# Our minimum headroom is 685.88, but the ground truth headroom is 603.30.
# The delta is 82.58.
#
# If we look at the projected events, we project the same 11 recurring patterns.
# But groceries (avg ~135/month) and dining (avg ~191/month) are NOT projected!
#
# 135/month grocery => ~4.50/day. Over the 10 days from Jan 3 to Jan 13, 
# that's ~45.00 in groceries.
# 191/month dining => ~6.37/day. Over 10 days, that's ~63.70.
#
# Actually the problem states that groceries and dining have 'fixed' flexibility.
# They CAN'T be reduced or stopped. But they should still be PROJECTED as expenses!
#
# The fact that we don't project them means our balance is too optimistic.

# Let's verify: what is the total monthly projected expenses vs actual monthly expenses?
from collections import defaultdict

monthly_hist_totals = defaultdict(Decimal)
for n in state.historical_events:
    if n.direction == 'debit':
        month_key = f"{n.date.year}-{n.date.month:02d}"
        monthly_hist_totals[month_key] += n.amount

print("Historical monthly debit totals:")
for month, total in sorted(monthly_hist_totals.items()):
    print(f"  {month}: {total}")

monthly_proj = Decimal(0)
for n in state.projected_events:
    if n.direction == 'debit' and n.date.month == 1 and n.date.year == 2026:
        monthly_proj += n.amount
print(f"\nProjected January 2026 debits: {monthly_proj}")
print(f"Avg historical monthly debits: {sum(monthly_hist_totals.values()) / len(monthly_hist_totals):.2f}")
print(f"Delta (not projected): {sum(monthly_hist_totals.values()) / len(monthly_hist_totals) - monthly_proj:.2f}")
