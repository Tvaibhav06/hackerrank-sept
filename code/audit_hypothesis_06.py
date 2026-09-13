"""
Check debits_first=True vs debits_first=False for request_06.
Also check if the ground truth might use debits_first=True for amount_safe_to_pay.
And check if there are OTHER categories of recurring events we're missing entirely.
"""
import datetime, json, sys, os
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
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
req_date = datetime.date(2026, 1, 3)

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

# Test with both debits_first settings
for df_label, df_val in [("debits_first=True", True), ("debits_first=False", False)]:
    state = builder.build(profile, events, resolved, req_date)
    detector.apply_recurrences(state, uid)
    f = ForecastEngine(state, debits_first=df_val)
    safe = f.amount_safe_to_pay(Decimal('620.4'))
    earliest = f.earliest_date_for_full_payment(Decimal('620.4'))
    print(f"{df_label}: safe={safe} earliest={earliest}")

print()

# Now let's check: what if we ALSO project the debt_repayment category?
# Or what if there's a recurring event category we're totally missing?
print("=" * 80)
print("CHECK: All event categories for user_06")
print("=" * 80)
cat_counts = defaultdict(int)
for e in events:
    cat_counts[e.category] += 1
for cat, count in sorted(cat_counts.items()):
    print(f"  {cat:25s}: {count}")

# Check what recurring categories are projected vs historical
print()
print("=" * 80)
print("Categories in projected vs historical")
print("=" * 80)
state = builder.build(profile, events, resolved, req_date)
detector.apply_recurrences(state, uid)

proj_cats = defaultdict(list)
hist_cats = defaultdict(list)
for n in state.projected_events:
    proj_cats[n.category].append(n)
for n in state.historical_events:
    hist_cats[n.category].append(n)

all_cats = set(list(proj_cats.keys()) + list(hist_cats.keys()))
for cat in sorted(all_cats):
    h = len(hist_cats.get(cat, []))
    p = len(proj_cats.get(cat, []))
    marker = " *** NOT PROJECTED ***" if h > 0 and p == 0 else ""
    print(f"  {cat:25s}: hist={h:3d} proj={p:3d}{marker}")

# What's the total monthly spending in UNPROJECTED categories?
print()
print("=" * 80)
print("Monthly spending in unprojected categories")
print("=" * 80)
for cat in sorted(all_cats):
    if cat in proj_cats:
        continue
    evs = hist_cats.get(cat, [])
    if not evs:
        continue
    amounts = [e.amount for e in evs]
    dates = [e.date for e in evs]
    span = (max(dates) - min(dates)).days or 1
    monthly_avg = sum(amounts) / Decimal(span) * 30
    print(f"  {cat:25s}: {len(evs)} events, total={sum(amounts):>10}, "
          f"span={span}d, ~monthly={monthly_avg:.2f}")

# The big question: what if debits_first affects the balance on request_date?
# On 2026-01-03, there is a rent debit of 254.1 and NO credits.
# With debits_first=True: sim = 1942.4 - 254.1 = 1688.3, then +0 = 1688.3
# With debits_first=False: sim = 1942.4 + 0 - 254.1 = 1688.3
# Same result on this date.

# What about Jan 15 where salary and entertainment both occur?
# debits_first=True:  sim = X - 35.1, check, then + 1037.52
# debits_first=False: sim = X + 1037.52 - 35.1, check
# Different! But our minimum occurs on Jan 13, not Jan 15.

# Let me check: what if the problem considers 
# amount_safe_to_pay as available AFTER debiting today's events on request_date?
print()
print("=" * 80)
print("HYPOTHESIS: amount_safe_to_pay computed DIFFERENTLY")
print("=" * 80)
print("What if amount_safe_to_pay = balance - min_balance - today's_debits?")
state = builder.build(profile, events, resolved, req_date)
detector.apply_recurrences(state, uid)
# On Jan 3, the rent of 254.1 is projected
today_debits = Decimal(0)
for e in state.projected_events:
    if e.date == req_date and e.direction == 'debit':
        today_debits += e.amount
        print(f"  Today's debit: {e.amount} {e.category}/{e.description}")
print(f"  Total today debits: {today_debits}")
print(f"  Balance - min - today_debits = {state.start_balance} - {state.min_balance} - {today_debits} = {state.start_balance - state.min_balance - today_debits}")

# What if there should be a debt_repayment event?
print()
print("=" * 80)
print("CHECK: debt_repayment events")
print("=" * 80)
for e in events:
    if 'debt' in e.category:
        print(f"  {e.event_id} {e.event_date} {e.amount} {e.category} {e.description}")

# What if there's an uncategorized recurring expense?
# Check for grocery aggregation
print()
print("=" * 80)
print("GROCERY AGGREGATION ANALYSIS")
print("=" * 80)
grocery_events = [e for e in events if e.category == 'groceries' and e.status == 'settled']
grocery_events.sort(key=lambda x: x.event_date)
monthly_grocery = defaultdict(Decimal)
for e in grocery_events:
    month_key = f"{e.event_date.year}-{e.event_date.month:02d}"
    monthly_grocery[month_key] += e.amount

print("Monthly grocery spending:")
for month, total in sorted(monthly_grocery.items()):
    print(f"  {month}: {total}")

avg_monthly_grocery = sum(monthly_grocery.values()) / len(monthly_grocery)
print(f"  Average monthly grocery: {avg_monthly_grocery:.2f}")

# Check dining aggregation
print()
dining_events = [e for e in events if e.category == 'dining' and e.status == 'settled']
dining_events.sort(key=lambda x: x.event_date)
monthly_dining = defaultdict(Decimal)
for e in dining_events:
    month_key = f"{e.event_date.year}-{e.event_date.month:02d}"
    monthly_dining[month_key] += e.amount

print("Monthly dining spending:")
for month, total in sorted(monthly_dining.items()):
    print(f"  {month}: {total}")

avg_monthly_dining = sum(monthly_dining.values()) / len(monthly_dining)
print(f"  Average monthly dining: {avg_monthly_dining:.2f}")

# Transport aggregation
print()
transport_events = [e for e in events if e.category == 'transport' and e.status == 'settled']
monthly_transport = defaultdict(Decimal)
for e in transport_events:
    month_key = f"{e.event_date.year}-{e.event_date.month:02d}"
    monthly_transport[month_key] += e.amount

print("Monthly transport spending:")
for month, total in sorted(monthly_transport.items()):
    print(f"  {month}: {total}")

avg_monthly_transport = sum(monthly_transport.values()) / len(monthly_transport)
print(f"  Average monthly transport: {avg_monthly_transport:.2f}")
print(f"  Our projected monthly transport: {27.8 + 28.03 + 27.65} = {Decimal('27.8') + Decimal('28.03') + Decimal('27.65')}")
print(f"  Missing monthly transport: {avg_monthly_transport - (Decimal('27.8') + Decimal('28.03') + Decimal('27.65')):.2f}")
