import datetime
from decimal import Decimal
from typing import List, Dict, Any, Optional
from collections import defaultdict, Counter

from data_loader import Event, Profile, Message
from exchange_rate import ExchangeRateEngine

class EventNode:
    def __init__(self, event: Event):
        self.event = event
        self.amount = event.amount or event.extracted_amount or Decimal(0)
        self.date = event.settlement_date or event.event_date
        self.status = event.status
        self.currency = event.currency
        self.direction = event.direction
        self.category = event.category
        self.flexibility = event.flexibility
        self.description = event.description
        self.source_event_id = event.event_id
        self.ai_action = None
        self.ai_amount = None

class FinancialState:
    def __init__(self, start_balance: Decimal, currency: str, min_balance: Decimal, date: datetime.date):
        self.start_balance = start_balance
        self.currency = currency
        self.min_balance = min_balance
        self.request_date = date
        self.projected_events: List[EventNode] = []
        self.historical_events: List[EventNode] = []

    def get_worst_balance_in_window(self, days: int = 90) -> Decimal:
        end_date = self.request_date + datetime.timedelta(days=days)
        sim = self.start_balance
        worst = sim
        
        # Sort by date
        sorted_evs = sorted(self.projected_events, key=lambda e: e.date)
        for e in sorted_evs:
            if e.date > end_date:
                continue
            if e.date < self.request_date:
                # Should already be resolved or it's a past pending debit
                if e.direction == 'debit' and e.status in ('pending', 'scheduled'):
                    sim -= e.amount
            else:
                if e.direction == 'credit':
                    sim += e.amount
                elif e.direction == 'debit':
                    sim -= e.amount
            
            worst = min(worst, sim)
            
        return worst

class StateBuilder:
    def __init__(self, engine: ExchangeRateEngine):
        self.engine = engine

    def _resolve_linked_events(self, events: List[Event]) -> Dict[str, Event]:
        """
        Groups linked events and selects the most authoritative one.
        """
        # Map event_id -> root_event_id
        parents = {}
        for e in events:
            if e.linked_event_id:
                parents[e.event_id] = e.linked_event_id
                
        def find_root(eid):
            while eid in parents and parents[eid] != eid:
                eid = parents[eid]
            return eid

        groups = defaultdict(list)
        for e in events:
            root = find_root(e.event_id)
            groups[root].append(e)

        resolved = {}
        for root, group in groups.items():
            # Rule: explicit cancellation/settlement/amendment > newer > settled > safer
            # Actually, we can just sort by status priority and date
            status_priority = {'cancelled': 0, 'settled': 1, 'failed': 0, 'pending': 2, 'scheduled': 3}
            # The exact rules might require more nuance, but if it's cancelled we take that.
            # If any is cancelled/failed, the whole chain is dead.
            if any(e.status in ('cancelled', 'failed') for e in group):
                continue
                
            # If any is settled, that's the final state. 
            settled = [e for e in group if e.status == 'settled']
            if settled:
                # Latest settled
                resolved[root] = max(settled, key=lambda x: x.event_date)
                continue
                
            # Else take the newest pending/scheduled
            latest = max(group, key=lambda x: x.event_date)
            resolved[root] = latest
            
        return resolved

    def apply_ai_facts(self, events: Dict[str, Event], ai_facts: Dict[str, Any]):
        """
        Applies LLM/VLM facts to the events.
        """
        for eid, fact in ai_facts.items():
            if eid in events:
                e = events[eid]
                action = fact.get('action')
                if action == 'CANCEL':
                    e.status = 'cancelled'
                elif action == 'DELAY':
                    if e.status != 'settled':
                        e.status = 'scheduled'
                        if fact.get('date') and fact['date'] != 'NONE':
                            d = datetime.date.fromisoformat(fact['date'])
                            e.settlement_date = d
                            e.event_date = d
                elif action == 'REDUCE' or action == 'INCREASE':
                    amt = fact.get('amount')
                    if amt and amt != 0.0:
                        e.amount = Decimal(str(amt))
                        e.ai_action = action
                        e.ai_amount = e.amount
                elif action == 'CONFIRM':
                    if e.status != 'settled':
                        e.status = 'scheduled'  # Solidifies date
                elif action == 'PENDING':
                    if e.status != 'settled':
                        e.status = 'pending'
                    
    def build(self, profile: Profile, events: List[Event], ai_facts: Dict[str, Any], req_date: datetime.date) -> FinancialState:
        state = FinancialState(profile.current_available_balance, profile.home_currency, profile.minimum_balance_to_keep, req_date)
        
        # 1. Resolve Links
        resolved_dict = self._resolve_linked_events(events)
        
        # 2. Apply AI Facts
        self.apply_ai_facts(resolved_dict, ai_facts)
        
        active_events = [e for e in resolved_dict.values() if e.status not in ('cancelled', 'failed') and e.direction != 'non_cash']
        
        # 3. Handle specific events (pending, scheduled)
        # Convert all amounts to home currency
        nodes = []
        for e in active_events:
            amt = e.amount or e.extracted_amount or Decimal(0)
            if e.currency != state.currency:
                # Convert
                d = e.settlement_date or e.event_date
                try:
                    amt = self.engine.convert(amt, e.currency, state.currency, d)
                except ValueError:
                    pass # Keep original if conversion fails, though shouldn't happen
            
            node = EventNode(e)
            node.amount = amt
            if hasattr(e, 'ai_action'):
                node.ai_action = e.ai_action
                node.ai_amount = getattr(e, 'ai_amount', None)
            nodes.append(node)
            
        # 4. Handle Pending Debits and existing scheduled
        for n in nodes:
            if n.status == 'pending' and n.direction == 'debit':
                # Reserve pending debits. Subtract from balance.
                state.start_balance -= n.amount
            elif n.status == 'scheduled':
                if n.date >= req_date:
                    state.projected_events.append(n)
            elif n.status == 'settled':
                state.historical_events.append(n)
                
        return state
