import unittest
import datetime
from decimal import Decimal

from data_loader import Request, Profile, PaymentOption, Event
from state_builder import FinancialState, EventNode
from forecast import ForecastEngine
from validator import Validator

class TestValidator(unittest.TestCase):
    def setUp(self):
        self.request_date = datetime.date(2026, 1, 1)
        self.request = Request(
            request_id='req_1',
            user_id='u1',
            request_date=self.request_date,
            request_type='one_time',
            requested_amount=Decimal('500.00'),
            desired_completion_date=datetime.date(2026, 1, 15),
            allows_partial_payment=True,
            request_text='test'
        )
        
        self.profile = Profile(
            user_id='u1',
            home_currency='EUR',
            current_available_balance=Decimal('2000.00'),
            minimum_balance_to_keep=Decimal('1000.00'),
            financial_priorities=[],
            expense_categories_to_protect=[],
            expense_categories_user_is_willing_to_reduce=['dining'],
            expense_categories_user_is_willing_to_stop=['streaming'],
            payment_methods_user_will_consider=['full_payment', 'partial_payment', 'installments', 'wait'],
            max_installment_months=None
        )
        
        self.options = [
            PaymentOption(
                payment_option_id='opt_1',
                request_id='req_1',
                payment_method='installments',
                number_of_payments=2,
                payment_amount=Decimal('250.00'),
                payment_frequency_days=14,
                first_payment_date=self.request_date,
                total_payable_amount=Decimal('500.00'),
                financing_fee=Decimal('0.00')
            )
        ]
        
        self.state = FinancialState(
            start_balance=Decimal('2000.00'),
            currency='EUR',
            min_balance=Decimal('1000.00'),
            date=self.request_date
        )
        self.state.historical_events = []
        self.state.projected_events = []

    def _make_validator(self, plan_dict):
        # We assume baseline safe amount is 1000.00 (since balance 2000 - min 1000 = 1000)
        # And requested is 500, so safe is 500.
        return Validator(plan_dict, self.request, self.profile, self.options, self.state)

    def test_valid_full_payment(self):
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('500.00'),
            'affordability_status': 'affordable_now',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertTrue(res['valid'], res['errors'])

    def test_invalid_amount(self):
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('600.00'), # Invalid, > 500
            'affordability_status': 'affordable_now',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('outside' in e or 'does not match' for e in res['errors']))

    def test_invalid_status(self):
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('500.00'),
            'affordability_status': 'fake_status',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('Invalid affordability_status' in e for e in res['errors']))

    def test_invalid_payment_method(self):
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('500.00'),
            'affordability_status': 'affordable_now',
            'recommended_payment_method': 'fake_method',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('Invalid recommended_payment_method' in e for e in res['errors']))
        
    def test_unsafe_full_payment(self):
        # Add a big expense that makes 500 unsafe
        ev = Event('e1', 'u1', 'projected', 'test', 'rent', 'debit', Decimal('600'), 'EUR', self.request_date, self.request_date, 'scheduled', None, 'fixed', None)
        n = EventNode(ev)
        self.state.projected_events.append(n)
        
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('400.00'), # Baseline matches
            'affordability_status': 'affordable_with_plan',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00', # Trying to pay 500 anyway
            'earliest_date_for_full_payment': '',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('full_payment is not safe' in e for e in res['errors']))

    def test_valid_spending_change(self):
        # Starting balance 2000. Min balance 1000. Safe = 1000.
        # Add an expense of 600. Safe = 400. We want 500.
        ev = Event('e1', 'u1', 'projected', 'test', 'streaming', 'debit', Decimal('200'), 'EUR', self.request_date, self.request_date, 'scheduled', None, 'stoppable', None)
        n = EventNode(ev)
        n.source_event_id = 'src_1'
        self.state.projected_events.append(n)
        
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('500.00'), # Capped at 500
            'affordability_status': 'affordable_now',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'stop:src_1'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertTrue(res['valid'], res['errors'])

    def test_invalid_spending_change(self):
        ev = Event('e1', 'u1', 'projected', 'test', 'rent', 'debit', Decimal('600'), 'EUR', self.request_date, self.request_date, 'scheduled', None, 'fixed', None)
        n = EventNode(ev)
        n.source_event_id = 'src_1'
        self.state.projected_events.append(n)
        
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('400.00'), 
            'affordability_status': 'affordable_with_plan',
            'recommended_payment_method': 'full_payment',
            'payment_plan': '2026-01-01:500.00',
            'earliest_date_for_full_payment': '',
            'spending_changes_needed': 'stop:src_1' # Rent is fixed!
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('non-stoppable' in e or 'not eligible' in e for e in res['errors']))
        
    def test_installments_mismatch(self):
        plan = {
            'request_id': 'req_1',
            'amount_safe_to_pay': Decimal('500.00'),
            'affordability_status': 'affordable_with_plan',
            'recommended_payment_method': 'installments',
            'payment_plan': '2026-01-01:250.00|2026-01-14:260.00', # wrong amount
            'earliest_date_for_full_payment': '2026-01-01',
            'spending_changes_needed': 'none'
        }
        v = self._make_validator(plan)
        res = v.validate()
        self.assertFalse(res['valid'])
        self.assertTrue(any('does not exactly match any available payment option' in e for e in res['errors']))


if __name__ == '__main__':
    unittest.main()
