import datetime
from decimal import Decimal
from exchange_rate import ExchangeRateEngine

def test_exchange_rate():
    engine = ExchangeRateEngine()
    
    # Direct
    d = datetime.date(2023, 10, 15)
    amt = Decimal("100")
    res1 = engine.convert(amt, "EUR", "ZAR", d)
    print(f"100 EUR -> ZAR on 2023-10-15: {res1} (Expect 2000)")
    
    # Inverse
    res2 = engine.convert(amt, "EUR", "USD", d)
    print(f"100 EUR -> USD on 2023-10-15: {res2} (Expect 100 / 0.92 = 108.695...)")
    
    # Cross (if applicable)
    try:
        res3 = engine.convert(amt, "EUR", "IDR", d)
        print(f"100 EUR -> IDR on 2023-10-15: {res3}")
    except ValueError as e:
        print(e)
        
    print("Exchange Rate Engine Tests Completed.")

if __name__ == "__main__":
    test_exchange_rate()
