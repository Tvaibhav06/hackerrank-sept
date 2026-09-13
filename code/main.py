import os
import sys
import json
import logging
from decimal import Decimal
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector
from forecast import ForecastEngine
from ai_resolver import AIResolver
from plan_generator import PlanGenerator
from validator import Validator
from output_writer import OutputWriter


import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None, help='Limit number of requests to process')
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    loader = DataLoader()
    engine = ExchangeRateEngine()
    builder = StateBuilder(engine)
    detector = RecurrenceDetector()
    
    output_path = os.path.join(os.path.dirname(__file__), '..', 'dataset', 'output.csv')
    writer = OutputWriter(output_path)
    
    # Load AI facts gracefully
    ai_cache_path = os.path.join(os.path.dirname(__file__), 'ai_cache.json')
    ai_facts = {}
    if os.path.exists(ai_cache_path):
        try:
            with open(ai_cache_path, 'r', encoding='utf-8') as f:
                ai_cache = json.load(f)
            for req_hash, resp in ai_cache.items():
                if isinstance(resp, dict) and 'action' in resp:
                    if resp.get('target_event_id') and resp['target_event_id'] != 'NONE':
                        ai_facts[resp['target_event_id']] = resp
        except Exception as e:
            logging.error(f"Error loading AI cache: {e}")
            
    resolver = AIResolver(loader.messages, loader.events_by_user)
    
    success_count = 0
    fail_count = 0
    
    requests_to_process = list(loader.requests.values())
    if args.limit:
        requests_to_process = requests_to_process[:args.limit]
        
    for req in requests_to_process:
        try:
            # Re-resolve for this specific request's context if needed, 
            # but AIResolver handles it all at once currently.
            # We'll just use the globally resolved facts.
            resolved = resolver.resolve(ai_facts)
            
            uid = req.user_id
            profile = loader.profiles.get(uid)
            if not profile:
                raise ValueError(f"Profile not found for {uid}")
                
            events = loader.events_by_user.get(uid, [])
            opts = loader.payment_options.get(req.request_id, [])
            
            # Phase 3: State Building
            state = builder.build(profile, events, resolved, req.request_date)
            
            # Phase 4/5: Recurrence and Forecast
            detector.apply_recurrences(state, uid)
            f_engine = ForecastEngine(state, debits_first=False)
            
            # Phase 6: Plan Generation
            generator = PlanGenerator(f_engine, req, profile, opts)
            plan_dict = generator.generate_best_plan()
            plan_dict['request_id'] = req.request_id
            
            # Phase 7: Validation
            # Create a fresh untouched state for the validator
            val_state = builder.build(profile, events, resolved, req.request_date)
            detector.apply_recurrences(val_state, uid)
            
            validator = Validator(plan_dict, req, profile, opts, val_state)
            val_result = validator.validate()
            
            if val_result['valid']:
                writer.add_result(plan_dict, is_valid=True)
                success_count += 1
            else:
                error_msg = "; ".join(val_result['errors'])
                logging.error(f"Validation failed for {req.request_id}: {error_msg}")
                writer.add_result(plan_dict, is_valid=False, error_msg=error_msg)
                fail_count += 1
                
        except Exception as e:
            error_msg = f"Exception: {str(e)}"
            logging.error(f"Pipeline crashed for {req.request_id}: {error_msg}")
            logging.error(traceback.format_exc())
            # Fallback dict
            fallback = {'request_id': req.request_id}
            writer.add_result(fallback, is_valid=False, error_msg=error_msg)
            fail_count += 1
            
    writer.write()
    logging.info(f"Pipeline complete. Wrote {success_count + fail_count} rows. Success: {success_count}, Failed: {fail_count}")

if __name__ == '__main__':
    main()
