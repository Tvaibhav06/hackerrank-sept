"""
Verify the dining-only daily aggregate hypothesis more precisely.
Try different methods of computing the daily rate.
"""
import datetime, json, sys, os
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from ai_resolver import AIResolver

loader = DataLoader()
engine = ExchangeRateEngine()
builder = StateBuilder(engine)
detector = RecurrenceDetector()

uid = 'user_06'
req_date = datetime.date(2026, 1, 3)
end_date = req_date + datetime.timedelta(days=90)

with open('ai_cache.json', 'r') as f:
    ai_cache = json.load(f)
ai_facts = {}
for req_hash, resp in ai_cache.items():
    if isinstance(resp, dict) and 'action' in resp:
        if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
            ai_facts[resp['target_event_id']] = resp

resolver = AIResolver(loader.messages, loader.events_by_user)
resolved = resolver.resolve(ai_facts)

profile = loader.profiles[uid]
events = loader.events_by_user.get(uid, [])

state = builder.build(profile, events, resolved, req_date)
detector.apply_recurrences(state, uid)

projected_cats_descs = set()
for n in state.projected_events:
    projected_cats_descs.add((n.category, n.description))

# Gather dining events not already projected
dining_events = []
for n in state.historical_events:
    if n.direction == 'debit' and n.category == 'dining':
        if (n.category, n.description) not in projected_cats_descs:
            dining_events.append(n)

dining_events.sort(key=lambda x: x.date)
print(f"Dining events: {len(dining_events)}")
for d in dining_events:
    print(f"  {d.date} {d.amount:>8} {d.description}")

total = sum(d.amount for d in dining_events)
first = dining_events[0].date
last = dining_events[-1].date
span_days = (last - first).days
print(f"\nTotal: {total}")
print(f"Span: {first} to {last} = {span_days} days")

# Method 1: total / span_days (daily rate)
daily_1 = total / span_days
print(f"\nMethod 1 (total/span): daily={daily_1}")

# Method 2: total / (span_days + 1) 
daily_2 = total / (span_days + 1)
print(f"Method 2 (total/(span+1)): daily={daily_2}")

# Method 3: monthly average then /30
monthly_totals = defaultdict(Decimal)
for d in dining_events:
    month_key = f"{d.date.year}-{d.date.month:02d}"
    monthly_totals[month_key] += d.amount

avg_monthly = sum(monthly_totals.values()) / len(monthly_totals)
daily_3 = avg_monthly / 30
print(f"Method 3 (avg_monthly/30): daily={daily_3:.6f}, monthly={avg_monthly:.2f}")

daily_3b = avg_monthly / Decimal('30.44')  # Average days in a month
print(f"Method 3b (avg_monthly/30.44): daily={daily_3b:.6f}")

# Method 4: Count of months * 30.5 as span
num_months = len(monthly_totals)
daily_4 = total / (num_months * Decimal('30.5'))
print(f"Method 4 (total/(months*30.5)): daily={daily_4:.6f}")

# Now simulate with each method
events_by_date = defaultdict(list)
for e in state.projected_events:
    events_by_date[e.date].append(e)

for label, daily in [
    ("M1: total/span", daily_1),
    ("M2: total/(span+1)", daily_2),
    ("M3: avg_monthly/30", daily_3),
    ("M3b: avg_monthly/30.44", daily_3b),
    ("M4: total/(months*30.5)", daily_4),
]:
    sim = state.start_balance
    min_headroom = sim - state.min_balance
    min_date = req_date
    
    curr = req_date
    while curr <= end_date:
        dd = daily  # Daily dining
        dc = Decimal(0)
        for e in events_by_date.get(curr, []):
            if e.direction == 'debit': dd += e.amount
            else: dc += e.amount
        sim += dc - dd
        headroom = sim - state.min_balance
        if headroom < min_headroom:
            min_headroom = headroom
            min_date = curr
        curr += datetime.timedelta(days=1)
    
    print(f"  {label:30s}: min_headroom={min_headroom:.2f} on {min_date} "
          f"(delta from 603.30 = {min_headroom - Decimal('603.30'):.2f})")

# Try: what if the daily rate should include Jan 3 itself in the first day?
# i.e., on the request_date, we ALSO debit the daily aggregate?
print()
print("=" * 80)
print("Check: should the daily aggregate be debited on the request_date?")
print("=" * 80)
# Already is in the simulation above. On Jan 3, we debit rent + daily aggregate.

# Let me check what happens if we use weekly dining instead of daily
print()
print("=" * 80)
print("WEEKLY DINING LUMP SUM TESTS")
print("=" * 80)
weekly_dining = total / (span_days / Decimal('7'))
print(f"Weekly dining: {weekly_dining:.2f}")

# When would weekly dining hit? Every 7 days from the last dining event.
last_dining_date = last
print(f"Last dining event: {last_dining_date}")

# Project weekly dining events
sim = state.start_balance
min_headroom = sim - state.min_balance
min_date = req_date

dining_proj_dates = set()
curr_d = last_dining_date
while True:
    curr_d += datetime.timedelta(days=7)
    if curr_d > end_date:
        break
    if curr_d >= req_date:
        dining_proj_dates.add(curr_d)

print(f"Weekly dining projection dates: {sorted(dining_proj_dates)}")

curr = req_date
while curr <= end_date:
    dd = Decimal(0)
    dc = Decimal(0)
    if curr in dining_proj_dates:
        dd += weekly_dining
    for e in events_by_date.get(curr, []):
        if e.direction == 'debit': dd += e.amount
        else: dc += e.amount
    sim += dc - dd
    headroom = sim - state.min_balance
    if headroom < min_headroom:
        min_headroom = headroom
        min_date = curr
    curr += datetime.timedelta(days=1)

print(f"  Weekly dining lump: min_headroom={min_headroom:.2f} on {min_date}")
