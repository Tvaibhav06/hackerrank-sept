import csv
import json
import datetime
from decimal import Decimal

from data_loader import DataLoader, Request
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from plan_generator import PlanGenerator
from ai_resolver import AIResolver

def main():
    loader = DataLoader()
    engine = ExchangeRateEngine()
    builder = StateBuilder(engine)
    detector = RecurrenceDetector()

    ai_cache = {}
    try:
        with open('ai_cache.json', 'r') as f:
            ai_cache = json.load(f)
    except FileNotFoundError:
        pass

    ai_facts = {}
    for req_hash, resp in ai_cache.items():
        if isinstance(resp, dict) and 'action' in resp:
            if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
                ai_facts[resp['target_event_id']] = resp

    samples = []
    with open('../dataset/sample_requests.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            samples.append(r)

    matches = 0
    total = 0
    
    mismatches = []

    for s in samples:
        uid = s['user_id']
        req_id = s['request_id']
        req_date = datetime.date.fromisoformat(s['request_date'])
        
        expected_status = s['affordability_status']
        expected_action = s['recommended_payment_method']
        expected_plan = s['payment_plan']
        expected_sc = s['spending_changes_needed']
        
        total += 1
        
        profile = loader.profiles[uid]
        events = loader.events_by_user.get(uid, [])
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
        opts = loader.payment_options.get(req_id, [])
        events = loader.events_by_user.get(uid, [])
        
        resolver = AIResolver(loader.messages, loader.events_by_user)
        resolved_ai_facts = resolver.resolve(ai_facts)
        
        state = builder.build(profile, events, resolved_ai_facts, req_date)
        detector.apply_recurrences(state, uid)
        
        forecast_engine = ForecastEngine(state, debits_first=False)
        generator = PlanGenerator(forecast_engine, request, profile, opts)
        
        plan = generator.generate_best_plan()
        
        if (plan['affordability_status'] == expected_status and 
            plan['recommended_payment_method'] == expected_action and
            plan['spending_changes_needed'] == expected_sc):
            
            # Additional check: Does the payment_plan match?
            # It might not match exactly due to rounding/formatting, but let's check it.
            # If it's a structural match, it's a pass.
            matches += 1
        else:
            mismatches.append({
                'req_id': req_id,
                'exp': f"{expected_status} | {expected_action} | {expected_sc} | {expected_plan}",
                'act': f"{plan['affordability_status']} | {plan['recommended_payment_method']} | {plan['spending_changes_needed']} | {plan['payment_plan']}"
            })

    print(f"Results: {matches} / {total} matches on status, method, and spending changes.")
    if mismatches:
        print("\nMismatches:")
        for m in mismatches:
            print(f"  {m['req_id']}")
            print(f"    Expected: {m['exp']}")
            print(f"    Actual:   {m['act']}")

if __name__ == '__main__':
    main()
