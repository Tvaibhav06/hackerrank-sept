import pytest
import datetime
from decimal import Decimal
from typing import List, Any

from data_loader import Profile, Request, PaymentOption, Event
from forecast import ForecastEngine
from state_builder import FinancialState, EventNode
from plan_generator import PlanGenerator

@pytest.fixture
def base_profile():
    return Profile(
        user_id='test_user',
        home_currency='USD',
        current_available_balance=Decimal('1000'),
        minimum_balance_to_keep=Decimal('100'),
        financial_priorities=[],
        expense_categories_to_protect=[],
        expense_categories_user_is_willing_to_reduce=['streaming'],
        expense_categories_user_is_willing_to_stop=['streaming'],
        payment_methods_user_will_consider=['full_payment', 'partial_payment', 'installments', 'wait'],
        max_installment_months=12
    )

@pytest.fixture
def base_request():
    return Request(
        request_id='req_01',
        user_id='test_user',
        request_date=datetime.date(2024, 1, 1),
        request_type='purchase',
        requested_amount=Decimal('500'),
        desired_completion_date=datetime.date(2024, 1, 31),
        allows_partial_payment=True,
        request_text='test'
    )

def _build_engine(bal: Decimal, min_bal: Decimal, req_date: datetime.date, projected: List[EventNode] = None) -> ForecastEngine:
    state = FinancialState(bal, 'USD', min_bal, req_date)
    if projected:
        state.projected_events = projected
    return ForecastEngine(state)

def test_amount_safe_to_pay_remains_baseline(base_profile, base_request):
    """
    Test that even if a spending change allows the plan to become affordable,
    the returned 'amount_safe_to_pay' is still the baseline amount (computed BEFORE changes).
    """
    # Safe to pay without changes = 300 (balance 600 - min_bal 100 - 200 debit)
    # With a spending reduction, safe to pay might be 500, but output must still be 300.
    engine = _build_engine(Decimal('600'), Decimal('100'), base_request.request_date)
    
    # Add a stoppable event that frees up 200
    e = Event('evt_1', 'u', 'sub', 'test', 'streaming', 'debit', Decimal('200'), 'USD', 
              datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), 'scheduled', None, 'stoppable', None)
    n = EventNode(e)
    n.source_event_id = 'hist_evt_1'
    engine.state.projected_events.append(n)
    
    # Before change, safe_now = 600 - 100 - 200 = 300.
    assert engine.amount_safe_to_pay(Decimal('500')) == Decimal('300')
    
    gen = PlanGenerator(engine, base_request, base_profile, [])
    result = gen.generate_best_plan()
    
    # Must use spending change to afford 500
    assert result['spending_changes_needed'] == 'stop:hist_evt_1'
    assert result['affordability_status'] == 'affordable_with_plan'
    
    # Critical: amount_safe_to_pay must remain baseline 300!
    assert result['amount_safe_to_pay'] == Decimal('300')

def test_source_event_id_semantics(base_profile, base_request):
    """
    Test that the spending_changes_needed references the historical source_event_id,
    and properly resolves stop and reduce_to operations.
    """
    engine = _build_engine(Decimal('700'), Decimal('100'), base_request.request_date)
    
    e = Event('proj_2024', 'u', 'sub', 'test', 'streaming', 'debit', Decimal('200'), 'USD', 
              datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), 'scheduled', None, 'reducible', Decimal('50'))
    n = EventNode(e)
    # The source ID from the last historical occurrence
    n.source_event_id = 'historical_123'
    engine.state.projected_events.append(n)
    
    gen = PlanGenerator(engine, base_request, base_profile, [])
    result = gen.generate_best_plan()
    
    assert result['spending_changes_needed'] == 'reduce_to:historical_123:50'

def test_payment_option_id_fallback_and_sorting(base_profile, base_request):
    """
    Test that ties between payment plans use payment_option_id deterministically,
    and that non-installment plans fallback correctly without inventing empty string behavior.
    """
    # Balance 600, min 100. Safe to pay 500 now.
    engine = _build_engine(Decimal('600'), Decimal('100'), base_request.request_date)
    
    # Two identical installment options with different IDs
    opts = [
        PaymentOption('opt_B', 'req_01', 'installments', Decimal('500'), 1, base_request.request_date, None, Decimal('0'), Decimal('500')),
        PaymentOption('opt_A', 'req_01', 'installments', Decimal('500'), 1, base_request.request_date, None, Decimal('0'), Decimal('500'))
    ]
    
    # Because both complete now, 0 spending changes, same total (500), same start date, 
    # same num_payments (1), opt_A should win over opt_B.
    # ALSO, full_payment has exactly these stats but NO option ID.
    # The ranker assigns 'zzz_no_option' to full_payment, so opt_A (and opt_B) should beat full_payment.
    
    gen = PlanGenerator(engine, base_request, base_profile, opts)
    result = gen.generate_best_plan()
    
    assert result['recommended_payment_method'] == 'installments'
    # Wait, the ranking criteria is:
    # 6. Lowest payment_option_id
    # Since opt_A < opt_B < zzz_no_option, opt_A wins!
    
    # We don't return the option ID in the final output, but we can verify the payment_plan is from the installment
    assert result['payment_plan'] == f"{base_request.request_date}:500"

def test_spending_change_conflict_avoidance(base_profile, base_request):
    """Ensure stop and reduce_to are not both applied to the same event."""
    engine = _build_engine(Decimal('500'), Decimal('0'), base_request.request_date)
    
    e = Event('proj_2024', 'u', 'sub', 'test', 'streaming', 'debit', Decimal('400'), 'USD', 
              datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), 'scheduled', None, 'reducible_or_stoppable', Decimal('50'))
    n = EventNode(e)
    n.source_event_id = 'conflict_event'
    engine.state.projected_events.append(n)
    
    # With a stop we afford it. With a reduce we afford it.
    # Stop changes = 1, Reduce changes = 1.
    # If the system tried a combo of both, it would be 2 changes, which is worse, but we want to ensure it doesn't crash or emit invalid strings.
    
    gen = PlanGenerator(engine, base_request, base_profile, [])
    result = gen.generate_best_plan()
    
    # Since reducing to 50 saves 350, we have 450, we need 500, so reduce is NOT enough!
    # Wait, start bal 100, reduce saves 350, so we have 450 total. We need 500. Not enough!
    # We MUST stop it to get 500 total.
    assert result['spending_changes_needed'] == 'stop:conflict_event'
