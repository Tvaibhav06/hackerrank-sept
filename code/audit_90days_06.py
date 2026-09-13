"""
Print daily balances for user_06 for 90 days, with and without a 620.40 initial payment.
This will prove whether the minimum balance happens on Jan 13 or later.
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

state = builder.build(profile, events, resolved, req_date)
detector.apply_recurrences(state, uid)

events_by_date = defaultdict(list)
for e in state.projected_events:
    events_by_date[e.date].append(e)

# Simulate with NO initial payment
print("=== SIMULATION WITHOUT PAYMENT ===")
sim1 = state.start_balance
min1 = sim1
min_date1 = req_date
curr = req_date
while curr <= req_date + datetime.timedelta(days=90):
    dc = Decimal(0)
    dd = Decimal(0)
    for e in events_by_date.get(curr, []):
        if e.direction == 'credit': dc += e.amount
        else: dd += e.amount
    sim1 += dc - dd
    
    if dd > 0 or dc > 0 or sim1 < min1:
        if sim1 < min1:
            min1 = sim1
            min_date1 = curr
        print(f"{curr}: +{dc:7.2f} -{dd:7.2f} = {sim1:8.2f} (min={min1:8.2f} on {min_date1})")
        
    curr += datetime.timedelta(days=1)

print(f"\nFinal min1: {min1} on {min_date1}. Headroom: {min1 - state.min_balance}")

print("\n=== SIMULATION WITH 685.88 PAYMENT ON JAN 3 ===")
# Simulate with 685.88 payment on Jan 3
sim2 = state.start_balance - Decimal('685.88')
min2 = sim2
min_date2 = req_date
curr = req_date
while curr <= req_date + datetime.timedelta(days=90):
    dc = Decimal(0)
    dd = Decimal(0)
    for e in events_by_date.get(curr, []):
        if e.direction == 'credit': dc += e.amount
        else: dd += e.amount
    sim2 += dc - dd
    
    if dd > 0 or dc > 0 or sim2 < min2:
        if sim2 < min2:
            min2 = sim2
            min_date2 = curr
        print(f"{curr}: +{dc:7.2f} -{dd:7.2f} = {sim2:8.2f} (min={min2:8.2f} on {min_date2})")
        
    curr += datetime.timedelta(days=1)

print(f"\nFinal min2: {min2} on {min_date2}. Headroom: {min2 - state.min_balance}")
