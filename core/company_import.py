# -*- coding: utf-8 -*-
"""Εξαγωγή στοιχείων κεφαλίδας εταιρείας-εκδότη από ένα δείγμα PDF, με δύο
αναγνωρίσιμες μορφές:

1. Δείγμα απόδειξης -- είτε παραγμένη από αυτή την εφαρμογή είτε από τον
   πρόγονο CLI (laxefsis-receipts). Η κεφαλίδα γράφεται πάντα ως 5 σταθερές
   γραμμές (επωνυμία/υπότιτλος/διεύθυνση/ΑΦΜ-ΔΟΥ-ΓΕΜΗ/email), βλ.
   core/receipt_pdf.py::_company_header, πριν τον τίτλο "ΑΠΟΔΕΙΞΗ ΕΙΣΠΡΑΞΗΣ"
   ή "ΑΠΟΔΕΙΞΗ ΠΛΗΡΩΜΗΣ".
2. Κάρτελα πελάτη/προμηθευτή (το ίδιο export του λογιστικού που διαβάζει το
   core/ledger_parser.py) -- η κεφαλίδα εκεί είναι η εταιρεία-εκδότης του
   ίδιου του λογιστικού βιβλίου, σε μορφή "ΕΠΩΝΥΜΙΑ, ΕΔΡΑ" / διεύθυνση /
   "Α.Φ.Μ. :... ΔΟΥ :..." / "Γ.Ε.ΜΗ :...". Δεν έχει υπότιτλο/email -- μένουν
   κενά, ο χρήστης τα συμπληρώνει χειροκίνητα όπως και τα υπόλοιπα πεδία.

Απλή προσυμπλήρωση των ήδη επεξεργάσιμων πεδίων στο βήμα "Εταιρεία" -- δεν
αποθηκεύεται τίποτα αυτόματα, ο χρήστης επιβεβαιώνει/διορθώνει πριν
προχωρήσει."""
from __future__ import annotations

import re

import fitz  # PyMuPDF

RECEIPT_MARKER_RE = re.compile(r"^ΑΠΟΔΕΙΞΗ (ΕΙΣΠΡΑΞΗΣ|ΠΛΗΡΩΜΗΣ)$")
KARTELA_AFM_RE = re.compile(r"^Α\.Φ\.Μ\.\s*:\s*(\d+)\s+ΔΟΥ\s*:\s*(\S+)$")
KARTELA_GEMI_RE = re.compile(r"^Γ\.Ε\.ΜΗ\s*:\s*(\d+)$")

FIELDS = ("name", "subtitle", "address_line", "ids_line", "email")
EMPTY_FIELDS = dict(zip(FIELDS, [""] * 5))


def _from_receipt(lines: list[str]) -> dict[str, str] | None:
    header_lines: list[str] = []
    for line in lines:
        if RECEIPT_MARKER_RE.match(line):
            values = header_lines[:5] + [""] * max(0, 5 - len(header_lines))
            return dict(zip(FIELDS, values))
        header_lines.append(line)
    return None


def _from_kartela(lines: list[str]) -> dict[str, str] | None:
    """Εντοπίζει τη γραμμή ΑΦΜ/ΔΟΥ (πολύ χαρακτηριστική, χαμηλό ρίσκο ψευδούς
    ταιριάσματος) και διαβάζει επωνυμία/διεύθυνση από τις δύο ακριβώς
    προηγούμενες γραμμές -- ανεξάρτητα από πόσες γραμμές τίτλου/σελίδας
    προηγούνται, σε αντίθεση με τη σταθερή μέτρηση από την αρχή του PDF στο
    _from_receipt (αυτό ακριβώς έσπασε όταν δόθηκε κάρτελα στο παλιό κώδικα)."""
    for i, line in enumerate(lines):
        m_afm = KARTELA_AFM_RE.match(line)
        if not m_afm or i < 2:
            continue
        afm, doy = m_afm.groups()
        address = lines[i - 1]
        name = re.sub(r",?\s*ΕΔΡΑ\s*$", "", lines[i - 2]).strip()

        gemi = ""
        if i + 1 < len(lines):
            m_gemi = KARTELA_GEMI_RE.match(lines[i + 1])
            if m_gemi:
                gemi = m_gemi.group(1)

        ids_parts = [f"ΑΦΜ: {afm}", f"ΔΟΥ: {doy}"]
        if gemi:
            ids_parts.append(f"Αρ. Γ.Ε.ΜΗ: {gemi}")

        return {
            "name": name,
            "subtitle": "",
            "address_line": address,
            "ids_line": " – ".join(ids_parts),
            "email": "",
        }
    return None


def extract_company_header(pdf_path: str) -> dict[str, str]:
    """Δοκιμάζει πρώτα τη μορφή απόδειξης, μετά τη μορφή κάρτελας. Αν καμία
    δεν αναγνωριστεί, όλα τα πεδία μένουν κενά string -- ποτέ IndexError, και
    ποτέ σιωπηλή αποδοχή τυχαίων πρώτων γραμμών από άσχετο PDF (βλ. wizard.py
    για το προειδοποιητικό μήνυμα όταν όλα είναι κενά)."""
    doc = fitz.open(pdf_path)
    text = doc[0].get_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    return _from_receipt(lines) or _from_kartela(lines) or dict(EMPTY_FIELDS)
