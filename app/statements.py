"""Relevés de compte : synthèse par période, catégorisation des dépenses, export CSV.

Fonctionnalité livrée SANS tests unitaires (scénario de démo « demo/warning-coverage ») :
la couverture globale passe sous l'objectif de 80 % sans descendre sous 70 %, ce qui
déclenche un avertissement non critique du Quality Gate.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.schemas import Transaction

CATEGORIES = {
    "grocery": ("market", "carrefour", "monoprix", "aziza", "epicerie"),
    "transport": ("uber", "taxi", "sncf", "metro", "bolt", "essence"),
    "restaurant": ("cafe", "restaurant", "pizza", "burger", "boulangerie"),
    "shopping": ("zara", "amazon", "boutique", "fnac", "decathlon"),
    "bills": ("steg", "sonede", "orange", "ooredoo", "edf", "loyer"),
}


@dataclass
class CategoryTotal:
    category: str
    count: int = 0
    total: Decimal = Decimal("0")


@dataclass
class Statement:
    account_id: str
    currency: str
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_credits: Decimal = Decimal("0")
    total_debits: Decimal = Decimal("0")
    largest_debit: Decimal = Decimal("0")
    transactions: list[Transaction] = field(default_factory=list)
    categories: dict[str, CategoryTotal] = field(default_factory=dict)

    @property
    def net_change(self) -> Decimal:
        return self.total_credits - self.total_debits

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)


def categorize(description: str) -> str:
    text = description.lower()
    for category, keywords in CATEGORIES.items():
        if any(keyword in text for keyword in keywords):
            return category
    if text.startswith("initial deposit"):
        return "deposit"
    return "other"


def in_period(moment: datetime, start: date, end: date) -> bool:
    day = moment.date()
    return start <= day <= end


def validate_period(start: date, end: date) -> None:
    if end < start:
        raise ValueError("period end must not be before period start")
    if (end - start).days > 366:
        raise ValueError("statement period cannot exceed one year")


def build_statement(
    account_id: str, currency: str, history: list[Transaction], start: date, end: date
) -> Statement:
    validate_period(start, end)
    ordered = sorted(history, key=lambda txn: txn.created_at)
    before = [txn for txn in ordered if txn.created_at.date() < start]
    opening = before[-1].balance_after if before else Decimal("0")
    selected = [txn for txn in ordered if in_period(txn.created_at, start, end)]
    closing = selected[-1].balance_after if selected else opening
    statement = Statement(
        account_id=account_id,
        currency=currency,
        period_start=start,
        period_end=end,
        opening_balance=opening,
        closing_balance=closing,
        transactions=selected,
    )
    for txn in selected:
        if txn.type == "credit":
            statement.total_credits += txn.amount
        else:
            statement.total_debits += txn.amount
            statement.largest_debit = max(statement.largest_debit, txn.amount)
        category = categorize(txn.description)
        bucket = statement.categories.setdefault(category, CategoryTotal(category))
        bucket.count += 1
        bucket.total += txn.amount
    return statement


def spending_breakdown(statement: Statement) -> list[dict[str, str]]:
    debits = statement.total_debits or Decimal("1")
    rows = []
    for bucket in sorted(statement.categories.values(), key=lambda b: b.total, reverse=True):
        if bucket.category == "deposit":
            continue
        share = (bucket.total / debits * 100).quantize(Decimal("0.1"))
        rows.append(
            {
                "category": bucket.category,
                "count": str(bucket.count),
                "total": f"{bucket.total:.2f}",
                "share_percent": f"{share}",
            }
        )
    return rows


def monthly_totals(history: list[Transaction]) -> dict[str, dict[str, Decimal]]:
    months: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"credit": Decimal("0"), "debit": Decimal("0")}
    )
    for txn in history:
        key = txn.created_at.strftime("%Y-%m")
        months[key][txn.type] += txn.amount
    return dict(sorted(months.items()))


def to_csv(statement: Statement) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["account_id", statement.account_id])
    writer.writerow(
        ["period", statement.period_start.isoformat(), statement.period_end.isoformat()]
    )
    writer.writerow(["opening_balance", f"{statement.opening_balance:.2f}", statement.currency])
    writer.writerow(["closing_balance", f"{statement.closing_balance:.2f}", statement.currency])
    writer.writerow([])
    writer.writerow(["date", "type", "description", "category", "amount", "balance_after"])
    for txn in statement.transactions:
        sign = "" if txn.type == "credit" else "-"
        writer.writerow(
            [
                txn.created_at.isoformat(timespec="seconds"),
                txn.type,
                txn.description,
                categorize(txn.description),
                f"{sign}{txn.amount:.2f}",
                f"{txn.balance_after:.2f}",
            ]
        )
    writer.writerow([])
    writer.writerow(["total_credits", f"{statement.total_credits:.2f}"])
    writer.writerow(["total_debits", f"{statement.total_debits:.2f}"])
    writer.writerow(["net_change", f"{statement.net_change:.2f}"])
    return buffer.getvalue()


def summary(statement: Statement) -> dict[str, object]:
    return {
        "account_id": statement.account_id,
        "currency": statement.currency,
        "period_start": statement.period_start.isoformat(),
        "period_end": statement.period_end.isoformat(),
        "opening_balance": f"{statement.opening_balance:.2f}",
        "closing_balance": f"{statement.closing_balance:.2f}",
        "total_credits": f"{statement.total_credits:.2f}",
        "total_debits": f"{statement.total_debits:.2f}",
        "net_change": f"{statement.net_change:.2f}",
        "largest_debit": f"{statement.largest_debit:.2f}",
        "transaction_count": statement.transaction_count,
        "spending": spending_breakdown(statement),
    }


def detect_recurring(history: list[Transaction], min_occurrences: int = 3) -> list[dict[str, str]]:
    """Repère les paiements récurrents (même marchand, même montant, intervalle régulier)."""
    groups: dict[tuple[str, Decimal], list[Transaction]] = defaultdict(list)
    for txn in history:
        if txn.type != "debit":
            continue
        groups[(txn.description.lower(), txn.amount)].append(txn)
    recurring = []
    for (description, amount), items in groups.items():
        if len(items) < min_occurrences:
            continue
        items.sort(key=lambda txn: txn.created_at)
        gaps = [
            (later.created_at - earlier.created_at).days
            for earlier, later in zip(items, items[1:], strict=False)
        ]
        average_gap = sum(gaps) / len(gaps)
        if average_gap == 0:
            continue
        regular = all(abs(gap - average_gap) <= 3 for gap in gaps)
        if not regular:
            continue
        if 25 <= average_gap <= 35:
            frequency = "monthly"
        elif 6 <= average_gap <= 8:
            frequency = "weekly"
        else:
            frequency = f"every {average_gap:.0f} days"
        recurring.append(
            {
                "description": description,
                "amount": f"{amount:.2f}",
                "occurrences": str(len(items)),
                "frequency": frequency,
                "last_seen": items[-1].created_at.date().isoformat(),
            }
        )
    return sorted(recurring, key=lambda row: row["description"])
