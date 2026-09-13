import re
from collections import defaultdict

class AIResolver:
    def __init__(self, messages, events_by_user):
        """
        messages: list of Dict (from messages.csv)
        events_by_user: Dict[user_id, List[Event]]
        """
        self.messages = {m.message_id: m for m in messages}
        self.events_by_user = events_by_user
        
        # 1. Build mapping of textual references to message_id
        self.ref_to_msg = {}
        for m in messages:
            matches = re.findall(r'[A-Z]{3}-\d{4}', m.message_text)
            for match in matches:
                self.ref_to_msg[match] = m.message_id
                
        # 2. Build statistical mapping of source_type to categories (from explicit links)
        self.source_cat_freq = defaultdict(lambda: defaultdict(int))
        for m in messages:
            if m.related_event_id:
                uid = m.user_id
                for e in self.events_by_user.get(uid, []):
                    if e.event_id == m.related_event_id:
                        self.source_cat_freq[m.source_type][e.category] += 1
                        break

        # 3. Build global word-to-category mapping from all event descriptions
        self.word_cat_freq = defaultdict(lambda: defaultdict(int))
        for uid, evs in self.events_by_user.items():
            for e in evs:
                words = set(re.findall(r'\w+', e.description.lower()))
                for w in words:
                    if len(w) > 3: # Ignore small words
                        self.word_cat_freq[w][e.category] += 1

    def resolve(self, ai_facts):
        resolved_facts = {}
        
        for target_id, fact in ai_facts.items():
            fact = fact.copy()
            fact['original_target_id'] = target_id
            
            # Explicit identifier match
            exact_match = False
            for uid, evs in self.events_by_user.items():
                for e in evs:
                    if e.event_id == target_id:
                        resolved_facts[target_id] = fact
                        exact_match = True
                        break
                if exact_match:
                    break
            if exact_match:
                continue
                
            # Resolve via message linkage
            msg_id = self.ref_to_msg.get(target_id)
            if not msg_id:
                resolved_facts[target_id] = fact
                continue
                
            msg = self.messages[msg_id]
            uid = msg.user_id
            msg_text = msg.message_text.lower()
            msg_words = set(re.findall(r'\w+', msg_text))
            
            candidates = self.events_by_user.get(uid, [])
            if not candidates:
                resolved_facts[target_id] = fact
                continue
                
            best_score = -1
            best_candidates = []
            
            for e in candidates:
                score = 0
                desc_words = set(re.findall(r'\w+', e.description.lower()))
                cat_words = set(re.findall(r'\w+', e.category.lower()))
                
                # Direct word overlap with this specific event
                overlap = len(msg_words.intersection(desc_words)) + len(msg_words.intersection(cat_words))
                score += overlap * 20
                
                # Global word evidence (e.g. "payroll" in message -> implies "salary" category)
                global_evidence = 0
                for w in msg_words:
                    if len(w) > 3 and w in self.word_cat_freq:
                        # If this word appears in this category globally, add evidence
                        global_evidence += self.word_cat_freq[w].get(e.category, 0)
                # Cap global evidence so it doesn't overwhelm exact matches
                score += min(global_evidence, 30)
                
                # Dates
                msg_dates = re.findall(r'\d{4}-\d{2}-\d{2}', msg_text)
                if str(e.event_date) in msg_dates:
                    score += 50
                
                # Amounts
                msg_amounts = re.findall(r'\d+\.?\d*', msg_text)
                if e.amount is not None:
                    if str(e.amount) in msg_amounts or str(int(e.amount)) in msg_amounts:
                        score += 50
                    
                # Source type explicitly linked
                if e.category in self.source_cat_freq.get(msg.source_type, {}):
                    score += 10
                    
                # Tiebreaker: Recency
                recency = e.event_date.toordinal() / 1000000.0
                final_score = score + recency
                
                if score > 0: # Must have at least some non-recency evidence
                    if final_score > best_score:
                        best_score = final_score
                        best_candidates = [e]
                    elif final_score == best_score:
                        best_candidates.append(e)
                    
            if len(best_candidates) == 1:
                resolved_id = best_candidates[0].event_id
                resolved_facts[resolved_id] = fact
            else:
                # Unresolved
                resolved_facts[target_id] = fact
                
        return resolved_facts
