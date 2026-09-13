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
        
        # Group historical settled events and scheduled events by (category, description)
        cat_groups = defaultdict(list)
        # Combine historical (settled) and projected (scheduled/pending)
        all_events = state.historical_events + state.projected_events
        for n in all_events:
            if n.status in ('settled', 'scheduled', 'pending'):
                desc_key = n.description
                if n.category == 'salary':
                    d_lower = desc_key.lower()
                    if 'prorated first' in d_lower or 'next confirmed' in d_lower or 'payroll credit' in d_lower:
                        desc_key = 'Standard Payroll'
                cat_groups[(n.category, desc_key)].append(n)
            
        end_date = state.request_date + datetime.timedelta(days=90)
            
        for (cat, desc), evs in cat_groups.items():
            if len(evs) < 2:
                continue
                
            # Check for "final" in description
            is_final = any('final' in e.description.lower() for e in evs)
            if is_final:
                continue
                
            evs = sorted(evs, key=lambda x: x.date)
            intervals = [(evs[i+1].date - evs[i].date).days for i in range(len(evs)-1)]
            avg_interval = sum(intervals) / len(intervals)
            
            # Check if expired
            last_date = evs[-1].date
            if (state.request_date - last_date).days > avg_interval + 15:
                continue
            
            # Support weekly (6-8), bi-weekly (13-15), and monthly (26-35)
            if (6 <= avg_interval <= 8) or (13 <= avg_interval <= 15) or (26 <= avg_interval <= 35):
                direction = evs[0].direction
                flex = evs[0].flexibility
                
                # Rule: Forecast essential variable spending conservatively -> max for debits.
                # Flexible variable spending -> median
                amounts = [e.amount for e in evs]
                latest = evs[-1]
                if getattr(latest, 'ai_action', None) in ('REDUCE', 'INCREASE') and latest.ai_amount is not None:
                    forecast_amt = latest.ai_amount
                elif len(amounts) >= 2 and amounts[-1] == amounts[-2]:
                    forecast_amt = amounts[-1]
                elif direction == 'debit' and flex == 'essential':
                    forecast_amt = max(amounts)
                else:
                    forecast_amt = sorted(amounts)[len(amounts)//2] # median
                    
                day = Counter(e.date.day for e in evs).most_common(1)[0][0]
                is_monthly = (26 <= avg_interval <= 35)
                
                curr = evs[-1].date
                pattern_added = False
                while True:
                    if is_monthly:
                        # Increment month
                        m = curr.month + 1
                        y = curr.year
                        if m > 12:
                            m = 1; y += 1
                        try:
                            curr = datetime.date(y, m, min(day, 28))
                        except ValueError:
                            break
                    else:
                        curr += datetime.timedelta(days=int(avg_interval))
                        
                    if curr > end_date:
                        break
                        
                    if curr >= state.request_date:
                        # Ensure we don't double count if a scheduled/amended event already exists
                        covered = any(e.category == cat and e.description == desc and abs((e.date - curr).days) <= (5 if is_monthly else 2) for e in state.projected_events)
                        if not covered:
                            fake_e = Event(
                                event_id=f"proj_{cat}_{curr.strftime('%Y%m%d')}",
                                user_id=user_id,
                                event_type="projected",
                                description=desc,
                                category=cat,
                                direction=direction,
                                amount=forecast_amt,
                                currency=state.currency,
                                event_date=curr,
                                settlement_date=curr,
                                status='scheduled',
                                linked_event_id=None,
                                flexibility=flex,
                                minimum_allowed_amount=None
                            )
                            n = EventNode(fake_e)
                            n.amount = forecast_amt
                            n.source_event_id = evs[-1].event.event_id
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
