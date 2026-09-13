"""
Test: what if we project aggregate category-level recurring spending
for groceries, dining, transport (sub-categories not already projected)?

The hypothesis is that the ground truth projects average monthly spending
for categories that have consistent spending patterns, even if individual
transactions don't form strict weekly/monthly recurrences.
"""
import datetime, json, sys, os
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder, EventNode
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from ai_resolver import AIResolver
from data_loader import Event

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

# Calculate what categories are already projected
projected_cats_descs = set()
for n in state.projected_events:
    projected_cats_descs.add((n.category, n.description))

print("Already projected (category, description):")
for cd in sorted(projected_cats_descs):
    print(f"  {cd}")

# Calculate historical daily rate for unprojected spending by category
cat_spending = defaultdict(lambda: {'total': Decimal(0), 'events': [], 'first': None, 'last': None})
for n in state.historical_events:
    if n.direction == 'debit':
        key = n.category
        # Skip events whose (category, description) is already projected
        if (n.category, n.description) in projected_cats_descs:
            continue
        cat_spending[key]['total'] += n.amount
        cat_spending[key]['events'].append(n)
        if cat_spending[key]['first'] is None or n.date < cat_spending[key]['first']:
            cat_spending[key]['first'] = n.date
        if cat_spending[key]['last'] is None or n.date > cat_spending[key]['last']:
            cat_spending[key]['last'] = n.date

print("\nUnprojected category spending:")
for cat, data in sorted(cat_spending.items()):
    span = (data['last'] - data['first']).days or 1
    daily_rate = data['total'] / span
    monthly_rate = daily_rate * 30
    print(f"  {cat:20s}: {len(data['events']):3d} events, total={data['total']:>10}, "
          f"span={span:3d}d, daily={daily_rate:.4f}, monthly~={monthly_rate:.2f}")

# Test hypothesis: add monthly aggregate for unprojected categories
# Distribute as a daily amount across the 90-day window
print("\n" + "=" * 80)
print("HYPOTHESIS TEST: Add daily aggregate spending")
print("=" * 80)

total_daily_extra = Decimal(0)
for cat, data in cat_spending.items():
    if not data['events']:
        continue
    span = (data['last'] - data['first']).days or 1
    daily_rate = data['total'] / span
    total_daily_extra += daily_rate

print(f"Total daily unprojected spending: {total_daily_extra:.4f}")
print(f"Over 10 days (Jan 3-13): {total_daily_extra * 10:.2f}")
print(f"Over 30 days (one month): {total_daily_extra * 30:.2f}")

# Now let's simulate: what would be the minimum headroom if we added
# this daily spending as daily debit events?
sim = state.start_balance
min_headroom = sim - state.min_balance

events_by_date = defaultdict(list)
for e in state.projected_events:
    events_by_date[e.date].append(e)

curr = req_date
while curr <= end_date:
    daily_debits = total_daily_extra  # extra daily spending
    daily_credits = Decimal(0)
    
    for e in events_by_date.get(curr, []):
        if e.direction == 'debit':
            daily_debits += e.amount
        else:
            daily_credits += e.amount

    sim += daily_credits - daily_debits
    min_headroom = min(min_headroom, sim - state.min_balance)
    curr += datetime.timedelta(days=1)

print(f"\nWith daily aggregate spending:")
print(f"  min_headroom = {min_headroom:.2f}")
print(f"  Expected: 603.30")
print(f"  Delta from expected: {min_headroom - Decimal('603.30'):.2f}")

# Try different approaches
# Maybe not all categories should be projected. Let's try ONLY groceries + dining.
for test_cats in [
    ['dining'],
    ['groceries'],
    ['dining', 'groceries'],
    ['transport'],
    ['dining', 'groceries', 'transport'],
]:
    daily_extra = Decimal(0)
    for cat in test_cats:
        if cat in cat_spending and cat_spending[cat]['events']:
            span = (cat_spending[cat]['last'] - cat_spending[cat]['first']).days or 1
            daily_extra += cat_spending[cat]['total'] / span
    
    sim = state.start_balance
    min_headroom = sim - state.min_balance
    
    curr = req_date
    while curr <= end_date:
        dd = daily_extra
        dc = Decimal(0)
        for e in events_by_date.get(curr, []):
            if e.direction == 'debit': dd += e.amount
            else: dc += e.amount
        sim += dc - dd
        min_headroom = min(min_headroom, sim - state.min_balance)
        curr += datetime.timedelta(days=1)
    
    print(f"\n  With {test_cats}: min_headroom={min_headroom:.2f}, "
          f"daily_extra={daily_extra:.4f}, monthly={daily_extra*30:.2f}")

# Another approach: what if aggregate spending is projected as weekly lump sums?
print("\n" + "=" * 80)
print("WEEKLY LUMP SUM APPROACH")
print("=" * 80)

# Compute weekly averages for unprojected categories
for cat, data in sorted(cat_spending.items()):
    if not data['events']:
        continue
    span = (data['last'] - data['first']).days or 1
    weekly_rate = data['total'] / span * 7
    print(f"  {cat:20s}: weekly~={weekly_rate:.2f}")
