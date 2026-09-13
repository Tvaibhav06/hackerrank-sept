import datetime
import itertools
import copy
from decimal import Decimal
from typing import List, Dict, Any, Optional, Tuple

from data_loader import Request, Profile, PaymentOption
from forecast import ForecastEngine, Payment
from state_builder import FinancialState

class CandidatePlan:
    def __init__(self, 
                 payment_method: str, 
                 payment_plan: str, 
                 spending_changes_needed: str,
                 total_amount_paid: Decimal,
                 first_payment_date: datetime.date,
                 last_payment_date: datetime.date,
                 num_payments: int,
                 payment_option_id: str,
                 num_spending_changes: int,
                 affordability_status: str):
        self.payment_method = payment_method
        self.payment_plan = payment_plan
        self.spending_changes_needed = spending_changes_needed
        self.total_amount_paid = total_amount_paid
        self.first_payment_date = first_payment_date
        self.last_payment_date = last_payment_date
        self.num_payments = num_payments
        self.payment_option_id = payment_option_id
        self.num_spending_changes = num_spending_changes
        self.affordability_status = affordability_status

class PlanGenerator:
    def __init__(self, engine: ForecastEngine, request: Request, profile: Profile, payment_options: List[PaymentOption]):
        self.engine = engine
        self.request = request
        self.profile = profile
        self.payment_options = payment_options
        
        # Calculate baseline safe amounts (on UNMODIFIED state)
        self.baseline_amount_safe = self.engine.amount_safe_to_pay(self.request.requested_amount)
        self.baseline_earliest_date = self.engine.earliest_date_for_full_payment(self.request.requested_amount)
        self.baseline_earliest_date_str = self.baseline_earliest_date.isoformat() if self.baseline_earliest_date else ''

    def generate_best_plan(self) -> Dict[str, Any]:
        # Generate candidates without spending changes
        candidates = self._generate_all_candidates(self.engine, spending_changes=[])
        
        # Filter safe and completes on time
        safe_candidates = [c for c in candidates if self._is_candidate_safe(c, self.engine)]
        
        # If no safe no-change candidates exist, explore spending changes
        if not safe_candidates:
            sc_candidates = self._explore_spending_changes()
            safe_candidates.extend(sc_candidates)
            
        if not safe_candidates:
            # Deny
            return {
                'amount_safe_to_pay': self.baseline_amount_safe,
                'affordability_status': 'not_affordable',
                'recommended_payment_method': 'not_recommended',
                'payment_plan': 'none',
                'earliest_date_for_full_payment': self.baseline_earliest_date_str,
                'spending_changes_needed': 'none'
            }
            
        # Rank the safe candidates
        best_candidate = self._rank_candidates(safe_candidates)
        
        return {
            'amount_safe_to_pay': self.baseline_amount_safe,
            'affordability_status': best_candidate.affordability_status,
            'recommended_payment_method': best_candidate.payment_method,
            'payment_plan': best_candidate.payment_plan,
            'earliest_date_for_full_payment': self.baseline_earliest_date_str,
            'spending_changes_needed': best_candidate.spending_changes_needed
        }

    def _generate_all_candidates(self, engine: ForecastEngine, spending_changes: List[str]) -> List[CandidatePlan]:
        candidates = []
        req_amt = self.request.requested_amount
        safe_now = engine.amount_safe_to_pay(req_amt)
        earliest_date = engine.earliest_date_for_full_payment(req_amt)
        
        sc_str = "none" if not spending_changes else "|".join(spending_changes)
        num_sc = len(spending_changes)
        afford_status = 'affordable_with_plan' if num_sc > 0 else 'affordable_now'
        
        # 1. Full Payment
        if 'full_payment' in self.profile.payment_methods_user_will_consider:
            if safe_now >= req_amt:
                candidates.append(CandidatePlan(
                    payment_method='full_payment',
                    payment_plan=f"{self.request.request_date}:{req_amt}",
                    spending_changes_needed=sc_str,
                    total_amount_paid=req_amt,
                    first_payment_date=self.request.request_date,
                    last_payment_date=self.request.request_date,
                    num_payments=1,
                    payment_option_id="",
                    num_spending_changes=num_sc,
                    affordability_status=afford_status
                ))
                
        # 2. Partial Payment
        if self.request.allows_partial_payment and 'partial_payment' in self.profile.payment_methods_user_will_consider:
            if safe_now >= req_amt * Decimal('0.20') and 0 < safe_now < req_amt:
                if earliest_date:
                    remainder = req_amt - safe_now
                    candidates.append(CandidatePlan(
                        payment_method='partial_payment',
                        payment_plan=f"{self.request.request_date}:{safe_now}|{earliest_date}:{remainder}",
                        spending_changes_needed=sc_str,
                        total_amount_paid=req_amt,
                        first_payment_date=self.request.request_date,
                        last_payment_date=earliest_date,
                        num_payments=2,
                        payment_option_id="",
                        num_spending_changes=num_sc,
                        affordability_status='affordable_with_plan'
                    ))
                    
        # 3. Installments
        if 'installments' in self.profile.payment_methods_user_will_consider:
            inst_options = [o for o in self.payment_options if o.payment_method == 'installments']
            if self.profile.max_installment_months is not None:
                inst_options = [o for o in inst_options if o.number_of_payments <= self.profile.max_installment_months]
                
            for opt in inst_options:
                payments = []
                curr_d = opt.first_payment_date
                for i in range(opt.number_of_payments):
                    payments.append(Payment(curr_d, opt.payment_amount))
                    if opt.payment_frequency_days:
                        curr_d += datetime.timedelta(days=opt.payment_frequency_days)
                        
                plan_str = "|".join(f"{p.date}:{p.amount}" for p in payments)
                candidates.append(CandidatePlan(
                    payment_method='installments',
                    payment_plan=plan_str,
                    spending_changes_needed=sc_str,
                    total_amount_paid=opt.total_payable_amount,
                    first_payment_date=payments[0].date,
                    last_payment_date=payments[-1].date,
                    num_payments=opt.number_of_payments,
                    payment_option_id=opt.payment_option_id,
                    num_spending_changes=num_sc,
                    affordability_status='affordable_with_plan'
                ))
                
        # 4. Wait
        if earliest_date and 'full_payment' in self.profile.payment_methods_user_will_consider:
            candidates.append(CandidatePlan(
                payment_method='wait',
                payment_plan=f"{earliest_date}:{req_amt}",
                spending_changes_needed=sc_str,
                total_amount_paid=req_amt,
                first_payment_date=earliest_date,
                last_payment_date=earliest_date,
                num_payments=1,
                payment_option_id="",
                num_spending_changes=num_sc,
                affordability_status='affordable_later' if num_sc == 0 else 'affordable_with_plan'
            ))
            
        return candidates

    def _is_candidate_safe(self, candidate: CandidatePlan, engine: ForecastEngine) -> bool:
        if candidate.last_payment_date > self.request.desired_completion_date:
            return False
            
        payments = []
        for p in candidate.payment_plan.split('|'):
            d, a = p.split(':')
            payments.append(Payment(datetime.date.fromisoformat(d), Decimal(a)))
            
        return engine.is_plan_safe(payments)

    def _explore_spending_changes(self) -> List[CandidatePlan]:
        allowed_cats = set(self.profile.expense_categories_user_is_willing_to_reduce + self.profile.expense_categories_user_is_willing_to_stop)
        if not allowed_cats:
            return []
            
        # Extract distinct flexible recurrences from the initial state
        recurrences = {}
        for ev in self.engine.state.projected_events:
            if ev.flexibility in ('stoppable', 'reducible', 'reducible_or_stoppable') and ev.category in allowed_cats:
                sid = getattr(ev, 'source_event_id', None)
                if sid and sid not in recurrences:
                    recurrences[sid] = ev

        changes = []
        for sid, ev in recurrences.items():
            if ev.flexibility in ('stoppable', 'reducible_or_stoppable'):
                changes.append(('stop', sid))
            if ev.flexibility in ('reducible', 'reducible_or_stoppable'):
                if getattr(ev.event, 'minimum_allowed_amount', None):
                    changes.append(('reduce_to', sid, ev.event.minimum_allowed_amount))
                    
        safe_candidates = []
        for r in range(1, min(4, len(changes) + 1)):
            for combo in itertools.combinations(changes, r):
                # Ensure no conflict (same sid in stop and reduce_to)
                sids = [c[1] for c in combo]
                if len(sids) != len(set(sids)):
                    continue
                    
                mod_engine = self._clone_engine_with_changes(combo)
                
                sc_str_list = []
                for c in combo:
                    if c[0] == 'stop':
                        sc_str_list.append(f"stop:{c[1]}")
                    else:
                        sc_str_list.append(f"reduce_to:{c[1]}:{c[2]}")
                        
                cands = self._generate_all_candidates(mod_engine, sc_str_list)
                for cand in cands:
                    if self._is_candidate_safe(cand, mod_engine):
                        safe_candidates.append(cand)
                        
        return safe_candidates

    def _clone_engine_with_changes(self, combo: List[Tuple]) -> ForecastEngine:
        new_state = copy.deepcopy(self.engine.state)
        
        to_remove = []
        reductions = {c[1]: Decimal(str(c[2])) for c in combo if c[0] == 'reduce_to'}
        stops = {c[1] for c in combo if c[0] == 'stop'}
        
        for ev in new_state.projected_events:
            sid = getattr(ev, 'source_event_id', None)
            if sid in stops:
                to_remove.append(ev)
            elif sid in reductions:
                ev.amount = reductions[sid]
                
        for ev in to_remove:
            new_state.projected_events.remove(ev)
            
        return ForecastEngine(new_state)

    def _rank_candidates(self, candidates: List[CandidatePlan]) -> CandidatePlan:
        def rank_key(c: CandidatePlan):
            # Fallback tiebreaker string for plans without a payment option ID
            # 'zzz_no_option' ensures they sort AFTER real options (like 'opt_1').
            opt_id = c.payment_option_id if c.payment_option_id else 'zzz_no_option'
            
            return (
                0,                          # 1. Complete by deadline (all passed filter)
                c.num_spending_changes,     # 2. Require no spending changes
                c.total_amount_paid,        # 3. Minimize total amount paid
                c.first_payment_date,       # 4. Start payment earlier
                c.num_payments,             # 5. Use fewer payments
                opt_id                      # 6. Lowest payment_option_id
            )
            
        candidates.sort(key=rank_key)
        return candidates[0]
