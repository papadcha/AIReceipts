# -*- coding: utf-8 -*-
"""
Parses a "Καρτέλα Πελάτη/Προμηθευτή" (customer/supplier statement of account)
PDF exported from Greek accounting software, and computes the list of
still-open (unpaid / partially paid) invoices using FIFO allocation (oldest
invoice gets paid first by the next payment), exactly like the real
bookkeeping does.

Usage as a library:
    from core.ledger_parser import parse_ledger
    ledger = parse_ledger("karteles.pdf")
    ledger.open_invoices   -> list[OpenInvoice]
    ledger.total_open      -> float
    ledger.customer_name   -> str
    ledger.customer_afm    -> str
    ledger.next_receipt_no -> int
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

AMOUNT_RE = r"\d{1,3}(?:\.\d{3})*,\d{2}"
DATE_RE = r"\d{1,2}/\d{1,2}/\d{4}"

TXN_RE = re.compile(
    rf"(?P<date>{DATE_RE})\n"
    rf"(?P<code>\S+)\n"
    rf"(?P<desc>[^\n]+)\n"
    rf"(?P<n1>{AMOUNT_RE})\n"
    rf"(?P<n2>{AMOUNT_RE})\n"
    rf"(?P<n3>{AMOUNT_RE})"
    rf"(?:\n(?P<n4>{AMOUNT_RE}))?"
)

OPENING_RE = re.compile(
    rf"Από μεταφορά\n"
    rf"(?P<n1>{AMOUNT_RE})\n"
    rf"(?P<n2>{AMOUNT_RE})\n"
    rf"(?P<n3>{AMOUNT_RE})\n"
    rf"(?P<n4>{AMOUNT_RE})"
)

CUSTOMER_RE = re.compile(
    r"\n(\d+)\s+(\d{4,7})\s+(.+?)\n(\d{8,12})\n"
)

RECEIPT_CODE_RE = re.compile(r"^([Α-Ωa-zA-Z]+-?)(\d+)$")


def parse_amount(s: str) -> float:
    return round(float(s.replace(".", "").replace(",", ".")), 2)


def fmt_amount(v: float) -> str:
    s = f"{v:,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


@dataclass
class Transaction:
    date: str          # dd/mm/yyyy as printed
    code: str           # e.g. ΧΑΕ-0340 or 00ΚΤΔ00892
    desc: str
    amount: float
    kind: str           # 'debit' (τιμολόγιο - αυξάνει οφειλή) or 'credit' (απόδειξη - μειώνει οφειλή)
    printed_balance: float  # balance printed on the row (Υπόλοιπο), for cross-check


@dataclass
class OpenInvoice:
    label: str       # code, e.g. "00ΚΤΔ00892" or "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ"
    date: str
    remaining: float


@dataclass
class Ledger:
    customer_name: str
    customer_afm: str
    opening_balance: float
    transactions: list = field(default_factory=list)
    open_invoices: list = field(default_factory=list)
    total_open: float = 0.0
    next_receipt_no: int = 1
    receipt_prefix: str = "ΧΑΕ-"
    receipt_padding: int = 4
    balance_check_ok: bool = True
    balance_check_detail: str = ""


def extract_text(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    return "\n".join(page.get_text() for page in doc)


def parse_ledger(pdf_path: str, exclude_codes: set[str] | None = None) -> Ledger:
    """exclude_codes: παραστατικά (π.χ. {'ΧΑΕ-0340','ΧΑΕ-0341'}) που αγνοούνται
    εντελώς, σαν να μην είχαν ποτέ εκδοθεί -- χρήσιμο για "τι θα γινόταν αν"
    αναδημιουργία (π.χ. ακύρωση παλιών αποδείξεων και επανέκδοση με σωστό
    διαχωρισμό ≤500€)."""
    exclude_codes = exclude_codes or set()
    text = extract_text(pdf_path)

    m_cust = CUSTOMER_RE.search(text)
    customer_name = re.sub(r"\s+", " ", m_cust.group(3)).strip() if m_cust else "ΑΓΝΩΣΤΟΣ ΠΕΛΑΤΗΣ"
    customer_afm = m_cust.group(4).strip() if m_cust else ""

    m_open = OPENING_RE.search(text)
    opening_balance = parse_amount(m_open.group("n4")) if m_open else 0.0

    transactions: list[Transaction] = []
    for m in TXN_RE.finditer(text):
        desc = m.group("desc").strip()
        n1 = parse_amount(m.group("n1"))
        n4 = parse_amount(m.group("n4") or m.group("n3"))
        if "Τιμολόγιο" in desc:
            kind = "debit"
        elif "Απόδειξη" in desc or "Πίστωση" in desc:
            kind = "credit"
        else:
            # Unknown row type -> skip defensively rather than mis-book it
            continue
        transactions.append(
            Transaction(
                date=m.group("date"),
                code=m.group("code"),
                desc=desc,
                amount=n1,
                kind=kind,
                printed_balance=n4,
            )
        )

    # ---- FIFO simulation over the open invoice queue, with self-check ----
    sim_transactions = [t for t in transactions if t.code not in exclude_codes]

    queue: list[OpenInvoice] = []
    if opening_balance > 0:
        queue.append(OpenInvoice("ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ", "-", opening_balance))

    running_balance = opening_balance
    mismatches = []
    for t in sim_transactions:
        if t.kind == "debit":
            queue.append(OpenInvoice(t.code, t.date, t.amount))
            running_balance = round(running_balance + t.amount, 2)
        else:
            remaining_credit = t.amount
            while remaining_credit > 0.005 and queue:
                head = queue[0]
                pay = min(head.remaining, remaining_credit)
                head.remaining = round(head.remaining - pay, 2)
                remaining_credit = round(remaining_credit - pay, 2)
                if head.remaining <= 0.005:
                    queue.pop(0)
            running_balance = round(running_balance - t.amount, 2)

        if not exclude_codes and abs(running_balance - t.printed_balance) > 0.02:
            mismatches.append(
                f"{t.date} {t.code}: υπολογισμένο υπόλοιπο {running_balance} "
                f"!= τυπωμένο {t.printed_balance}"
            )

    queue = [inv for inv in queue if inv.remaining > 0.005]
    total_open = round(sum(inv.remaining for inv in queue), 2)

    # ---- next receipt number (from ΧΑΕ-like codes among credit rows) ----
    prefix, padding, max_no = "ΧΑΕ-", 4, 0
    for t in sim_transactions:
        if t.kind != "credit":
            continue
        m = RECEIPT_CODE_RE.match(t.code)
        if not m:
            continue
        digits = m.group(2)
        num = int(digits)
        if num > max_no:
            max_no = num
            prefix = m.group(1)
            padding = len(digits)

    ledger = Ledger(
        customer_name=customer_name,
        customer_afm=customer_afm,
        opening_balance=opening_balance,
        transactions=transactions,
        open_invoices=queue,
        total_open=total_open,
        next_receipt_no=max_no + 1 if max_no else 1,
        receipt_prefix=prefix,
        receipt_padding=padding,
        balance_check_ok=not mismatches,
        balance_check_detail="\n".join(mismatches),
    )
    return ledger
