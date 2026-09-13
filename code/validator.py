import datetime
import copy
from decimal import Decimal
from typing import Dict, Any, List, Optional

from data_loader import Request, Profile, PaymentOption
from state_builder import FinancialState
from forecast import ForecastEngine, Payment


class Validator:
    REQUIRED_FIELDS = [
        'request_id',
        'amount_safe_to_pay',
        'affordability_status',
        'recommended_payment_method',
        'payment_plan',
        'earliest_date_for_full_payment',
        'spending_changes_needed'
    ]
    
    ALLOWED_STATUSES = {
        'affordable_now',
        'affordable_with_plan',
        'affordable_later',
        'not_affordable'
    }
    
    ALLOWED_METHODS = {
        'full_payment',
        'partial_payment',
        'installments',
        'wait',
        'not_recommended'
    }

    def __init__(self, plan_dict: Dict[str, Any], request: Request, profile: Profile, payment_options: List[PaymentOption], initial_state: FinancialState):
        self.plan_dict = plan_dict
        self.request = request
        self.profile = profile
        self.payment_options = payment_options
        self.initial_state = initial_state
        
        self.errors = []
        self.warnings = []

    def validate(self) -> Dict[str, Any]:
        """
        Validates the recommendation completely and returns a dictionary
        {"valid": bool, "errors": list, "warnings": list}.
        """
        if not self._check_basic_structure():
            return {"valid": False, "errors": self.errors, "warnings": self.warnings}
            
        self._validate_amount_safe_to_pay()
        self._validate_earliest_date()
        self._validate_affordability_status()
        self._validate_recommended_method()
        
        # Determine spending changes early because we need the modified state for forecast simulation
        modified_engine = self._validate_and_apply_spending_changes()
        
        # Method-specific validation
        method = self.plan_dict.get('recommended_payment_method')
        if method == 'full_payment':
            self._validate_full_payment(modified_engine)
        elif method == 'partial_payment':
            self._validate_partial_payment(modified_engine)
        elif method == 'installments':
            self._validate_installments(modified_engine)
        elif method == 'wait':
            self._validate_wait(modified_engine)
        elif method == 'not_recommended':
            self._validate_not_recommended()

        return {
            "valid": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings
        }

    def _check_basic_structure(self) -> bool:
        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in self.plan_dict:
                self.errors.append(f"Missing required field: {field}")
                
        # Check request ID match
        req_id = self.plan_dict.get('request_id')
        if req_id != self.request.request_id:
            self.errors.append(f"request_id mismatch: {req_id} != {self.request.request_id}")
            
        return len(self.errors) == 0

    def _validate_amount_safe_to_pay(self):
        amt = self.plan_dict.get('amount_safe_to_pay')
        if not isinstance(amt, Decimal):
            self.errors.append(f"amount_safe_to_pay must be a Decimal, got {type(amt)}")
            return
            
        if amt < 0 or amt > self.request.requested_amount:
            self.errors.append(f"amount_safe_to_pay {amt} is outside [0, {self.request.requested_amount}]")
            
        # Verify it matches the baseline untouched state
        engine = ForecastEngine(self.initial_state, debits_first=False)
        baseline_safe = engine.amount_safe_to_pay(self.request.requested_amount)
        if amt != baseline_safe:
            self.errors.append(f"amount_safe_to_pay {amt} does not match baseline deterministic amount {baseline_safe}")

    def _validate_earliest_date(self):
        earliest = self.plan_dict.get('earliest_date_for_full_payment')
        
        engine = ForecastEngine(self.initial_state, debits_first=False)
        baseline_earliest = engine.earliest_date_for_full_payment(self.request.requested_amount)
        
        expected_str = baseline_earliest.isoformat() if baseline_earliest else ''
        
        if earliest != expected_str:
            self.errors.append(f"earliest_date_for_full_payment {earliest} does not match baseline deterministic date {expected_str}")
            
    def _validate_affordability_status(self):
        status = self.plan_dict.get('affordability_status')
        if status not in self.ALLOWED_STATUSES:
            self.errors.append(f"Invalid affordability_status: {status}")

    def _validate_recommended_method(self):
        method = self.plan_dict.get('recommended_payment_method')
        if method not in self.ALLOWED_METHODS:
            self.errors.append(f"Invalid recommended_payment_method: {method}")
            return
            
        if method != 'not_recommended':
            required_method = 'full_payment' if method == 'wait' else method
            if required_method not in self.profile.payment_methods_user_will_consider:
                self.errors.append(f"recommended_payment_method {method} is not accepted by user (requires {required_method})")

    def _validate_and_apply_spending_changes(self) -> ForecastEngine:
        sc = self.plan_dict.get('spending_changes_needed')
        engine = ForecastEngine(self.initial_state, debits_first=False)
        
        if sc == 'none' or not sc:
            return engine
            
        changes = sc.split('|')
        if len(changes) > 3:
            self.errors.append(f"Too many spending changes: {len(changes)}")
            return engine
            
        allowed_cats = set(self.profile.expense_categories_user_is_willing_to_reduce + self.profile.expense_categories_user_is_willing_to_stop)
        
        new_state = copy.deepcopy(self.initial_state)
        to_remove = []
        
        for change in changes:
            parts = change.split(':')
            if parts[0] == 'stop':
                if len(parts) != 2:
                    self.errors.append(f"Malformed stop change: {change}")
                    continue
                target_id = parts[1]
                
                # Find event
                ev = self._find_projected_event_by_source(new_state, target_id)
                if not ev:
                    self.errors.append(f"Spending change target {target_id} not found or not eligible")
                    continue
                    
                if ev.flexibility not in ('stoppable', 'reducible_or_stoppable'):
                    self.errors.append(f"Cannot stop non-stoppable event {target_id}")
                    continue
                    
                if ev.category not in allowed_cats:
                    self.errors.append(f"Category {ev.category} not permitted for spending changes by user")
                    continue
                    
                to_remove.append(target_id)
                
            elif parts[0] == 'reduce_to':
                if len(parts) != 3:
                    self.errors.append(f"Malformed reduce_to change: {change}")
                    continue
                target_id = parts[1]
                try:
                    new_amt = Decimal(parts[2])
                except Exception:
                    self.errors.append(f"Invalid decimal amount in reduce_to: {parts[2]}")
                    continue
                    
                ev = self._find_projected_event_by_source(new_state, target_id)
                if not ev:
                    self.errors.append(f"Spending change target {target_id} not found or not eligible")
                    continue
                    
                if ev.flexibility not in ('reducible', 'reducible_or_stoppable'):
                    self.errors.append(f"Cannot reduce non-reducible event {target_id}")
                    continue
                    
                if ev.category not in allowed_cats:
                    self.errors.append(f"Category {ev.category} not permitted for spending changes by user")
                    continue
                    
                if ev.event.minimum_allowed_amount and new_amt < ev.event.minimum_allowed_amount:
                    self.errors.append(f"Reduce to {new_amt} violates minimum allowed amount {ev.event.minimum_allowed_amount}")
                    continue
                    
                # Apply reduction
                for p_ev in new_state.projected_events:
                    if getattr(p_ev, 'source_event_id', None) == target_id:
                        p_ev.amount = new_amt
            else:
                self.errors.append(f"Unknown spending change action: {parts[0]}")
                
        # Remove stopped events
        if to_remove:
            new_state.projected_events = [e for e in new_state.projected_events if getattr(e, 'source_event_id', None) not in to_remove]
            
        return ForecastEngine(new_state, debits_first=False)
        
    def _find_projected_event_by_source(self, state, target_id):
        for e in state.projected_events:
            if getattr(e, 'source_event_id', None) == target_id:
                return e
        return None

    def _parse_payment_plan(self) -> List[Payment]:
        plan_str = self.plan_dict.get('payment_plan')
        if not plan_str or plan_str == 'none':
            return []
            
        payments = []
        for part in plan_str.split('|'):
            if not part:
                continue
            pieces = part.split(':')
            if len(pieces) != 2:
                self.errors.append(f"Malformed payment plan element: {part}")
                continue
            try:
                date = datetime.date.fromisoformat(pieces[0])
                amt = Decimal(pieces[1])
                payments.append(Payment(date, amt))
            except Exception as e:
                self.errors.append(f"Error parsing payment plan part {part}: {e}")
                
        return payments

    def _validate_full_payment(self, engine: ForecastEngine):
        payments = self._parse_payment_plan()
        if len(payments) != 1:
            self.errors.append("full_payment must have exactly one payment")
            return
            
        if payments[0].amount != self.request.requested_amount:
            self.errors.append(f"full_payment amount {payments[0].amount} != requested {self.request.requested_amount}")
            
        if payments[0].date != self.request.request_date:
            self.errors.append(f"full_payment date {payments[0].date} != request_date {self.request.request_date}")
            
        if not engine.is_plan_safe(payments):
            self.errors.append("full_payment is not safe according to forecast engine")

    def _validate_partial_payment(self, engine: ForecastEngine):
        if not self.request.allows_partial_payment:
            self.errors.append("partial_payment recommended but not allowed by request")
            
        amt_safe = self.plan_dict.get('amount_safe_to_pay')
        if not (0 < amt_safe < self.request.requested_amount):
            self.errors.append("partial_payment requires 0 < amount_safe_to_pay < requested_amount")
            
        payments = self._parse_payment_plan()
        if len(payments) != 2:
            self.errors.append("partial_payment plan must have exactly two payments")
            return
            
        if payments[0].date != self.request.request_date:
            self.errors.append("partial_payment first payment must be on request_date")
            
        if payments[0].amount != amt_safe:
            self.errors.append(f"partial_payment first amount {payments[0].amount} != amount_safe_to_pay {amt_safe}")
            
        expected_second = self.request.requested_amount - amt_safe
        if payments[1].amount != expected_second:
            self.errors.append(f"partial_payment second amount {payments[1].amount} != {expected_second}")
            
        if payments[1].date <= payments[0].date:
            self.errors.append("partial_payment second date must be after first date")
            
        if payments[1].date > self.request.desired_completion_date:
            self.errors.append(f"partial_payment completes on {payments[1].date} after deadline {self.request.desired_completion_date}")
            
        expected_earliest = self.plan_dict.get('earliest_date_for_full_payment')
        if expected_earliest and payments[1].date.isoformat() != expected_earliest:
            self.errors.append(f"partial_payment second date does not match earliest_date_for_full_payment {expected_earliest}")

        if not engine.is_plan_safe(payments):
            self.errors.append("partial_payment is not safe according to forecast engine")

    def _validate_installments(self, engine: ForecastEngine):
        payments = self._parse_payment_plan()
        
        # Verify it exactly matches one of the provided payment options
        matched_option = None
        for opt in self.payment_options:
            if opt.payment_method == 'installments':
                # Build what the schedule should look like
                expected_payments = []
                curr_d = opt.first_payment_date
                for _ in range(opt.number_of_payments):
                    expected_payments.append(Payment(curr_d, opt.payment_amount))
                    if opt.payment_frequency_days:
                        curr_d += datetime.timedelta(days=opt.payment_frequency_days)
                        
                # Compare
                if len(payments) == len(expected_payments):
                    match = True
                    for p, exp in zip(payments, expected_payments):
                        if p.date != exp.date or p.amount != exp.amount:
                            match = False
                            break
                    if match:
                        matched_option = opt
                        break
                        
        if not matched_option:
            self.errors.append("installments plan does not exactly match any available payment option")
            return
            
        if payments[-1].date > self.request.desired_completion_date:
            self.errors.append(f"installments complete on {payments[-1].date} after deadline {self.request.desired_completion_date}")
            
        if not engine.is_plan_safe(payments):
            self.errors.append("installments plan is not safe according to forecast engine")

    def _validate_wait(self, engine: ForecastEngine):
        payments = self._parse_payment_plan()
        if len(payments) != 1:
            self.errors.append("wait must have exactly one payment")
            return
            
        if payments[0].amount != self.request.requested_amount:
            self.errors.append(f"wait payment amount {payments[0].amount} != requested {self.request.requested_amount}")
            
        if payments[0].date <= self.request.request_date:
            self.errors.append("wait payment date must be strictly after request_date")
            
        if payments[0].date > self.request.desired_completion_date:
            self.errors.append(f"wait completes on {payments[0].date} after deadline {self.request.desired_completion_date}")
            
        expected_earliest = self.plan_dict.get('earliest_date_for_full_payment')
        if expected_earliest and payments[0].date.isoformat() != expected_earliest:
            self.errors.append(f"wait date {payments[0].date} != earliest_date_for_full_payment {expected_earliest}")
            
        if not engine.is_plan_safe(payments):
            self.errors.append("wait plan is not safe according to forecast engine")

    def _validate_not_recommended(self):
        if self.plan_dict.get('payment_plan') != 'none':
            self.errors.append("not_recommended must have payment_plan='none'")
            
        if self.plan_dict.get('affordability_status') != 'not_affordable':
            self.errors.append("not_recommended must have affordability_status='not_affordable'")
