"""
Full end-to-end audit of request_06 / user_06.
Traces every event, balance change, and projection to find the EUR 82.58 discrepancy.
"""
import datetime, json, csv, sys, os
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder, EventNode
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

profile = loader.profiles[uid]
events = loader.events_by_user.get(uid, [])

# ── 1. Profile ──
print("=" * 80)
print("SECTION 1: USER PROFILE")
print("=" * 80)
print(f"  user_id:                  {profile.user_id}")
print(f"  home_currency:            {profile.home_currency}")
print(f"  current_available_balance:{profile.current_available_balance}")
print(f"  minimum_balance_to_keep:  {profile.minimum_balance_to_keep}")
print()

# ── 2. All raw financial events ──
print("=" * 80)
print("SECTION 2: ALL RAW FINANCIAL EVENTS")
print("=" * 80)
for e in sorted(events, key=lambda x: x.event_date):
    linked = e.linked_event_id or ""
    print(f"  {e.event_id:12s} {e.event_date} {e.status:10s} {e.direction:6s} "
          f"{str(e.amount):>12s} {e.currency:4s} {e.category:20s} {e.description:30s} "
          f"flex={e.flexibility or 'N/A':12s} min={e.minimum_allowed_amount or 'N/A'} linked={linked}")
print()

# ── 3. Messages for user_06 ──
print("=" * 80)
print("SECTION 3: MESSAGES FOR USER_06")
print("=" * 80)
for m in loader.messages:
    if m.user_id == uid:
        print(f"  {m.message_id} | related_event={m.related_event_id or 'NONE'} | "
              f"source={m.source_type}")
        print(f"    text: {m.message_text[:120]}...")
print()

# ── 4. AI Facts ──
print("=" * 80)
print("SECTION 4: AI FACTS (from cache)")
print("=" * 80)
with open('ai_cache.json', 'r') as f:
    ai_cache = json.load(f)
ai_facts = {}
for req_hash, resp in ai_cache.items():
    if isinstance(resp, dict) and 'action' in resp:
        if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
            ai_facts[resp['target_event_id']] = resp

print(f"  Total AI facts: {len(ai_facts)}")
for tid, fact in ai_facts.items():
    print(f"  {tid}: action={fact['action']} amount={fact.get('amount')} date={fact.get('date')}")

resolver = AIResolver(loader.messages, loader.events_by_user)
resolved = resolver.resolve(ai_facts)
print(f"\n  After resolution:")
for k, v in resolved.items():
    print(f"  {v.get('original_target_id',k)} -> {k}: action={v['action']} amount={v.get('amount')}")
print()

# ── 5. Linked event resolution ──
print("=" * 80)
print("SECTION 5: LINKED EVENT RESOLUTION")
print("=" * 80)
resolved_dict = builder._resolve_linked_events(events)
print(f"  Events after link resolution: {len(resolved_dict)} (from {len(events)} raw)")
for eid, e in sorted(resolved_dict.items(), key=lambda x: x[1].event_date):
    print(f"  {e.event_id:12s} {e.event_date} {e.status:10s} {e.direction:6s} "
          f"{str(e.amount):>12s} {e.currency:4s} {e.category:20s} {e.description}")
print()

# ── 6. Build state and apply recurrences ──
print("=" * 80)
print("SECTION 6: STATE BUILD + RECURRENCE DETECTION")
print("=" * 80)
state = builder.build(profile, events, resolved, req_date)
print(f"  Start balance after pending debits: {state.start_balance}")
print(f"  Min balance: {state.min_balance}")
print(f"  Historical events: {len(state.historical_events)}")
print(f"  Projected (pre-recurrence): {len(state.projected_events)}")

for n in sorted(state.projected_events, key=lambda x: x.date):
    print(f"    {n.date} {n.category:20s} {n.direction:6s} {n.amount:>12} {n.description}")

print(f"\n  Applying recurrences...")
stats = detector.apply_recurrences(state, uid)
print(f"  Detected patterns: {stats['detected_patterns']}")
print(f"  Total projected amount: {stats['total_projected_amount']}")
for ex in stats['examples']:
    print(f"    {ex['category']:20s} {ex['description']:30s} interval={ex['interval']:.1f} "
          f"amt={ex['amount']} dir={ex['direction']}")

print(f"\n  All projected events after recurrence ({len(state.projected_events)}):")
for n in sorted(state.projected_events, key=lambda x: (x.date, x.direction)):
    src = getattr(n, 'source_event_id', '?')
    print(f"    {n.date} {n.category:20s} {n.direction:6s} {n.amount:>12} "
          f"{n.description:30s} src={src}")
print()

# ── 7. Daily cash-flow simulation ──
print("=" * 80)
print("SECTION 7: DAILY CASH-FLOW AUDIT (90 days)")
print("=" * 80)

sorted_events = sorted(state.projected_events, key=lambda x: (x.date, 0 if x.direction == 'debit' else 1))
balance = state.start_balance
min_balance_seen = balance
min_balance_date = req_date

print(f"  {'Date':12s} {'Inflows':>12s} {'Outflows':>12s} {'Balance':>14s} "
      f"{'Headroom':>12s}  Events")
print(f"  {req_date}  {'':>12s} {'':>12s} {balance:>14}  "
      f"{balance - state.min_balance:>12}  [START]")

# Group events by date
date_events = defaultdict(list)
for e in sorted_events:
    if req_date <= e.date <= end_date:
        date_events[e.date].append(e)

curr = req_date
while curr <= end_date:
    if curr in date_events:
        day_in = Decimal(0)
        day_out = Decimal(0)
        descs = []
        for e in date_events[curr]:
            if e.direction == 'credit':
                day_in += e.amount
                descs.append(f"+{e.amount} {e.category}/{e.description}")
            elif e.direction == 'debit':
                day_out += e.amount
                descs.append(f"-{e.amount} {e.category}/{e.description}")
        balance = balance + day_in - day_out
        headroom = balance - state.min_balance
        
        in_str = str(day_in) if day_in else ""
        out_str = str(day_out) if day_out else ""
        print(f"  {curr}  {in_str:>12s} {out_str:>12s} {balance:>14}  "
              f"{headroom:>12}  {'; '.join(descs)}")
        
        if balance < min_balance_seen:
            min_balance_seen = balance
            min_balance_date = curr
    curr += datetime.timedelta(days=1)

print()
print(f"  MINIMUM BALANCE: {min_balance_seen} on {min_balance_date}")
print(f"  HEADROOM AT MIN: {min_balance_seen - state.min_balance}")
print(f"  amount_safe_to_pay = headroom = {min_balance_seen - state.min_balance}")
print()

# ── 8. Compare with expected ──
print("=" * 80)
print("SECTION 8: COMPARISON WITH GROUND TRUTH")
print("=" * 80)
expected_safe = Decimal('603.30')
our_safe = min_balance_seen - state.min_balance
delta = our_safe - expected_safe
print(f"  Our amount_safe_to_pay:      {our_safe}")
print(f"  Expected amount_safe_to_pay: {expected_safe}")
print(f"  Delta:                       {delta}")
print()

# ── 9. Check: are there any events we might be missing? ──
print("=" * 80)
print("SECTION 9: MISSING EVENT ANALYSIS")
print("=" * 80)

# Check all events that were excluded by link resolution or status
all_event_ids = {e.event_id for e in events}
resolved_ids = set(resolved_dict.keys())
excluded = all_event_ids - resolved_ids
print(f"  Events excluded by link resolution: {len(excluded)}")
for eid in sorted(excluded):
    e = next(ev for ev in events if ev.event_id == eid)
    print(f"    {e.event_id} {e.event_date} {e.status} {e.direction} {e.amount} "
          f"{e.category} {e.description} linked={e.linked_event_id}")

# Check non-cash events
noncash = [e for e in events if e.direction == 'non_cash']
print(f"\n  Non-cash events: {len(noncash)}")
for e in noncash:
    print(f"    {e.event_id} {e.event_date} {e.status} {e.description}")

# Check if any historical events have amounts close to 82.58
print(f"\n  Events with amounts near 82.58:")
for e in events:
    if e.amount and abs(float(e.amount) - 82.58) < 5:
        print(f"    {e.event_id} {e.event_date} {e.amount} {e.category} {e.description}")

# ── 10. Check variable spending forecasting ──
print()
print("=" * 80)
print("SECTION 10: VARIABLE SPENDING AMOUNTS")
print("=" * 80)
# Group historical events by (category, description)
cat_groups = defaultdict(list)
for n in state.historical_events:
    cat_groups[(n.category, n.description)].append(n)

for (cat, desc), evs in sorted(cat_groups.items()):
    evs_sorted = sorted(evs, key=lambda x: x.date)
    amounts = [e.amount for e in evs_sorted]
    if len(amounts) >= 2:
        median = sorted(amounts)[len(amounts)//2]
        mx = max(amounts)
        mn = min(amounts)
        last2_same = amounts[-1] == amounts[-2] if len(amounts) >= 2 else False
        print(f"  {cat:20s} {desc:30s} n={len(amounts):2d} "
              f"min={mn:>10} max={mx:>10} med={median:>10} last2same={last2_same} "
              f"flex={evs_sorted[0].flexibility or 'N/A'}")
