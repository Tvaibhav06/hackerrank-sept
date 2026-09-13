"""
Check if dining events are EXACTLY weekly (every 7 days).
If so, the correct recurrence approach is weekly projection, not daily smoothing.
"""
import datetime, sys, os
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from ai_resolver import AIResolver
from forecast import ForecastEngine

loader = DataLoader()
engine = ExchangeRateEngine()
builder = StateBuilder(engine)
detector = RecurrenceDetector()

uid = 'user_06'
req_date = datetime.date(2026, 1, 3)
end_date = req_date + datetime.timedelta(days=90)

with open('ai_cache.json', 'r') as f:
    import json
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

# Check dining events - are they exactly weekly?
dining = [n for n in state.historical_events 
          if n.direction == 'debit' and n.category == 'dining']
dining.sort(key=lambda x: x.date)

print("Dining event intervals:")
for i in range(len(dining) - 1):
    gap = (dining[i+1].date - dining[i].date).days
    print(f"  {dining[i].date} -> {dining[i+1].date}: {gap} days "
          f"({dining[i].amount} {dining[i].description})")

# They ARE weekly! Every 7 days!
amounts = [d.amount for d in dining]
print(f"\nAmounts: min={min(amounts)} max={max(amounts)} median={sorted(amounts)[len(amounts)//2]}")
print(f"Count: {len(dining)}")

# The recurrence detector requires avg_interval in [6,8] for weekly.
# Let's check what happens if we relax to [6,8]:
intervals = [(dining[i+1].date - dining[i].date).days for i in range(len(dining)-1)]
avg = sum(intervals) / len(intervals)
print(f"Avg interval: {avg}")
print(f"All intervals: {intervals}")
print(f"All are 7? {all(i == 7 for i in intervals)}")

# YES! Dining is a perfect weekly recurrence. But our detector groups by
# (category, description), so each sub-type (Takeaway, Family dinner, etc.)
# has only 2-5 events with irregular intervals.
#
# The FIX: we should also detect category-level recurrences, not just
# (category, description)-level.

# What would happen if we project dining as a weekly recurrence?
# Use the median amount as the forecast.
median_dining = sorted(amounts)[len(amounts)//2]
print(f"\nMedian dining amount: {median_dining}")

# Project weekly dining from last event
last_dining = dining[-1]
print(f"Last dining event: {last_dining.date}")

# Now apply recurrences normally PLUS add weekly dining
detector.apply_recurrences(state, uid)

from data_loader import Event
from state_builder import EventNode

proj_dining_dates = []
curr_d = last_dining.date
while True:
    curr_d += datetime.timedelta(days=7)
    if curr_d > end_date:
        break
    if curr_d >= req_date:
        # Check if any projected event already covers this
        covered = any(e.category == 'dining' and abs((e.date - curr_d).days) <= 2 
                      for e in state.projected_events)
        if not covered:
            proj_dining_dates.append(curr_d)
            fake_e = Event(
                event_id=f"proj_dining_{curr_d.strftime('%Y%m%d')}",
                user_id=uid,
                event_type="projected",
                description="Weekly dining aggregate",
                category="dining",
                direction="debit",
                amount=median_dining,
                currency=state.currency,
                event_date=curr_d,
                settlement_date=curr_d,
                status='scheduled',
                linked_event_id=None,
                flexibility='fixed',
                minimum_allowed_amount=None
            )
            n = EventNode(fake_e)
            n.amount = median_dining
            n.source_event_id = last_dining.source_event_id
            state.projected_events.append(n)

print(f"Projected dining dates: {proj_dining_dates}")
print(f"Projected dining amount: {median_dining} each")

# Now compute amount_safe_to_pay
f_eng = ForecastEngine(state, debits_first=False)
safe = f_eng.amount_safe_to_pay(Decimal('620.4'))
earliest = f_eng.earliest_date_for_full_payment(Decimal('620.4'))
print(f"\nWith weekly dining projection:")
print(f"  amount_safe_to_pay = {safe}")
print(f"  earliest_date = {earliest}")
print(f"  Expected: 603.30")
print(f"  Delta: {safe - Decimal('603.30')}")

# Simulate to find exact minimum balance
events_by_date = defaultdict(list)
for e in state.projected_events:
    events_by_date[e.date].append(e)

sim = state.start_balance
min_bal = sim
min_date = req_date

curr = req_date
while curr <= end_date:
    dc = Decimal(0)
    dd = Decimal(0)
    for e in events_by_date.get(curr, []):
        if e.direction == 'credit': dc += e.amount
        else: dd += e.amount
    sim += dc - dd
    if sim < min_bal:
        min_bal = sim
        min_date = curr
    curr += datetime.timedelta(days=1)

headroom = min_bal - state.min_balance
print(f"\n  Min balance: {min_bal} on {min_date}")
print(f"  Headroom: {headroom}")

# Check: what if we also do the same for groceries?
print()
print("=" * 80)
print("CHECK GROCERIES AS WELL")
print("=" * 80)
grocery = [n for n in state.historical_events 
           if n.direction == 'debit' and n.category == 'groceries']
grocery.sort(key=lambda x: x.date)

print("Grocery event intervals:")
g_intervals = []
for i in range(len(grocery) - 1):
    gap = (grocery[i+1].date - grocery[i].date).days
    g_intervals.append(gap)
    print(f"  {grocery[i].date} -> {grocery[i+1].date}: {gap} days ({grocery[i].amount})")

g_avg = sum(g_intervals) / len(g_intervals)
print(f"\nAvg grocery interval: {g_avg:.1f} days")
print(f"Total grocery events: {len(grocery)}")

# Check transport sub-categories not projected
transport = [n for n in state.historical_events 
             if n.direction == 'debit' and n.category == 'transport'
             and (n.category, n.description) not in 
             {(e.category, e.description) for e in state.projected_events}]
transport.sort(key=lambda x: x.date)
print(f"\nUnprojected transport events: {len(transport)}")
t_intervals = []
for i in range(len(transport) - 1):
    gap = (transport[i+1].date - transport[i].date).days
    t_intervals.append(gap)

if t_intervals:
    t_avg = sum(t_intervals) / len(t_intervals)
    print(f"Avg unprojected transport interval: {t_avg:.1f} days")
