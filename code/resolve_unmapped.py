import csv
import json

with open('../dataset/messages.csv', 'r', encoding='utf-8') as f:
    messages = list(csv.DictReader(f))

with open('ai_cache.json', 'r', encoding='utf-8') as f:
    ai_cache = json.load(f)

# Collect all unmapped AI target IDs
for m in messages:
    if not m['related_event_id']:
        print(f"{m['message_id']} - {m['user_id']} ({m['source_type']}): {m['message_text']}")
