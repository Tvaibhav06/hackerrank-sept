"""
Deep analysis of recurring patterns that are NOT being detected for user_06.
Goal: find the missing EUR 82.58.
"""
import datetime, sys, os
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder

loader = DataLoader()
engine = ExchangeRateEngine()
builder = StateBuilder(engine)

uid = 'user_06'
req_date = datetime.date(2026, 1, 3)
events = loader.events_by_user.get(uid, [])

# Group ALL historical events by (category, description)
cat_groups = defaultdict(list)
for e in sorted(events, key=lambda x: x.event_date):
    if e.status == 'settled' and e.direction in ('debit', 'credit'):
        cat_groups[(e.category, e.description)].append(e)

print("=" * 100)
print("ALL RECURRING PATTERN CANDIDATES (including NOT detected)")
print("=" * 100)

undetected_total = Decimal(0)

for (cat, desc), evs in sorted(cat_groups.items()):
    if len(evs) < 2:
        continue
    
    evs_sorted = sorted(evs, key=lambda x: x.event_date)
    intervals = [(evs_sorted[i+1].event_date - evs_sorted[i].event_date).days 
                 for i in range(len(evs_sorted)-1)]
    avg_interval = sum(intervals) / len(intervals)
    last_date = evs_sorted[-1].event_date
    gap_to_req = (req_date - last_date).days
    
    amounts = [e.amount for e in evs_sorted]
    median = sorted(amounts)[len(amounts)//2]
    mx = max(amounts)
    
    # Check if our detector would pick it up
    is_monthly = 26 <= avg_interval <= 35
    is_biweekly = 13 <= avg_interval <= 15
    is_weekly = 6 <= avg_interval <= 8
    detected = (is_monthly or is_biweekly or is_weekly) and gap_to_req <= avg_interval + 15
    
    # Figure out next projected date
    if is_monthly:
        m = last_date.month + 1
        y = last_date.year
        if m > 12: m = 1; y += 1
        day = max(e.event_date.day for e in evs_sorted)
        try:
            next_date = datetime.date(y, m, min(day, 28))
        except:
            next_date = None
    elif is_weekly or is_biweekly:
        next_date = last_date + datetime.timedelta(days=int(avg_interval))
    else:
        next_date = last_date + datetime.timedelta(days=int(avg_interval))
    
    in_window = next_date and req_date <= next_date <= datetime.date(2026, 1, 13)
    
    status = "DETECTED" if detected else "NOT DETECTED"
    marker = " *** POSSIBLE MISSING ***" if not detected and in_window else ""
    
    if not detected and in_window:
        undetected_total += median
    
    print(f"  [{status}] {cat:20s} {desc:30s}")
    print(f"    n={len(evs):2d} intervals={intervals} avg={avg_interval:.1f}d "
          f"gap={gap_to_req}d last={last_date}")
    print(f"    amounts: {[str(a) for a in amounts]}")
    print(f"    median={median} max={mx} next_proj={next_date}"
          f"  in_jan3-13={in_window}{marker}")
    print()

print(f"\nTotal undetected recurring in Jan 3-13 window: {undetected_total}")
print()

# Now check: what events ACTUALLY happened between Dec 15 and Jan 3 (the recent gap)?
print("=" * 100)
print("EVENTS IN THE RECENT PERIOD (Dec 15 - Jan 3)")
print("=" * 100)
for e in sorted(events, key=lambda x: x.event_date):
    if datetime.date(2025, 12, 15) <= e.event_date <= datetime.date(2026, 1, 3):
        print(f"  {e.event_id} {e.event_date} {e.status:10s} {e.direction:6s} "
              f"{str(e.amount):>10s} {e.category:20s} {e.description}")

# Check what the ForecastEngine actually does
print()
print("=" * 100)
print("FORECAST ENGINE INTERNALS")
print("=" * 100)
from forecast import ForecastEngine
import inspect
print(inspect.getsource(ForecastEngine))
