import datetime
from decimal import Decimal
from data_loader import DataLoader
from exchange_rate import ExchangeRateEngine
from state_builder import StateBuilder
from recurrence import RecurrenceDetector

def test_recurrence():
    loader = DataLoader()
    engine = ExchangeRateEngine()
    builder = StateBuilder(engine)
    detector = RecurrenceDetector()

    uid = 'user_08'
    profile = loader.profiles[uid]
    events = loader.events_by_user[uid]
    req_date = datetime.date(2025, 2, 7)

    state = builder.build(profile, events, {}, req_date)
    stats = detector.apply_recurrences(state, uid)

    print("--- Recurrence Detection Statistics ---")
    print(f"Patterns detected: {stats['detected_patterns']}")
    print(f"Total projected amount (90-day): {stats['total_projected_amount']}")
    print("\nExamples:")
    for ex in stats['examples']:
        print(f" - {ex['direction'].upper()}: [{ex['category']}] {ex['description'][:30]} -> {ex['amount']} (Interval: {ex['interval']:.1f}d)")

    # Boundary tests
    # 1. Variable amount uses MAX for debit
    util = [ex for ex in stats['examples'] if ex['category'] == 'utilities']
    if util:
        print(f"\nVariable Debit Max Test: {util[0]['amount']}")

    # 2. Check if projected events contain correct dates
    proj = [e for e in state.projected_events if e.category == 'salary']
    print(f"\nSalary projected dates: {[e.date for e in proj]}")

if __name__ == '__main__':
    test_recurrence()
