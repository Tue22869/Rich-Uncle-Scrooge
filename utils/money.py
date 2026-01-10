"""Money formatting utilities."""
from decimal import Decimal
from typing import Dict


def format_amount(amount: Decimal, currency: str = "RUB") -> str:
    """Format amount with currency."""
    amount_str = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
    return f"{amount_str} {currency}"


def format_amount_simple(amount: Decimal) -> str:
    """Format amount without currency."""
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def group_by_currency(amounts: Dict[str, Decimal]) -> Dict[str, Decimal]:
    """Group amounts by currency."""
    result = {}
    for currency, amount in amounts.items():
        if currency in result:
            result[currency] += amount
        else:
            result[currency] = amount
    return result

