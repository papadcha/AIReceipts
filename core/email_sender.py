# -*- coding: utf-8 -*-
"""Αποστολή αποδείξεων με email μέσω SMTP -- στοιχεία σύνδεσης ανά εταιρεία
(core/db.py::companies.smtp_*), όχι ένας κοινός λογαριασμός, ώστε κάθε
εταιρεία να στέλνει από το δικό της. Τα στοιχεία μένουν τοπικά σε κάθε
σταθμό εργασίας (core/sync.py δεν τα συγχρονίζει ποτέ)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_receipt_email(
    *,
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_email: str | None,
    smtp_password: str | None,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[str],
) -> dict:
    if not smtp_host or not smtp_email or not smtp_password:
        return {"ok": False, "error": "Δεν έχουν οριστεί στοιχεία SMTP για αυτή την εταιρεία."}
    if not to_addr:
        return {"ok": False, "error": "Δεν δόθηκε παραλήπτης."}

    msg = EmailMessage()
    msg["From"] = smtp_email
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        with open(path, "rb") as f:
            data = f.read()
        msg.add_attachment(
            data, maintype="application", subtype="pdf", filename=os.path.basename(path),
        )

    try:
        with smtplib.SMTP(smtp_host, smtp_port or 587, timeout=30) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001 -- εμφανές μήνυμα στο χρήστη, όχι crash
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
