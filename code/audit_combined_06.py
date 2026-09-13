"""
Test: project BOTH weekly dining AND 10-day grocery recurrence at category level.
Also test groceries alone.
"""
import datetime, json, sys, os, copy
from decimal import Decimal
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader, Event
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder, EventNode
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from ai_resolver import AIResolver

loader = DataLoader()
engine = ExchangeRateEngine()
builder = StateBuilder(engine)

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

def build_state():
    state = builder.build(profile, events, resolved, req_date)
    detector = RecurrenceDetector()
    detector.apply_recurrences(state, uid)
    return state

def add_category_recurrence(state, category, interval_days, median_amt, last_event_date, src_event_id):
    """Add projected events for a category-level recurrence."""
    curr_d = last_event_date
    added = []
    while True:
        curr_d += datetime.timedelta(days=interval_days)
        if curr_d > end_date:
            break
        if curr_d >= req_date:
            # Check duplicate suppression
            covered = any(e.category == category and abs((e.date - curr_d).days) <= 2 
                          for e in state.projected_events)
            if not covered:
                fake_e = Event(
                    event_id=f"proj_{category}_{curr_d.strftime('%Y%m%d')}",
                    user_id=uid,
                    event_type="projected",
                    description=f"Recurring {category} aggregate",
                    category=category,
                    direction="debit",
                    amount=median_amt,
                    currency=state.currency,
                    event_date=curr_d,
                    settlement_date=curr_d,
                    status='scheduled',
                    linked_event_id=None,
                    flexibility='fixed',
                    minimum_allowed_amount=None
                )
                n = EventNode(fake_e)
                n.amount = median_amt
                n.source_event_id = src_event_id
                state.projected_events.append(n)
                added.append(curr_d)
    return added

def compute_headroom(state, requested=Decimal('620.4')):
    f_eng = ForecastEngine(state, debits_first=False)
    safe = f_eng.amount_safe_to_pay(requested)
    
    events_by_date = defaultdict(list)
    for e in state.projected_events:
        events_by_date[e.date].append(e)
    
    sim = state.start_balance
    min_bal = sim
    min_date = req_date
    
    curr = req_date
    while curr <= end_date:
        dc = dd = Decimal(0)
        for e in events_by_date.get(curr, []):
            if e.direction == 'credit': dc += e.amount
            else: dd += e.amount
        sim += dc - dd
        if sim < min_bal:
            min_bal = sim
            min_date = curr
        curr += datetime.timedelta(days=1)
    
    headroom = min_bal - state.min_balance
    return safe, headroom, min_date

# Baseline (no category-level recurrence)
state0 = build_state()
safe0, hr0, md0 = compute_headroom(state0)
print(f"Baseline: safe={safe0}, headroom={hr0}, min_date={md0}")

# Dining only (weekly, median=46.84)
state1 = build_state()
dining_hist = sorted([n for n in state1.historical_events if n.category == 'dining'], key=lambda x: x.date)
dining_median = sorted([d.amount for d in dining_hist])[len(dining_hist)//2]
d1 = add_category_recurrence(state1, 'dining', 7, dining_median, dining_hist[-1].date, dining_hist[-1].source_event_id)
safe1, hr1, md1 = compute_headroom(state1)
print(f"+ Dining weekly (median={dining_median}): safe={safe1}, headroom={hr1}, min_date={md1}, dates={d1}")

# Groceries only (10-day, median=46.22)
state2 = build_state()
grocery_hist = sorted([n for n in state2.historical_events if n.category == 'groceries'], key=lambda x: x.date)
grocery_median = sorted([d.amount for d in grocery_hist])[len(grocery_hist)//2]
g2 = add_category_recurrence(state2, 'groceries', 10, grocery_median, grocery_hist[-1].date, grocery_hist[-1].source_event_id)
safe2, hr2, md2 = compute_headroom(state2)
print(f"+ Groceries 10-day (median={grocery_median}): safe={safe2}, headroom={hr2}, min_date={md2}, dates={g2}")

# Both dining + groceries
state3 = build_state()
dining_hist3 = sorted([n for n in state3.historical_events if n.category == 'dining'], key=lambda x: x.date)
grocery_hist3 = sorted([n for n in state3.historical_events if n.category == 'groceries'], key=lambda x: x.date)
dining_median3 = sorted([d.amount for d in dining_hist3])[len(dining_hist3)//2]
grocery_median3 = sorted([d.amount for d in grocery_hist3])[len(grocery_hist3)//2]
add_category_recurrence(state3, 'dining', 7, dining_median3, dining_hist3[-1].date, dining_hist3[-1].source_event_id)
add_category_recurrence(state3, 'groceries', 10, grocery_median3, grocery_hist3[-1].date, grocery_hist3[-1].source_event_id)
safe3, hr3, md3 = compute_headroom(state3)
print(f"+ Both dining+groceries: safe={safe3}, headroom={hr3}, min_date={md3}")

# Test different dining amounts
print()
print("Trying different dining forecast amounts:")
for test_amt in [Decimal('45.88'), Decimal('45.879'), Decimal('45.00'), Decimal('47.79'), Decimal('44.00'),
                 Decimal('45.50'), Decimal('46.00'), Decimal('46.50'), Decimal('47.00'), Decimal('48.00'),
                 Decimal('48.36'), Decimal('45.88')]:
    st = build_state()
    dh = sorted([n for n in st.historical_events if n.category == 'dining'], key=lambda x: x.date)
    add_category_recurrence(st, 'dining', 7, test_amt, dh[-1].date, dh[-1].source_event_id)
    s, h, m = compute_headroom(st)
    print(f"  dining_amt={test_amt}: headroom={h:.2f}")

# What amount gives exactly 603.30?
# headroom = balance - min_balance
# With dining projected, minimum balance is at Jan 13.
# On Jan 4 and Jan 11, dining events occur BEFORE Jan 13.
# 685.88 - 2 * dining_amt = 603.30
# 2 * dining_amt = 82.58
# dining_amt = 41.29
print()
print("Reverse calculation: 685.88 - 603.30 = 82.58")
print("If 2 dining events before min date: 82.58 / 2 = 41.29 per event")
state4 = build_state()
dh4 = sorted([n for n in state4.historical_events if n.category == 'dining'], key=lambda x: x.date)
add_category_recurrence(state4, 'dining', 7, Decimal('41.29'), dh4[-1].date, dh4[-1].source_event_id)
s4, h4, m4 = compute_headroom(state4)
print(f"  dining_amt=41.29: headroom={h4:.2f}")

# What about mean instead of median?
dining_amounts = [d.amount for d in dining_hist]
mean_dining = sum(dining_amounts) / len(dining_amounts)
print(f"\nMean dining: {mean_dining:.4f}")
state5 = build_state()
dh5 = sorted([n for n in state5.historical_events if n.category == 'dining'], key=lambda x: x.date)
add_category_recurrence(state5, 'dining', 7, mean_dining, dh5[-1].date, dh5[-1].source_event_id)
s5, h5, m5 = compute_headroom(state5)
print(f"  dining_amt=mean({mean_dining:.2f}): headroom={h5:.2f}, min_date={m5}")

# What about the last 4 weeks average?
last_4_dining = [d.amount for d in dining_hist[-4:]]
avg_last_4 = sum(last_4_dining) / len(last_4_dining)
print(f"\nLast 4 dining avg: {avg_last_4:.4f}")
state6 = build_state()
dh6 = sorted([n for n in state6.historical_events if n.category == 'dining'], key=lambda x: x.date)
add_category_recurrence(state6, 'dining', 7, avg_last_4, dh6[-1].date, dh6[-1].source_event_id)
s6, h6, m6 = compute_headroom(state6)
print(f"  dining_amt=last4avg({avg_last_4:.2f}): headroom={h6:.2f}, min_date={m6}")
