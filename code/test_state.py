import datetime
from decimal import Decimal
from data_loader import Event, Profile
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder, EventNode

def run_tests():
    engine = ExchangeRateEngine()
    builder = StateBuilder(engine)
    
    # Fake Profile
    profile = Profile(
        user_id='test_user',
        home_currency='IDR',
        current_available_balance=Decimal('10000000.00'), # 10M IDR
        minimum_balance_to_keep=Decimal('1000000.00'), # 1M IDR
        financial_priorities=[],
        expense_categories_to_protect=[],
        expense_categories_user_is_willing_to_reduce=[],
        expense_categories_user_is_willing_to_stop=[],
        payment_methods_user_will_consider=[],
        max_installment_months=None
    )
    
    # Case 1: Pending Debit is reserved (subtracted from 10M)
    ev_pending_debit = Event(
        event_id='e1', user_id='test_user', event_type='transfer', description='desc', category='util',
        direction='debit', amount=Decimal('500000.00'), currency='IDR',
        event_date=datetime.date(2025,1,1), settlement_date=datetime.date(2025,1,1),
        status='pending', linked_event_id=None, flexibility='fixed', minimum_allowed_amount=None
    )
    
    # Case 2: Pending Credit is ignored
    ev_pending_credit = Event(
        event_id='e2', user_id='test_user', event_type='transfer', description='desc', category='salary',
        direction='credit', amount=Decimal('2000000.00'), currency='IDR',
        event_date=datetime.date(2025,1,1), settlement_date=datetime.date(2025,1,1),
        status='pending', linked_event_id=None, flexibility='fixed', minimum_allowed_amount=None
    )
    
    # Case 3: Linked events (double counting avoidance). e3 is scheduled, e4 is settled amendment. We should only take e4.
    ev_link_orig = Event(
        event_id='e3', user_id='test_user', event_type='transfer', description='desc', category='sub',
        direction='debit', amount=Decimal('100000.00'), currency='IDR',
        event_date=datetime.date(2025,1,2), settlement_date=datetime.date(2025,1,2),
        status='scheduled', linked_event_id=None, flexibility='fixed', minimum_allowed_amount=None
    )
    ev_link_update = Event(
        event_id='e4', user_id='test_user', event_type='transfer', description='desc', category='sub',
        direction='debit', amount=Decimal('150000.00'), currency='IDR',
        event_date=datetime.date(2025,1,3), settlement_date=datetime.date(2025,1,3),
        status='settled', linked_event_id='e3', flexibility='fixed', minimum_allowed_amount=None
    )
    
    # Case 4: Foreign currency scheduled debit
    ev_foreign = Event(
        event_id='e5', user_id='test_user', event_type='transfer', description='desc', category='sub',
        direction='debit', amount=Decimal('100.00'), currency='USD',
        event_date=datetime.date(2024,2,15), settlement_date=datetime.date(2024,2,15),
        status='scheduled', linked_event_id=None, flexibility='fixed', minimum_allowed_amount=None
    )
    
    events = [ev_pending_debit, ev_pending_credit, ev_link_orig, ev_link_update, ev_foreign]
    
    req_date = datetime.date(2024, 2, 1)
    
    state = builder.build(profile, events, {}, req_date)
    
    print(f"Starting balance (Expected: 10M - 500k = 9,500,000): {state.start_balance}")
    
    ev_ids = [e.event.event_id for e in state.projected_events]
    print(f"Projected event IDs: {ev_ids}")
    if 'e4' not in ev_ids and 'e4' not in [e.event.event_id for e in state.projected_events]: # e4 is settled before req_date though, it shouldn't be in projected!
        print("e4 is settled, correctly NOT in projected future events.")
        
    foreign_evs = [e for e in state.projected_events if e.event.event_id == 'e5']
    if foreign_evs:
        # USD to IDR on 2024-02-15 is 15833.33 * 100 = 1,583,333
        print(f"Foreign event amount in IDR (Expected: ~1583333.00): {foreign_evs[0].amount}")

    print("\n--- ALL TESTS RAN ---")
    
if __name__ == '__main__':
    run_tests()
