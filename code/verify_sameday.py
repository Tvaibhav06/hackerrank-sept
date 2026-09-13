import csv
from datetime import datetime

def verify_sameday():
    samples = []
    with open('dataset/sample_requests.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            samples.append(r)
            
    # We want to see if any plan in the samples makes a payment on a date 
    # where there is also a credit event (income) for that user, 
    # and whether applying the debit first would have blocked it.
    
    events_by_user = {}
    with open('dataset/financial_events.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            uid = r['user_id']
            if uid not in events_by_user:
                events_by_user[uid] = []
            events_by_user[uid].append(r)
            
    print("Checking samples for same-day income/payment ordering...")
    found = 0
    for s in samples:
        if s['status'] != 'APPROVED':
            continue
            
        uid = s['user_id']
        req_date = s['request_date']
        
        # Determine the payment date(s) for the sample
        payment_dates = []
        if s['action'] in ('FULL', 'PARTIAL'):
            payment_dates.append(req_date)
        elif s['action'] == 'DELAY':
            payment_dates.append(s['action_date'])
        elif s['action'] == 'INSTALLMENT':
            # installments are usually on 1st of month, etc. We don't have the exact dates in action_date sometimes 
            # (action_date might be first payment date). Let's just check action_date.
            payment_dates.append(s['action_date'])
            
        for pd in payment_dates:
            if not pd:
                continue
            # Look for income on pd
            user_evs = events_by_user.get(uid, [])
            for e in user_evs:
                # Need to consider projected events too, but let's just look for historical exact matches 
                # or assume we're looking for ANY credit that falls on pd (even projected).
                # To be precise, we need to know if the user receives income on `pd` typically.
                pass
                
    # Actually, a simpler way is to just read the problem statement or see if any sample explicitly relies on intraday float.
    # The problem statement says: "Ensure the user's balance never drops below minimum_balance_to_keep".
    # Usually, banking systems process credits before debits on the same day (or simultaneously at EOD).
    # If the user says "If unsupported, make the ordering configurable/conservative rather than claiming it is mandated".
    print("Done checking.")

if __name__ == '__main__':
    verify_sameday()
