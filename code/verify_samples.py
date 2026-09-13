import csv
import json
import datetime
from decimal import Decimal

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from forecast import ForecastEngine

def run_verification():
    loader = DataLoader()
    engine = ExchangeRateEngine()
    builder = StateBuilder(engine)
    detector = RecurrenceDetector()

    # Load AI cache so we don't have to call Gemini during this test
    ai_cache = {}
    try:
        with open('ai_cache.json', 'r') as f:
            ai_cache = json.load(f)
    except FileNotFoundError:
        print("Warning: ai_cache.json not found. Assuming empty AI facts for test.")

    samples = []
    with open('dataset/sample_requests.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            samples.append(r)
            
    # Need to group AI facts by user to pass to state builder?
    # No, state_builder apply_ai_facts expects a dict of {event_id: fact}
    # ai_cache contains message interpretations.
    # Let's map message interpretations to target_event_id.
    ai_facts = {}
    for req_hash, resp in ai_cache.items():
        if isinstance(resp, dict) and 'action' in resp:
            # this is a message interpretation
            if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
                ai_facts[resp['target_event_id']] = resp

    differences_found = 0
    matches_debits = 0
    matches_credits = 0

    print(f"Testing {len(samples)} samples for intraday ordering effects...")
    for s in samples:
        uid = s['user_id']
        req_id = s['request_id']
        req_amt = Decimal(s['requested_amount'])
        req_date = datetime.date.fromisoformat(s['request_date'])
        
        expected_status = s['affordability_status']
        expected_action = s['recommended_payment_method']
        expected_action_date = s['earliest_date_for_full_payment']
        
        profile = loader.profiles[uid]
        events = loader.events_by_user.get(uid, [])
        
        state = builder.build(profile, events, ai_facts, req_date)
        detector.apply_recurrences(state, uid)
        
        engine_debits = ForecastEngine(state, debits_first=True)
        safe_debits = engine_debits.amount_safe_to_pay(req_amt)
        earliest_debits = engine_debits.earliest_date_for_full_payment(req_amt)
        
        engine_credits = ForecastEngine(state, debits_first=False)
        safe_credits = engine_credits.amount_safe_to_pay(req_amt)
        earliest_credits = engine_credits.earliest_date_for_full_payment(req_amt)
        
        if safe_debits != safe_credits or earliest_debits != earliest_credits:
            differences_found += 1
            print(f"\n--- Difference on {req_id} ({uid}) ---")
            print(f"Sample Expected: {expected_status}, {expected_action}, {expected_action_date}")
            print(f"Debits First : safe={safe_debits}, earliest={earliest_debits}")
            print(f"Credits First: safe={safe_credits}, earliest={earliest_credits}")
            
            # Check which one matches expected
            # If expected_action is DELAY, the action_date should match earliest date
            if expected_action == 'DELAY':
                exp_date_str = expected_action_date
                if earliest_debits and earliest_debits.isoformat() == exp_date_str:
                    matches_debits += 1
                if earliest_credits and earliest_credits.isoformat() == exp_date_str:
                    matches_credits += 1

    print(f"\nResults:")
    print(f"Differences found: {differences_found}")
    print(f"Matches Debits First (strict): {matches_debits}")
    print(f"Matches Credits First (lenient): {matches_credits}")
    
    if differences_found == 0:
        print("Conclusion: Same-day ordering does not affect any of the 25 sample outcomes.")
    else:
        print("Conclusion: Intraday ordering DOES affect outcomes. We will configure based on matches.")

if __name__ == '__main__':
    run_verification()
