import csv
from decimal import Decimal
from typing import Dict, Tuple, Optional
from config import RATES_FILE
import datetime

class ExchangeRateEngine:
    def __init__(self):
        # Maps (date_str, from_currency, to_currency) -> rate
        self.rates: Dict[Tuple[str, str, str], Decimal] = {}
        self.load_rates()

    def load_rates(self):
        with open(RATES_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                self.rates[(r['rate_date'], r['from_currency'], r['to_currency'])] = Decimal(r['rate'])

    def convert(self, amount: Decimal, from_currency: str, to_currency: str, date: datetime.date) -> Decimal:
        if from_currency == to_currency:
            return amount

        date_str = date.isoformat()

        # Direct conversion
        if (date_str, from_currency, to_currency) in self.rates:
            return amount * self.rates[(date_str, from_currency, to_currency)]

        # Inverse conversion
        if (date_str, to_currency, from_currency) in self.rates:
            return amount / self.rates[(date_str, to_currency, from_currency)]

        # Chain conversion (fallback if needed)
        # We assume USD is the common base since the dataset usually has USD->EUR, USD->IDR, etc.
        if from_currency != 'USD' and to_currency != 'USD':
            # Try converting from_currency -> USD -> to_currency
            try:
                usd_amount = self.convert(amount, from_currency, 'USD', date)
                return self.convert(usd_amount, 'USD', to_currency, date)
            except ValueError:
                pass

        raise ValueError(f"No exchange rate found for {from_currency}->{to_currency} on {date_str}")
