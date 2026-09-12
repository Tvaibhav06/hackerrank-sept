import datetime
from decimal import Decimal
from data_loader import Event
from state_builder import FinancialState, EventNode
from forecast import ForecastEngine, Payment

def create_event_node(date: datetime.date, amount: str, direction: str, status: str = 'scheduled') -> EventNode:
    fake_e = Event(
        event_id='e1', user_id='u1', event_type='transfer', description='desc', category='test',
        direction=direction, amount=Decimal(amount), currency='IDR', event_date=date,
        settlement_date=date, status=status, linked_event_id=None, flexibility='fixed', minimum_allowed_amount=None
    )
    n = EventNode(fake_e)
    n.amount = Decimal(amount)
    return n

def test_forecast():
    print("--- Testing Forecast Simulator ---")
    
    req_date = datetime.date(2025, 1, 1)
    
    # 1. Payment on request date exactly affordable
    state1 = FinancialState(Decimal('2000'), 'IDR', Decimal('1000'), req_date)
    engine1 = ForecastEngine(state1)
    safe1 = engine1.amount_safe_to_pay(Decimal('1000'))
    print(f"Test 1 (Exact affordable on req_date): {safe1} (Expect 1000)")
    
    # 2. Zero safe amount (natural violation)
    state2 = FinancialState(Decimal('900'), 'IDR', Decimal('1000'), req_date)
    engine2 = ForecastEngine(state2)
    safe2 = engine2.amount_safe_to_pay(Decimal('500'))
    print(f"Test 2 (Zero safe, starts below min): {safe2} (Expect 0)")
    
    # 3. Income and Expense on the same date (Worst case intraday check)
    state3 = FinancialState(Decimal('2000'), 'IDR', Decimal('1000'), req_date)
    # Day 10: 1000 Debit, 2000 Credit
    d3 = req_date + datetime.timedelta(days=10)
    state3.projected_events.append(create_event_node(d3, '1500', 'debit')) # Drops to 500 (Violation!)
    state3.projected_events.append(create_event_node(d3, '2000', 'credit'))
    engine3 = ForecastEngine(state3)
    safe3 = engine3.amount_safe_to_pay(Decimal('500'))
    print(f"Test 3 (Intraday worst case ordering drops below min): {safe3} (Expect 0)")
    
    # 4. Salary arriving before payment makes it affordable
    state4 = FinancialState(Decimal('1500'), 'IDR', Decimal('1000'), req_date)
    # Safe on day 0 is 500. Requested is 1000.
    d4 = req_date + datetime.timedelta(days=15)
    state4.projected_events.append(create_event_node(d4, '1000', 'credit'))
    engine4 = ForecastEngine(state4)
    # earliest date for 1000 should be day 15
    earliest = engine4.earliest_date_for_full_payment(Decimal('1000'))
    print(f"Test 4 (Salary arriving before payment allows it): Earliest = {earliest} (Expect {d4})")
    
    # 5. Salary arriving after payment
    state5 = FinancialState(Decimal('1500'), 'IDR', Decimal('1000'), req_date)
    d5 = req_date + datetime.timedelta(days=5)
    state5.projected_events.append(create_event_node(d5, '1000', 'credit'))
    engine5 = ForecastEngine(state5)
    safe5 = engine5.amount_safe_to_pay(Decimal('1000'))
    print(f"Test 5 (Salary arrives after request_date, so safe on request_date is limited by initial headroom): {safe5} (Expect 500)")
    
    # 6. Multiple events on the same date
    state6 = FinancialState(Decimal('2000'), 'IDR', Decimal('1000'), req_date)
    d6 = req_date + datetime.timedelta(days=5)
    state6.projected_events.append(create_event_node(d6, '200', 'debit'))
    state6.projected_events.append(create_event_node(d6, '300', 'debit'))
    engine6 = ForecastEngine(state6)
    safe6 = engine6.amount_safe_to_pay(Decimal('1000'))
    # Headroom = 2000 - 1000 - 500 = 500
    print(f"Test 6 (Multiple same day debits): {safe6} (Expect 500)")
    
    # 7. 90-day boundary 
    state7 = FinancialState(Decimal('2000'), 'IDR', Decimal('1000'), req_date)
    d7 = req_date + datetime.timedelta(days=90)
    state7.projected_events.append(create_event_node(d7, '800', 'debit'))
    d7_out = req_date + datetime.timedelta(days=91)
    state7.projected_events.append(create_event_node(d7_out, '10000', 'debit')) # Ignored
    engine7 = ForecastEngine(state7)
    safe7 = engine7.amount_safe_to_pay(Decimal('1000'))
    print(f"Test 7 (90-day boundary debit included, day 91 ignored): {safe7} (Expect 200)")
    
    print("\n--- ALL TESTS COMPLETED ---")

if __name__ == '__main__':
    test_forecast()
