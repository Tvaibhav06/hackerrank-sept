import csv
from typing import Dict, Any, List
from decimal import Decimal


class OutputWriter:
    COLUMNS = [
        'request_id',
        'amount_safe_to_pay',
        'affordability_status',
        'recommended_payment_method',
        'payment_plan',
        'earliest_date_for_full_payment',
        'spending_changes_needed',
        'decision_explanation'
    ]

    def __init__(self, output_path: str):
        self.output_path = output_path
        self.rows = []

    def add_result(self, plan: Dict[str, Any], is_valid: bool = True, error_msg: str = ""):
        """
        Adds a single request result to the internal buffer.
        Generates the explanation deterministically.
        If the plan is invalid or pipeline failed, it writes a fallback not_affordable row.
        """
        if not is_valid:
            # Fallback for failed validation or crashes
            row = {
                'request_id': plan.get('request_id', 'UNKNOWN'),
                'amount_safe_to_pay': self._format_decimal(plan.get('amount_safe_to_pay', Decimal('0'))),
                'affordability_status': 'not_affordable',
                'recommended_payment_method': 'not_recommended',
                'payment_plan': 'none',
                'earliest_date_for_full_payment': plan.get('earliest_date_for_full_payment', ''),
                'spending_changes_needed': 'none',
                'decision_explanation': f"Validation failed or error occurred: {error_msg}"
            }
        else:
            row = {
                'request_id': plan['request_id'],
                'amount_safe_to_pay': self._format_decimal(plan['amount_safe_to_pay']),
                'affordability_status': plan['affordability_status'],
                'recommended_payment_method': plan['recommended_payment_method'],
                'payment_plan': plan['payment_plan'],
                'earliest_date_for_full_payment': plan['earliest_date_for_full_payment'],
                'spending_changes_needed': plan['spending_changes_needed'],
                'decision_explanation': self._generate_explanation(plan)
            }
        self.rows.append(row)

    def write(self):
        """
        Writes all buffered rows to the output CSV.
        """
        with open(self.output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(row)

    def _format_decimal(self, value) -> str:
        if isinstance(value, Decimal):
            return f"{value:.2f}"
        if isinstance(value, str):
            try:
                return f"{Decimal(value):.2f}"
            except Exception:
                pass
        return str(value)

    def _generate_explanation(self, plan: Dict[str, Any]) -> str:
        """
        Deterministically generate an explanation based purely on the plan fields.
        """
        method = plan['recommended_payment_method']
        status = plan['affordability_status']
        safe_amt = self._format_decimal(plan['amount_safe_to_pay'])
        sc = plan['spending_changes_needed']
        
        sc_text = ""
        if sc != 'none':
            sc_text = f" by making spending changes ({sc})"
            
        if method == 'full_payment':
            if status == 'affordable_now':
                return f"You can safely pay the full amount today{sc_text} without dropping below your minimum required balance."
            else:
                return f"You can safely pay the full amount{sc_text}."
                
        elif method == 'partial_payment':
            return f"Your current safe capacity is {safe_amt}. You can pay this amount today and the remainder on {plan['earliest_date_for_full_payment']}{sc_text}."
            
        elif method == 'installments':
            return f"Paying in installments is the safest option. Your current safe capacity of {safe_amt} is insufficient for a full payment, but the installment plan fits within your forecast minimums{sc_text}."
            
        elif method == 'wait':
            return f"Your current safe capacity is {safe_amt}. It is recommended to wait until {plan['earliest_date_for_full_payment']} when you can safely pay the full amount{sc_text}."
            
        elif method == 'not_recommended':
            return f"Your current safe capacity is {safe_amt}. No safe payment plan is available within the requested timeline that keeps your balance above the minimum required."
            
        return "Deterministic explanation."
