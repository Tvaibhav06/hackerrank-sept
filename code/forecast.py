import datetime
from decimal import Decimal
from typing import List, Optional
from collections import defaultdict
from dataclasses import dataclass

from state_builder import FinancialState

@dataclass
class Payment:
    date: datetime.date
    amount: Decimal

class ForecastEngine:
    def __init__(self, state: FinancialState, debits_first: bool = True):
        self.state = state
        self.debits_first = debits_first
        self.end_date = self.state.request_date + datetime.timedelta(days=90)

    def is_plan_safe(self, payments: List[Payment]) -> bool:
        """
        Simulates the 90-day window applying the projected events and the given payments.
        Returns True if the balance never falls below min_balance.
        """
        sim = self.state.start_balance
        min_bal = self.state.min_balance
        
        if sim < min_bal:
            return False

        events_by_date = defaultdict(list)
        for e in self.state.projected_events:
            events_by_date[e.date].append(e)

        payments_by_date = defaultdict(Decimal)
        for p in payments:
            payments_by_date[p.date] += p.amount

        current_date = self.state.request_date
        
        while current_date <= self.end_date:
            daily_debits = Decimal(0)
            daily_credits = Decimal(0)
            
            for e in events_by_date.get(current_date, []):
                if e.direction == 'debit':
                    daily_debits += e.amount
                else:
                    daily_credits += e.amount

            # Include plan payments as debits
            daily_debits += payments_by_date.get(current_date, Decimal(0))

            if self.debits_first:
                sim -= daily_debits
                if sim < min_bal:
                    return False
                sim += daily_credits
            else:
                sim += daily_credits
                sim -= daily_debits
                if sim < min_bal:
                    return False
            
            current_date += datetime.timedelta(days=1)

        return True

    def amount_safe_to_pay(self, requested_amount: Decimal) -> Decimal:
        """
        Calculates the maximum safe payment amount on request_date without violating min_balance.
        """
        sim = self.state.start_balance
        min_bal = self.state.min_balance
        min_headroom = sim - min_bal
        
        events_by_date = defaultdict(list)
        for e in self.state.projected_events:
            events_by_date[e.date].append(e)
            
        current_date = self.state.request_date
        
        while current_date <= self.end_date:
            daily_debits = Decimal(0)
            daily_credits = Decimal(0)
            
            for e in events_by_date.get(current_date, []):
                if e.direction == 'debit':
                    daily_debits += e.amount
                else:
                    daily_credits += e.amount

            if self.debits_first:
                sim -= daily_debits
                min_headroom = min(min_headroom, sim - min_bal)
                sim += daily_credits
            else:
                sim += daily_credits
                sim -= daily_debits
                min_headroom = min(min_headroom, sim - min_bal)
            
            current_date += datetime.timedelta(days=1)
            
        safe = max(Decimal(0), min_headroom)
        return min(safe, requested_amount)

    def earliest_date_for_full_payment(self, requested_amount: Decimal) -> Optional[datetime.date]:
        """
        Finds the earliest date D in the 90-day window where the full requested amount can be paid.
        """
        sim = self.state.start_balance
        min_bal = self.state.min_balance
        
        events_by_date = defaultdict(list)
        for e in self.state.projected_events:
            events_by_date[e.date].append(e)
            
        current_date = self.state.request_date
        sim_history = []
        
        while current_date <= self.end_date:
            daily_debits = Decimal(0)
            daily_credits = Decimal(0)
            
            for e in events_by_date.get(current_date, []):
                if e.direction == 'debit':
                    daily_debits += e.amount
                else:
                    daily_credits += e.amount

            if self.debits_first:
                sim -= daily_debits
                sim_history.append({'date': current_date, 'worst_balance': sim})
                sim += daily_credits
            else:
                sim += daily_credits
                sim -= daily_debits
                sim_history.append({'date': current_date, 'worst_balance': sim})
            
            current_date += datetime.timedelta(days=1)
            
        # Natural violation check
        if any(h['worst_balance'] < min_bal for h in sim_history):
            return None
            
        n = len(sim_history)
        min_after = [Decimal(0)] * n
        min_after[-1] = sim_history[-1]['worst_balance']
        for i in range(n-2, -1, -1):
            min_after[i] = min(sim_history[i]['worst_balance'], min_after[i+1])
            
        for i in range(n):
            if min_after[i] - min_bal >= requested_amount:
                return sim_history[i]['date']
                
        return None
