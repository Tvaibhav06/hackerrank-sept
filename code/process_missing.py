"""
Process missing AI extractions using the AIInterface (which includes the circuit breaker and global budget).
"""
import sys
import time
from data_loader import DataLoader
from ai_interface import AIInterface

def main():
    loader = DataLoader()
    ai = AIInterface()
    
    # We only care about sample users (01 to 25) for now
    sample_users = [f'user_{i:02d}' for i in range(1, 26)]
    
    # Collect what's missing
    missing_images = []
    missing_messages = []
    
    for uid in sample_users:
        # Images
        for e in loader.events_by_user.get(uid, []):
            if e.amount is None and e.image_id:
                if f"img_{e.image_id}" not in ai.cache:
                    missing_images.append(e)
                    
        # Messages
        for m in [msg for msg in loader.messages if msg.user_id == uid]:
            if f"msg_{m.message_id}" not in ai.cache:
                missing_messages.append(m)
                
    total_missing = len(missing_images) + len(missing_messages)
    print(f"Missing images: {len(missing_images)}")
    print(f"Missing messages: {len(missing_messages)}")
    print(f"Total to process: {total_missing}")
    
    if total_missing == 0:
        print("Nothing to do!")
        return

    # Use the unified preprocess_all method which respects all circuit breakers
    summary = ai.preprocess_all(missing_images, missing_messages)
    
    print("\n--- Summary ---")
    for k, v in summary.items():
        print(f"{k}: {v}")
        
    print(f"\nFinal cache size: {len(ai.cache)}")
    
    from logger import tracker
    print("\n--- Reliability Tracker ---")
    print(f"Total Attempts: {tracker.total_attempted_calls}")
    print(f"Successes: {tracker.successful_calls}")
    print(f"429 Quota Errors: {tracker.quota_errors}")
    print(f"Circuit Breaker Blocked: {tracker.breaker_blocked}")

if __name__ == "__main__":
    main()
