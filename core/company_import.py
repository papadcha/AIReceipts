# -*- coding: utf-8 -*-
"""Εξαγωγή στοιχείων κεφαλίδας εταιρείας από ένα δείγμα απόδειξης PDF --
είτε παραγμένη από αυτή την εφαρμογή είτε από τον πρόγονο CLI
(laxefsis-receipts). Η κεφαλίδα γράφεται πάντα ως 5 σταθερές γραμμές
(επωνυμία/υπότιτλος/διεύθυνση/ΑΦΜ-ΔΟΥ-ΓΕΜΗ/email), βλ.
core/receipt_pdf.py::_company_header, πριν τον τίτλο "ΑΠΟΔΕΙΞΗ ΕΙΣΠΡΑΞΗΣ"
ή "ΑΠΟΔΕΙΞΗ ΠΛΗΡΩΜΗΣ". Απλή προσυμπλήρωση των ήδη επεξεργάσιμων πεδίων στο
βήμα "Εταιρεία" -- δεν αποθηκεύεται τίποτα αυτόματα, ο χρήστης επιβεβαιώνει/
διορθώνει πριν προχωρήσει."""
from __future__ import annotations

import re

import fitz  # PyMuPDF

RECEIPT_MARKER_RE = re.compile(r"^ΑΠΟΔΕΙΞΗ (ΕΙΣΠΡΑΞΗΣ|ΠΛΗΡΩΜΗΣ)$")

FIELDS = ("name", "subtitle", "address_line", "ids_line", "email")


def extract_company_header(pdf_path: str) -> dict[str, str]:
    """Οι γραμμές πριν τον πρώτο τίτλο "ΑΠΟΔΕΙΞΗ ...", με τη σειρά
    name/subtitle/address_line/ids_line/email. Ό,τι λείπει γίνεται κενό
    string -- ποτέ IndexError σε PDF με ελλιπή/διαφορετική κεφαλίδα."""
    doc = fitz.open(pdf_path)
    text = doc[0].get_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    header_lines: list[str] = []
    for line in lines:
        if RECEIPT_MARKER_RE.match(line):
            break
        header_lines.append(line)

    values = header_lines[:5] + [""] * max(0, 5 - len(header_lines))
    return dict(zip(FIELDS, values))
