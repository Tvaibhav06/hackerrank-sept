import sys
import os
sys.path.append(os.path.abspath('../code'))
import json
from ai_resolver import AIResolver
from data_loader import DataLoader

loader = DataLoader()
resolver = AIResolver(loader.messages, loader.events_by_user)
with open('../code/ai_cache.json', 'r') as f:
    ai_facts = json.load(f)
resolved = resolver.resolve(ai_facts)
for k, v in resolved.items():
    if v.get('original_target_id') in ['EMP-0002', 'EMP-0003']:
        print(f"{v['original_target_id']} resolved to {k}")
