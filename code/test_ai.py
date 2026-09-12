from data_loader import DataLoader
from ai_interface import AIInterface
import sys

def test_ai():
    print("Loading data...")
    loader = DataLoader()
    ai = AIInterface()
    
    print("\n--- Testing VLM (Images) ---")
    blank_events = [e for e in loader.events.values() if e.amount is None]
    print(f"Found {len(blank_events)} events with blank amounts.")
    
    for i, e in enumerate(blank_events[:3]):
        if e.image_id:
            print(f"\nProcessing {e.event_id} with image {e.image_id}...")
            print(f"Desc: {e.description} | Currency: {e.currency}")
            amount = ai.extract_image_amount(e.image_id, e.description, e.currency)
            print(f"Result: {amount}")
        
    print("\n--- Testing LLM (Messages) ---")
    print(f"Total messages: {len(loader.messages)}")
    
    for i, m in enumerate(loader.messages[:5]):
        print(f"\nProcessing message {m.message_id}...")
        print(f"Source: {m.source_type} | Text: {m.message_text[:100]}...")
        fact = ai.interpret_message(m.message_id, m.message_text, m.source_type)
        print(f"Fact: {fact}")
        
    print("\n--- Generating Usage Report ---")
    from logger import tracker
    tracker.generate_report(1)

if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    test_ai()
