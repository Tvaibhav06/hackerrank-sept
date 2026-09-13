import unittest
import datetime
import os
from decimal import Decimal

from data_loader import Request, Profile, PaymentOption, Event
from state_builder import FinancialState, EventNode
from forecast import ForecastEngine
from plan_generator import PlanGenerator
from validator import Validator
from output_writer import OutputWriter

class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.request_date = datetime.date(2026, 1, 1)
        self.request = Request(
            request_id='req_pipe',
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
        
        self.state = FinancialState(
            start_balance=Decimal('2000.00'),
            currency='EUR',
            min_balance=Decimal('1000.00'),
            date=self.request_date
        )
        self.state.historical_events = []
        self.state.projected_events = []

    def test_pipeline_success(self):
        # 1. Forecast
        engine = ForecastEngine(self.state, debits_first=False)
        
        # 2. Plan
        generator = PlanGenerator(engine, self.request, self.profile, [])
        plan = generator.generate_best_plan()
        plan['request_id'] = self.request.request_id
        
        # 3. Validate
        validator = Validator(plan, self.request, self.profile, [], self.state)
        res = validator.validate()
        self.assertTrue(res['valid'], res['errors'])
        
        # 4. Output
        out_path = 'test_output.csv'
        writer = OutputWriter(out_path)
        writer.add_result(plan, is_valid=res['valid'])
        writer.write()
        
        # 5. Check output
        self.assertTrue(os.path.exists(out_path))
        with open(out_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('req_pipe', content)
        self.assertIn('500.00', content)
        self.assertIn('affordable_now', content)
        self.assertIn('full_payment', content)
        
        os.remove(out_path)

if __name__ == '__main__':
    unittest.main()
