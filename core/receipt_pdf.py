# -*- coding: utf-8 -*-
"""Generates a two-copy "Απόδειξη Είσπραξης / Πληρωμής" PDF, in the layout
used by Greek small-business printed receipt books. Company header and
direction (collection vs. payment) are fully parameterized via ReceiptData.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from core.amount_words import amount_to_words

FONT_DIR = r"C:\Windows\Fonts"
pdfmetrics.registerFont(TTFont("Arial", os.path.join(FONT_DIR, "arial.ttf")))
pdfmetrics.registerFont(TTFont("Arial-Bold", os.path.join(FONT_DIR, "arialbd.ttf")))
pdfmetrics.registerFont(TTFont("Arial-Italic", os.path.join(FONT_DIR, "ariali.ttf")))

BRAND = colors.HexColor("#1a4f78")
BORDER = colors.HexColor("#333333")

STYLES = {
    "title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=15, textColor=BRAND, leading=17),
    "subtitle": ParagraphStyle("subtitle", fontName="Arial-Bold", fontSize=9, leading=11),
    "small": ParagraphStyle("small", fontName="Arial", fontSize=7.5, leading=9),
    "heading": ParagraphStyle("heading", fontName="Arial-Bold", fontSize=11, leading=13, alignment=1),
    "label": ParagraphStyle("label", fontName="Arial-Bold", fontSize=8.5, leading=10),
    "value": ParagraphStyle("value", fontName="Arial", fontSize=9, leading=11),
    "body": ParagraphStyle("body", fontName="Arial", fontSize=8.5, leading=11),
    "cellhdr": ParagraphStyle("cellhdr", fontName="Arial-Bold", fontSize=7.5, leading=9, alignment=1),
    "cell": ParagraphStyle("cell", fontName="Arial", fontSize=8.5, leading=10, alignment=1),
    "footlabel": ParagraphStyle("footlabel", fontName="Arial-Bold", fontSize=8, leading=10),
}


@dataclass
class ReceiptData:
    topos: str
    date: str          # dd/mm/yyyy
    seira: str
    no: str
    amount: float
    customer_name: str
    occupation: str
    address: str
    aitiologia: str
    is_payment: bool  # True -> "Πληρώσαμε τον..." / ΑΠΟΔΕΙΞΗ ΠΛΗΡΩΜΗΣ και στα δύο μισά
    company_name: str
    company_subtitle: str
    company_address_line: str
    company_ids_line: str
    company_email: str
    signature_path: str | None  # εικόνα υπογραφής υπευθύνου, μπαίνει στη στήλη
    # "Ο Λαβών" όταν εισπράττουμε ή "Ο Πληρώσας" όταν πληρώνουμε (βλ. is_payment)


def _company_header(r: ReceiptData):
    rows = [
        [Paragraph(r.company_name, STYLES["title"])],
        [Paragraph(r.company_subtitle, STYLES["subtitle"])],
        [Paragraph(r.company_address_line, STYLES["small"])],
        [Paragraph(r.company_ids_line, STYLES["small"])],
        [Paragraph(r.company_email, STYLES["small"])],
    ]
    t = Table(rows, colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
    ]))
    return t


def _topline_table(kind_label: str, r: ReceiptData):
    data = [
        ["ΤΟΠΟΣ:", r.topos, "ΗΜΕΡΟΜΗΝΙΑ:", r.date, "ΣΕΙΡΑ", r.seira, "No", r.no],
        ["ΠΟΣΟ:", f"{r.amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."), "", "", "", "", "", ""],
    ]
    t = Table(data, colWidths=[20 * mm, 30 * mm, 26 * mm, 22 * mm, 14 * mm, 12 * mm, 10 * mm, 16 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Arial"),
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("SPAN", (1, 1), (-1, 1)),
        ("FONTNAME", (0, 0), (0, -1), "Arial-Bold"),
        ("FONTNAME", (2, 0), (2, 0), "Arial-Bold"),
        ("FONTNAME", (4, 0), (4, 0), "Arial-Bold"),
        ("FONTNAME", (6, 0), (6, 0), "Arial-Bold"),
        ("FONTNAME", (1, 0), (1, 1), "Arial"),
        ("FONTNAME", (3, 0), (3, 0), "Arial"),
        ("FONTNAME", (5, 0), (5, 0), "Arial"),
        ("FONTNAME", (7, 0), (7, 0), "Arial"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (1, 1), (1, 1), "LEFT"),
        ("LEFTPADDING", (1, 1), (1, 1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _customer_line(r: ReceiptData):
    verb = "Πληρώσαμε τον" if r.is_payment else "Εισπράξαμε από τον"
    text = (
        f"{verb} <b>{r.customer_name}</b> "
        f"Επάγγελμα: {r.occupation}  Διεύθυνση: {r.address}"
    )
    t = Table([[Paragraph(text, STYLES["body"])]], colWidths=[150 * mm])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _cash_table(r: ReceiptData):
    amt_str = f"{r.amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    header = ["ΤΡΑΠΕΖΑ / ΜΕΤΡΗΤΑ", "ΑΡΙΘΜΟΣ", "ΗΜΕΡΟΜΗΝΙΑ", "ΠΟΣΟ"]
    data = [[Paragraph(h, STYLES["cellhdr"]) for h in header]]
    data.append([Paragraph("Μετρητά", STYLES["cell"]), "", "", Paragraph(amt_str, STYLES["cell"])])
    for _ in range(3):
        data.append(["", "", "", ""])

    words = amount_to_words(r.amount)
    data.append([
        Paragraph(f"<b>ΠΟΣΟ ΟΛΟΓΡΑΦΩΣ:</b> {words}", STYLES["body"]),
        "", "",
        Paragraph(f"<b>ΓΕΝ. ΣΥΝΟΛΟ</b><br/>{amt_str}", STYLES["cell"]),
    ])

    t = Table(data, colWidths=[65 * mm, 30 * mm, 30 * mm, 25 * mm], rowHeights=[6 * mm] + [5.5 * mm] * 4 + [8 * mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Arial"),
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("INNERGRID", (0, 0), (-1, -2), 0.4, BORDER),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, BORDER),
        ("SPAN", (0, -1), (2, -1)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 1), (0, -2), "CENTER"),
        ("ALIGN", (3, 1), (3, -2), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, -1), (0, -1), 5),
    ]))
    return t


def _fit_image(path: str, max_w: float, max_h: float):
    """Εικόνα υπογραφής, scaled ώστε να χωράει στο κελί κρατώντας αναλογία."""
    try:
        iw, ih = ImageReader(path).getSize()
    except Exception:  # noqa: BLE001 -- αρχείο άκυρο/λείπει, κελί μένει κενό
        return ""
    scale = min(max_w / iw, max_h / ih)
    return Image(path, width=iw * scale, height=ih * scale)


def _footer_table(r: ReceiptData):
    labon_cell, plirosas_cell = "", ""
    if r.signature_path:
        sig = _fit_image(r.signature_path, max_w=32 * mm, max_h=12 * mm)
        if r.is_payment:
            plirosas_cell = sig  # η εταιρεία πληρώνει -> υπογράφει ως "Ο Πληρώσας"
        else:
            labon_cell = sig  # η εταιρεία εισπράττει -> υπογράφει ως "Ο Λαβών"

    data = [
        [Paragraph("<b>Αιτιολογία</b>", STYLES["footlabel"]),
         Paragraph("<b>Ο Λαβών</b>", STYLES["footlabel"]),
         Paragraph("<b>Ο Πληρώσας</b>", STYLES["footlabel"])],
        [Paragraph(r.aitiologia.replace("\n", "<br/>"), STYLES["body"]), labon_cell, plirosas_cell],
    ]
    t = Table(data, colWidths=[85 * mm, 35 * mm, 40 * mm], rowHeights=[5.5 * mm, 14 * mm])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 1), (2, 1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _build_half(kind_label: str, r: ReceiptData):
    flow = [
        _company_header(r),
        Spacer(1, 2 * mm),
        Paragraph(f"<u>{kind_label}</u>", STYLES["heading"]),
        Spacer(1, 1.5 * mm),
        _topline_table(kind_label, r),
        Spacer(1, 1.5 * mm),
        _customer_line(r),
        Spacer(1, 1.5 * mm),
        _cash_table(r),
        Spacer(1, 1.5 * mm),
        _footer_table(r),
    ]
    return flow


def build_receipt_pdf(output_path: str, r: ReceiptData):
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm,
        title=f"Απόδειξη {r.no}",
    )
    first_label = "ΑΠΟΔΕΙΞΗ ΠΛΗΡΩΜΗΣ" if r.is_payment else "ΑΠΟΔΕΙΞΗ ΕΙΣΠΡΑΞΗΣ"
    story = []
    story += _build_half(first_label, r)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#888888"), dash=(3, 2)))
    story.append(Spacer(1, 4 * mm))
    story += _build_half("ΑΠΟΔΕΙΞΗ ΠΛΗΡΩΜΗΣ", r)
    doc.build(story)
