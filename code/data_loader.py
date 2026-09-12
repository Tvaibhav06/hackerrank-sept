import csv
from dataclasses import dataclass
from decimal import Decimal
import datetime
from typing import List, Dict, Optional, Any
from config import (
    REQUESTS_FILE, PROFILES_FILE, EVENTS_FILE, RATES_FILE,
    PAYMENT_OPTIONS_FILE, MESSAGES_FILE, IMAGES_FILE
)

def parse_date(date_str: str) -> Optional[datetime.date]:
    if not date_str or date_str.strip() == '':
        return None
    try:
        # Assuming YYYY-MM-DD
        return datetime.date.fromisoformat(date_str.strip()[:10])
    except ValueError:
        return None

def parse_decimal(amt_str: str) -> Optional[Decimal]:
    if not amt_str or amt_str.strip() == '':
        return None
    return Decimal(amt_str.strip())

@dataclass
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: List[str]
    expense_categories_to_protect: List[str]
    expense_categories_user_is_willing_to_reduce: List[str]
    expense_categories_user_is_willing_to_stop: List[str]
    payment_methods_user_will_consider: List[str]
    max_installment_months: Optional[int]

@dataclass
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Optional[Decimal]
    currency: str
    event_date: datetime.date
    settlement_date: Optional[datetime.date]
    status: str
    linked_event_id: Optional[str]
    flexibility: str
    minimum_allowed_amount: Optional[Decimal]
    
    # Context injected during processing
    image_id: Optional[str] = None
    extracted_amount: Optional[Decimal] = None

@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: datetime.date
    payment_frequency_days: Optional[int]
    financing_fee: Decimal
    total_payable_amount: Decimal

@dataclass
class Message:
    message_id: str
    user_id: str
    request_id: Optional[str]
    related_event_id: Optional[str]
    sent_at: str
    source_type: str
    message_text: str

@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: datetime.date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: datetime.date
    allows_partial_payment: bool
    request_text: str

class DataLoader:
    def __init__(self):
        self.profiles: Dict[str, Profile] = {}
        self.events: Dict[str, Event] = {}
        self.events_by_user: Dict[str, List[Event]] = {}
        self.requests: Dict[str, Request] = {}
        self.messages: List[Message] = []
        self.payment_options: Dict[str, List[PaymentOption]] = {}
        self.images: Dict[str, str] = {}  # event_id -> image_id
        
        self.load_all()

    def _split_list(self, val: str) -> List[str]:
        if not val or val.strip() == '':
            return []
        return [x.strip() for x in val.split('|')]

    def load_profiles(self):
        with open(PROFILES_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                max_months = r['max_installment_months'].strip()
                self.profiles[r['user_id']] = Profile(
                    user_id=r['user_id'],
                    home_currency=r['home_currency'],
                    current_available_balance=parse_decimal(r['current_available_balance']),
                    minimum_balance_to_keep=parse_decimal(r['minimum_balance_to_keep']),
                    financial_priorities=self._split_list(r['financial_priorities']),
                    expense_categories_to_protect=self._split_list(r['expense_categories_to_protect']),
                    expense_categories_user_is_willing_to_reduce=self._split_list(r['expense_categories_user_is_willing_to_reduce']),
                    expense_categories_user_is_willing_to_stop=self._split_list(r['expense_categories_user_is_willing_to_stop']),
                    payment_methods_user_will_consider=self._split_list(r['payment_methods_user_will_consider']),
                    max_installment_months=int(max_months) if max_months else None
                )

    def load_events(self):
        with open(EVENTS_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                uid = r['user_id']
                eid = r['event_id']
                event = Event(
                    event_id=eid,
                    user_id=uid,
                    event_type=r['event_type'],
                    description=r['description'],
                    category=r['category'],
                    direction=r['direction'],
                    amount=parse_decimal(r['amount']),
                    currency=r['currency'],
                    event_date=parse_date(r['event_date']),
                    settlement_date=parse_date(r['settlement_date']),
                    status=r['status'],
                    linked_event_id=r['linked_event_id'] if r['linked_event_id'].strip() else None,
                    flexibility=r['flexibility'],
                    minimum_allowed_amount=parse_decimal(r['minimum_allowed_amount'])
                )
                self.events[eid] = event
                if uid not in self.events_by_user:
                    self.events_by_user[uid] = []
                self.events_by_user[uid].append(event)

    def load_images(self):
        with open(IMAGES_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                eid = r['related_event_id'].strip()
                if eid:
                    self.images[eid] = r['image_id']
                    # Inject into event
                    if eid in self.events:
                        self.events[eid].image_id = r['image_id']

    def load_requests(self):
        with open(REQUESTS_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                self.requests[r['request_id']] = Request(
                    request_id=r['request_id'],
                    user_id=r['user_id'],
                    request_date=parse_date(r['request_date']),
                    request_type=r['request_type'],
                    requested_amount=parse_decimal(r['requested_amount']),
                    desired_completion_date=parse_date(r['desired_completion_date']),
                    allows_partial_payment=(r['allows_partial_payment'].lower() == 'true'),
                    request_text=r['request_text']
                )

    def load_payment_options(self):
        with open(PAYMENT_OPTIONS_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                rid = r['request_id']
                freq = r['payment_frequency_days'].strip()
                opt = PaymentOption(
                    payment_option_id=r['payment_option_id'],
                    request_id=rid,
                    payment_method=r['payment_method'],
                    payment_amount=parse_decimal(r['payment_amount']),
                    number_of_payments=int(r['number_of_payments']),
                    first_payment_date=parse_date(r['first_payment_date']),
                    payment_frequency_days=int(freq) if freq else None,
                    financing_fee=parse_decimal(r['financing_fee']),
                    total_payable_amount=parse_decimal(r['total_payable_amount'])
                )
                if rid not in self.payment_options:
                    self.payment_options[rid] = []
                self.payment_options[rid].append(opt)

    def load_messages(self):
        with open(MESSAGES_FILE, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                self.messages.append(Message(
                    message_id=r['message_id'],
                    user_id=r['user_id'],
                    request_id=r['request_id'] if r['request_id'].strip() else None,
                    related_event_id=r['related_event_id'] if r['related_event_id'].strip() else None,
                    sent_at=r['sent_at'],
                    source_type=r['source_type'],
                    message_text=r['message_text']
                ))

    def load_all(self):
        self.load_profiles()
        self.load_events()
        self.load_images()
        self.load_requests()
        self.load_payment_options()
        self.load_messages()

if __name__ == "__main__":
    loader = DataLoader()
    print(f"Loaded {len(loader.profiles)} profiles")
    print(f"Loaded {len(loader.events)} events")
    print(f"Loaded {len(loader.requests)} requests")
    print(f"Loaded {len(loader.messages)} messages")
    print(f"Loaded {len(loader.images)} images")
    
    # Check blank amounts
    blanks = [e for e in loader.events.values() if e.amount is None]
    print(f"Events with blank amounts: {len(blanks)}")
    for e in blanks:
        print(f"  {e.event_id}: has image? {e.image_id is not None}")
