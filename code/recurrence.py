import datetime
from decimal import Decimal
from collections import defaultdict, Counter
from typing import Dict, Any

from state_builder import FinancialState, EventNode
from data_loader import Event

class RecurrenceDetector:
    def __init__(self):
        pass

    def apply_recurrences(self, state: FinancialState, user_id: str) -> Dict[str, Any]:
        """
        Analyzes historical settled events, detects recurring patterns,
        projects them into the next 90 days, and returns statistics.
        """
        stats = {
            'detected_patterns': 0,
            'total_projected_amount': Decimal(0),
            'examples': []
        }
        
        # Group historical settled events by (category, description)
        cat_groups = defaultdict(list)
        for n in state.historical_events:
            # We already filtered cancelled/failed/non_cash in StateBuilder, 
            # and only settled events are here.
            cat_groups[(n.category, n.description)].append(n)
            
        end_date = state.request_date + datetime.timedelta(days=90)
            
        for (cat, desc), evs in cat_groups.items():
            if len(evs) < 2:
                continue
                
            evs = sorted(evs, key=lambda x: x.date)
            intervals = [(evs[i+1].date - evs[i].date).days for i in range(len(evs)-1)]
            avg_interval = sum(intervals) / len(intervals)
            
            # Rule: Detect recurrence only when history supports it (20-35 days for monthly)
            if 20 <= avg_interval <= 35:
                direction = evs[0].direction
                flex = evs[0].flexibility
                
                # Rule: Forecast essential variable spending conservatively -> max for debits.
                amounts = [e.amount for e in evs]
                if direction == 'debit':
                    forecast_amt = max(amounts)
                else:
                    forecast_amt = sorted(amounts)[len(amounts)//2] # median for credit
                    
                day = Counter(e.date.day for e in evs).most_common(1)[0][0]
                
                pattern_added = False
                # Project next 3 months
                for m_offset in range(4):
                    m = state.request_date.month + m_offset
                    y = state.request_date.year + (m - 1) // 12
                    m = ((m - 1) % 12) + 1
                    try:
                        d = datetime.date(y, m, min(day, 28))
                    except ValueError:
                        continue
                        
                    if state.request_date < d <= end_date:
                        # Ensure we don't double count if a scheduled/amended event already exists
                        covered = any(e.category == cat and e.description == desc and abs((e.date - d).days) <= 5 for e in state.projected_events)
                        if not covered:
                            fake_e = Event(
                                event_id=f"proj_{cat}_{m_offset}",
                                user_id=user_id,
                                event_type="projected",
                                description=desc,
                                category=cat,
                                direction=direction,
                                amount=forecast_amt,
                                currency=state.currency,
                                event_date=d,
                                settlement_date=d,
                                status='scheduled',
                                linked_event_id=None,
                                flexibility=flex,
                                minimum_allowed_amount=None
                            )
                            n = EventNode(fake_e)
                            n.amount = forecast_amt
                            state.projected_events.append(n)
                            
                            stats['total_projected_amount'] += forecast_amt
                            pattern_added = True
                            
                if pattern_added:
                    stats['detected_patterns'] += 1
                    stats['examples'].append({
                        'category': cat,
                        'description': desc,
                        'interval': avg_interval,
                        'amount': forecast_amt,
                        'direction': direction
                    })
                    
        return stats
