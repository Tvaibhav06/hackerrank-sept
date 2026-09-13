import csv
from collections import defaultdict

with open('../dataset/messages.csv', 'r', encoding='utf-8') as f:
    messages = list(csv.DictReader(f))

with open('../dataset/financial_events.csv', 'r', encoding='utf-8') as f:
    events = list(csv.DictReader(f))

event_dict = {e['event_id']: e for e in events}

source_to_cat = defaultdict(list)
for m in messages:
    if m['related_event_id'] and m['related_event_id'] in event_dict:
        cat = event_dict[m['related_event_id']]['category']
        source_to_cat[m['source_type']].append(cat)

for src, cats in source_to_cat.items():
    s = set(cats)
    print(src, s, len(cats))
