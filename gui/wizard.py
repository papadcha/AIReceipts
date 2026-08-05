# -*- coding: utf-8 -*-
"""Αiποδείξεις -- wizard GUI (Φάση 1 / MVP, χωρίς persistence) πάνω στο
core/ layer. Κάθε βήμα αντιστοιχεί σε κάτι που χρειάστηκε χειροκίνητη
προσοχή στις δύο πραγματικές περιπτώσεις (ΠΕΛΑΤΗΣ, ΠΡΟΜΗΘΕΥΤΗΣ) -- βλ.
DESKTOP_APP_PLAN.md στο αρχικό project."""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDateEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget, QWizard, QWizardPage,
)

from core import company_import, db as dbmod, email_sender, presence
from core import sync as syncmod
from core.allocation import (
    allocate_receipts, build_aitiologia, linear_dates,
    spread_dates_respecting_invoices,
)
from core.ledger_parser import OpenInvoice, fmt_amount, parse_ledger
from core.receipt_pdf import ReceiptData, build_receipt_pdf

DB = dbmod.get_connection()

(
    PAGE_COMPANY, PAGE_LEDGER, PAGE_CONTACT, PAGE_AMOUNT, PAGE_DATES,
    PAGE_PREVIEW, PAGE_GENERATE,
) = range(7)

DATE_FMT_QT = "dd/MM/yyyy"

# Ίδιο χρωματολόγιο με τα αδερφά projects (expvault/lab-galatista, βλ.
# css/app.css --status-ok / --status-danger) -- πράσινο/κόκκινο σημαίνουν
# πάντα το ίδιο πράγμα σε όλα τα εργαλεία της οικογένειας.
STATUS_OK_COLOR = "#16a34a"
STATUS_OK_BG = "rgba(22,163,74,0.16)"
STATUS_OK_BORDER = "rgba(22,163,74,0.35)"
STATUS_DANGER_COLOR = "#dc2626"
STATUS_DANGER_BG = "rgba(220,38,38,0.15)"
STATUS_DANGER_BORDER = "rgba(220,38,38,0.4)"


def _qdate_to_dt(qd: QDate) -> datetime:
    return datetime(qd.year(), qd.month(), qd.day())


def _dt_to_qdate(dt: datetime) -> QDate:
    return QDate(dt.year, dt.month, dt.day)


# ---------------------------------------------------------------- Page 1 --
class CompanyPage(QWizardPage):
    """Ρητή επιλογή εταιρείας-εκδότη -- το λάθος που έγινε αρχικά ήταν να
    θεωρηθεί αυτόματα η προεπιλεγμένη εταιρεία (ΕΤΑΙΡΕΙΑ ΙΚΕ) χωρίς
    επιβεβαίωση, ενώ η καρτέλα αφορούσε άλλη εταιρεία (ΑΛΛΗ ΕΤΑΙΡΕΙΑ)."""

    def __init__(self):
        super().__init__()
        self.setTitle("Εταιρεία - εκδότης")
        self.setSubTitle("Ποια εταιρεία εμφανίζεται στην κεφαλίδα της απόδειξης;")

        self.company_combo = QComboBox()
        self._reload_companies()
        self.company_combo.currentIndexChanged.connect(self._on_company_selected)

        import_btn = QPushButton("Εισαγωγή στοιχείων από δείγμα PDF...")
        import_btn.clicked.connect(self._import_from_pdf)

        self.name = QLineEdit()
        self.subtitle = QLineEdit()
        self.address = QLineEdit()
        self.ids = QLineEdit()
        self.email = QLineEdit()
        self.receipt_prefix = QLineEdit("ΧΑΕ-")
        self.receipt_padding = QSpinBox()
        self.receipt_padding.setRange(1, 10)
        self.receipt_padding.setValue(4)

        self.signature_path_edit = QLineEdit()
        self.signature_path_edit.setReadOnly(True)
        sig_browse_btn = QPushButton("Επιλογή αρχείου...")
        sig_browse_btn.clicked.connect(self._browse_signature)
        sig_row = QHBoxLayout()
        sig_row.addWidget(self.signature_path_edit)
        sig_row.addWidget(sig_browse_btn)

        form = QFormLayout()
        form.addRow("Αποθηκευμένη εταιρεία:", self.company_combo)
        form.addRow("", import_btn)
        form.addRow("Επωνυμία:", self.name)
        form.addRow("Υπότιτλος:", self.subtitle)
        form.addRow("Διεύθυνση/τηλ.:", self.address)
        form.addRow("ΑΦΜ/ΔΟΥ/ΓΕΜΗ:", self.ids)
        form.addRow("Email:", self.email)
        form.addRow("Πρόθεμα αρ. απόδειξης:", self.receipt_prefix)
        form.addRow("Ψηφία αρίθμησης:", self.receipt_padding)
        form.addRow("Υπογραφή υπευθύνου (εικόνα):", sig_row)

        # -- SMTP: τοπικά ανά σταθμό, όχι μέσα στο κανονικό sync manifest
        # (core/sync.py) -- ένας σταθμός δεν σημαίνει αυτόματα διαφορετική
        # εταιρεία, οπότε το κουμπί "Κοινή χρήση" στέλνει το password μία
        # φορά σε άλλο σταθμό μέσω του ίδιου remote, με άμεση διαγραφή μετά
        # την παραλαβή -- δεν κάθεται μόνιμα εκεί.
        self.smtp_host = QLineEdit()
        self.smtp_host.setPlaceholderText("π.χ. smtp.gmail.com")
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(587)
        self.smtp_email = QLineEdit()
        self.smtp_email.setPlaceholderText("λογαριασμός αποστολής")
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        share_btn = QPushButton("Κοινή χρήση password σε άλλο σταθμό...")
        share_btn.clicked.connect(self._share_smtp_password)

        smtp_form = QFormLayout()
        smtp_form.addRow("SMTP server:", self.smtp_host)
        smtp_form.addRow("Θύρα:", self.smtp_port)
        smtp_form.addRow("Λογαριασμός:", self.smtp_email)
        smtp_form.addRow("Κωδικός (app password):", self.smtp_password)
        smtp_form.addRow("", share_btn)
        smtp_box = QGroupBox("Αποστολή email (SMTP) -- προαιρετικό, τοπικό ανά σταθμό")
        smtp_box.setLayout(smtp_form)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(smtp_box)
        self.setLayout(layout)

        for w in (self.name, self.subtitle, self.address, self.ids, self.email):
            w.textChanged.connect(self.completeChanged)

        self._company_id = None
        self._default_cap = None
        self._default_round_step = None
        self._output_dir = None

    def _browse_signature(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Επιλογή εικόνας υπογραφής", "",
            "Εικόνες (*.png *.jpg *.jpeg *.bmp)",
        )
        if path:
            self.signature_path_edit.setText(path)

    def _share_smtp_password(self):
        name = self.name.text().strip()
        if not name:
            return
        if not self.smtp_password.text():
            QMessageBox.warning(self, "Δεν υπάρχει κωδικός", "Συμπλήρωσε πρώτα τον κωδικό SMTP.")
            return
        # αποθηκεύουμε πρώτα τοπικά ό,τι φαίνεται στη φόρμα, ώστε το μοίρασμα
        # να στείλει ακριβώς αυτό που βλέπει ο χρήστης
        self.validatePage()
        result = syncmod.share_smtp_password(DB, name)
        if result.get("ok"):
            QMessageBox.information(
                self, "Έγινε το μοίρασμα",
                "Ο κωδικός ανέβηκε μία φορά -- θα παραληφθεί αυτόματα και θα "
                "διαγραφεί από το remote στο επόμενο άνοιγμα της εφαρμογής σε άλλο "
                "σταθμό που ξέρει ήδη αυτή την εταιρεία.",
            )
        else:
            QMessageBox.critical(self, "Σφάλμα", result.get("error", "Άγνωστο σφάλμα"))

    def _import_from_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Επιλογή δείγματος απόδειξης PDF", "", "PDF (*.pdf)",
        )
        if not path:
            return
        try:
            fields = company_import.extract_company_header(path)
        except Exception as exc:  # noqa: BLE001 -- εμφανές μήνυμα στο χρήστη
            QMessageBox.critical(self, "Σφάλμα ανάγνωσης", f"Δεν διαβάστηκε το PDF:\n{exc}")
            return
        if not any(fields.values()):
            QMessageBox.warning(
                self, "Καμία κεφαλίδα δεν αναγνωρίστηκε",
                "Δεν βρέθηκαν στοιχεία εταιρείας πριν τον τίτλο της απόδειξης σε αυτό "
                "το PDF -- συμπλήρωσε τα πεδία χειροκίνητα.",
            )
            return
        self.name.setText(fields["name"])
        self.subtitle.setText(fields["subtitle"])
        self.address.setText(fields["address_line"])
        self.ids.setText(fields["ids_line"])
        self.email.setText(fields["email"])

    def _reload_companies(self):
        self.company_combo.blockSignals(True)
        self.company_combo.clear()
        self.company_combo.addItem("-- Νέα εταιρεία --", None)
        for row in dbmod.list_companies(DB):
            self.company_combo.addItem(row["name"], row["id"])
        self.company_combo.blockSignals(False)

    def _on_company_selected(self, index: int):
        company_id = self.company_combo.itemData(index)
        self._company_id = company_id
        if company_id is None:
            self._default_cap = None
            self._default_round_step = None
            self._output_dir = None
            self.signature_path_edit.setText("")
            self.smtp_host.setText("")
            self.smtp_port.setValue(587)
            self.smtp_email.setText("")
            self.smtp_password.setText("")
            return
        row = next((r for r in dbmod.list_companies(DB) if r["id"] == company_id), None)
        if row is None:
            return
        self.name.setText(row["name"] or "")
        self.subtitle.setText(row["subtitle"] or "")
        self.address.setText(row["address_line"] or "")
        self.ids.setText(row["ids_line"] or "")
        self.email.setText(row["email"] or "")
        self.receipt_prefix.setText(row["receipt_prefix"] or "ΧΑΕ-")
        self.receipt_padding.setValue(row["receipt_padding"] or 4)
        self.signature_path_edit.setText(row["signature_path"] or "")
        self.smtp_host.setText(row["smtp_host"] or "")
        self.smtp_port.setValue(row["smtp_port"] or 587)
        self.smtp_email.setText(row["smtp_email"] or "")
        self.smtp_password.setText(row["smtp_password"] or "")
        self._default_cap = row["default_cap"]
        self._default_round_step = row["default_round_step"]
        self._output_dir = row["output_dir"]

    def isComplete(self) -> bool:
        return bool(self.name.text().strip())

    def validatePage(self) -> bool:
        wiz = self.wizard()
        wiz.company_name = self.name.text().strip()
        wiz.company_subtitle = self.subtitle.text().strip()
        wiz.company_address = self.address.text().strip()
        wiz.company_ids = self.ids.text().strip()
        wiz.company_email = self.email.text().strip()
        wiz.receipt_prefix = self.receipt_prefix.text()
        wiz.receipt_padding = self.receipt_padding.value()
        wiz.default_cap = self._default_cap
        wiz.default_round_step = self._default_round_step
        wiz.signature_path = self.signature_path_edit.text().strip() or None
        wiz.output_dir = self._output_dir
        wiz.smtp_host = self.smtp_host.text().strip() or None
        wiz.smtp_port = self.smtp_port.value()
        wiz.smtp_email = self.smtp_email.text().strip() or None
        wiz.smtp_password = self.smtp_password.text() or None
        wiz.company_id = self._company_id

        wiz.save_company()
        self._company_id = wiz.company_id
        return True


# ---------------------------------------------------------------- Page 2 --
class LedgerPage(QWizardPage):
    """Προεπισκόπηση των κινήσεων που διάβασε ο parser -- το bug στην
    καρτέλα ΠΡΟΜΗΘΕΥΤΗΣ (0 κινήσεις αναγνωρίστηκαν, λάθος σύνολο 675€ αντί
    2.225€) περνούσε απαρατήρητο χωρίς αυτό το βήμα."""

    def __init__(self):
        super().__init__()
        self.setTitle("Κάρτελα πελάτη / προμηθευτή")
        self.setSubTitle("Φόρτωσε το PDF export από το λογιστικό.")

        self.path_label = QLabel("(δεν έχει επιλεγεί αρχείο)")
        self.path_label.setWordWrap(True)
        browse_btn = QPushButton("Άνοιγμα PDF καρτέλας...")
        browse_btn.clicked.connect(self._browse)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-weight: bold;")

        self.summary_label = QLabel("")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Κωδικός", "Ημερομηνία", "Ανοιχτό ποσό"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.exclude_edit = QLineEdit()
        self.exclude_edit.setPlaceholderText(
            "π.χ. ΧΑΕ-0340, ΧΑΕ-0341 -- αγνοούνται σαν να μην είχαν ποτέ εκδοθεί"
        )
        reparse_btn = QPushButton("Εφαρμογή / επανάληψη ανάλυσης")
        reparse_btn.clicked.connect(self._reparse)
        exclude_row = QHBoxLayout()
        exclude_row.addWidget(self.exclude_edit)
        exclude_row.addWidget(reparse_btn)
        exclude_box = QGroupBox("Εξαίρεση παλιών αποδείξεων (σενάριο επανέκδοσης μετά από ακύρωση)")
        exclude_box.setLayout(exclude_row)

        layout = QVBoxLayout()
        layout.addWidget(browse_btn)
        layout.addWidget(self.path_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)
        layout.addWidget(QLabel("Ανοιχτά τιμολόγια όπως αναγνωρίστηκαν:"))
        layout.addWidget(self.table)
        layout.addWidget(exclude_box)
        self.setLayout(layout)

        self._ledger = None
        self._path = None

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Επιλογή καρτέλας PDF", "", "PDF (*.pdf)")
        if not path:
            return
        self._path = path
        self.path_label.setText(path)
        self._reparse()

    def _reparse(self):
        if not self._path:
            return
        exclude_codes = {c.strip() for c in self.exclude_edit.text().split(",") if c.strip()}
        try:
            ledger = parse_ledger(self._path, exclude_codes=exclude_codes or None)
        except Exception as exc:  # noqa: BLE001 -- εμφανές μήνυμα στο χρήστη
            QMessageBox.critical(self, "Σφάλμα ανάγνωσης", f"Δεν διαβάστηκε το PDF:\n{exc}")
            self._ledger = None
            self.completeChanged.emit()
            return

        self._ledger = ledger
        self.wizard().ledger_path = self._path
        self.wizard().excluded_codes = exclude_codes

        if ledger.balance_check_ok:
            self.status_label.setText("✓ Το υπόλοιπο ταιριάζει σε κάθε γραμμή της καρτέλας")
            self.status_label.setStyleSheet(f"font-weight: bold; color: {STATUS_OK_COLOR};")
        else:
            self.status_label.setText("✗ ΑΣΥΜΦΩΝΙΑ υπολοίπου -- έλεγξε χειροκίνητα πριν προχωρήσεις!")
            self.status_label.setStyleSheet(f"font-weight: bold; color: {STATUS_DANGER_COLOR};")

        self.summary_label.setText(
            f"Πελάτης/Προμηθευτής: {ledger.customer_name}  (ΑΦΜ {ledger.customer_afm})   "
            f"Ανοιχτό υπόλοιπο: {fmt_amount(ledger.total_open)} €   "
            f"Κινήσεις που αναγνωρίστηκαν: {len(ledger.transactions)}"
        )

        self.table.setRowCount(0)
        for inv in ledger.open_invoices:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(inv.label))
            self.table.setItem(row, 1, QTableWidgetItem(inv.date))
            self.table.setItem(row, 2, QTableWidgetItem(f"{fmt_amount(inv.remaining)} €"))

        if len(ledger.transactions) == 0 and ledger.opening_balance <= 0:
            QMessageBox.warning(
                self, "Καμία κίνηση δεν αναγνωρίστηκε",
                "Ο parser δεν βρήκε ούτε αρχικό υπόλοιπο ούτε κινήσεις. Πιθανότατα η "
                "μορφή της καρτέλας δεν αναγνωρίζεται -- έλεγξε το αρχείο πριν συνεχίσεις.",
            )
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._ledger is not None

    def validatePage(self) -> bool:
        if self._ledger and not self._ledger.balance_check_ok:
            reply = QMessageBox.question(
                self, "Ασυμφωνία υπολοίπου",
                "Ο αυτόματος έλεγχος υπολοίπου απέτυχε για αυτή την καρτέλα. "
                "Θέλεις σίγουρα να συνεχίσεις;",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False
        self.wizard().ledger = self._ledger
        return True


# ---------------------------------------------------------------- Page 3 --
class ContactPage(QWizardPage):
    """Επάγγελμα/διεύθυνση δεν υπάρχουν ΠΟΤΕ στην κάρτελα -- ρητό βήμα ώστε
    να μη ξεχαστούν (και στις δύο περιπτώσεις χρειάστηκε να ζητηθούν
    ξεχωριστά ή να βρεθούν από άλλο έγγραφο)."""

    def __init__(self):
        super().__init__()
        self.setTitle("Στοιχεία πελάτη / προμηθευτή")
        self.setSubTitle("Το όνομα/ΑΦΜ έρχονται από την κάρτελα -- τα υπόλοιπα δεν υπάρχουν εκεί.")

        self.name_label = QLabel("-")
        self.afm_label = QLabel("-")
        self.occupation = QLineEdit()
        self.address = QLineEdit()

        form = QFormLayout()
        form.addRow("Όνομα:", self.name_label)
        form.addRow("ΑΦΜ:", self.afm_label)
        form.addRow("Επάγγελμα:", self.occupation)
        form.addRow("Διεύθυνση:", self.address)
        self.setLayout(form)

    def initializePage(self):
        ledger = self.wizard().ledger
        self.name_label.setText(ledger.customer_name)
        self.afm_label.setText(ledger.customer_afm)

        contact = dbmod.get_contact(DB, ledger.customer_afm)
        self.occupation.setText(contact["occupation"] if contact else "")
        self.address.setText(contact["address"] if contact else "")

    def validatePage(self) -> bool:
        wiz = self.wizard()
        wiz.occupation = self.occupation.text().strip() or "-"
        wiz.address = self.address.text().strip() or "-"
        dbmod.upsert_contact(
            DB, afm=wiz.ledger.customer_afm, name=wiz.ledger.customer_name,
            occupation=wiz.occupation, address=wiz.address,
        )
        return True


# ---------------------------------------------------------------- Page 4 --
class AmountPage(QWizardPage):
    """Κατεύθυνση (Είσπραξη/Πληρωμή) + ποσό + όριο + προαιρετική ανάλυση
    αρχικού υπολοίπου σε συγκεκριμένα τιμολόγια (--opening-invoices στο CLI)."""

    def __init__(self):
        super().__init__()
        self.setTitle("Κατεύθυνση, ποσό & όριο")

        self.collection_radio = QRadioButton("Είσπραξη (ο πελάτης μας πληρώνει)")
        self.payment_radio = QRadioButton("Πληρωμή (πληρώνουμε προμηθευτή)")
        self.collection_radio.setChecked(True)
        dir_group = QButtonGroup(self)
        dir_group.addButton(self.collection_radio)
        dir_group.addButton(self.payment_radio)
        self.preview_verb = QLabel()
        self.collection_radio.toggled.connect(self._update_preview)
        self.payment_radio.toggled.connect(self._update_preview)

        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 10_000_000)
        self.amount.setDecimals(2)
        self.amount.setSuffix(" €")

        self.cap = QDoubleSpinBox()
        self.cap.setRange(1, 100000)
        self.cap.setDecimals(2)
        self.cap.setValue(500.0)
        self.cap.setSuffix(" €")

        self.round_step = QDoubleSpinBox()
        self.round_step.setRange(1, 10000)
        self.round_step.setDecimals(2)
        self.round_step.setValue(100.0)
        self.round_step.setSuffix(" €")

        top = QVBoxLayout()
        top.addWidget(self.collection_radio)
        top.addWidget(self.payment_radio)
        top.addWidget(self.preview_verb)

        form = QFormLayout()
        form.addRow("Συνολικό ποσό εξόφλησης:", self.amount)
        form.addRow("Νόμιμο όριο ανά απόδειξη:", self.cap)
        form.addRow('Στρογγυλοποίηση "έναντι":', self.round_step)

        self.opening_table = QTableWidget(0, 3)
        self.opening_table.setHorizontalHeaderLabels(["Κωδικός", "Ημ/νία (dd/mm/yyyy)", "Ποσό"])
        self.opening_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        add_row_btn = QPushButton("+ Γραμμή")
        add_row_btn.clicked.connect(lambda: self.opening_table.insertRow(self.opening_table.rowCount()))
        del_row_btn = QPushButton("- Γραμμή")
        del_row_btn.clicked.connect(lambda: self.opening_table.removeRow(self.opening_table.currentRow()))
        row_btns = QHBoxLayout()
        row_btns.addWidget(add_row_btn)
        row_btns.addWidget(del_row_btn)
        row_btns.addStretch(1)

        box = QGroupBox('Προαιρετικό: ανάλυση "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ" σε συγκεκριμένα τιμολόγια')
        box_layout = QVBoxLayout()
        box_layout.addWidget(self.opening_table)
        box_layout.addLayout(row_btns)
        box.setLayout(box_layout)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addLayout(form)
        layout.addWidget(box)
        self.setLayout(layout)

    def _update_preview(self):
        verb = "Πληρώσαμε τον" if self.payment_radio.isChecked() else "Εισπράξαμε από τον"
        self.preview_verb.setText(f'Κείμενο απόδειξης: "{verb} ..."')

    def initializePage(self):
        wiz = self.wizard()
        ledger = wiz.ledger
        self.amount.setValue(ledger.total_open)
        if wiz.default_cap:
            self.cap.setValue(wiz.default_cap)
        if wiz.default_round_step:
            self.round_step.setValue(wiz.default_round_step)
        self._update_preview()

        has_opening_balance = any(inv.label == "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ" for inv in ledger.open_invoices)
        self.opening_table.setRowCount(0)
        if has_opening_balance:
            saved = dbmod.get_opening_breakdown(DB, ledger.customer_afm)
            for r in saved:
                row = self.opening_table.rowCount()
                self.opening_table.insertRow(row)
                self.opening_table.setItem(row, 0, QTableWidgetItem(r["code"]))
                self.opening_table.setItem(row, 1, QTableWidgetItem(r["date"]))
                self.opening_table.setItem(row, 2, QTableWidgetItem(fmt_amount(r["amount"])))

    def validatePage(self) -> bool:
        wiz = self.wizard()
        wiz.is_payment = self.payment_radio.isChecked()
        wiz.amount = self.amount.value()
        wiz.cap = self.cap.value()
        wiz.round_step = self.round_step.value()
        wiz.default_cap = wiz.cap
        wiz.default_round_step = wiz.round_step
        wiz.save_company()

        overrides = []
        for row in range(self.opening_table.rowCount()):
            code_item = self.opening_table.item(row, 0)
            date_item = self.opening_table.item(row, 1)
            amt_item = self.opening_table.item(row, 2)
            code = code_item.text().strip() if code_item else ""
            date = date_item.text().strip() if date_item else ""
            amt_text = amt_item.text().strip() if amt_item else ""
            if not code and not date and not amt_text:
                continue
            try:
                amt = float(amt_text.replace(",", "."))
                datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                QMessageBox.warning(
                    self, "Λάθος γραμμή ανάλυσης",
                    f"Η γραμμή {row + 1} έχει μη έγκυρη ημερομηνία ή ποσό "
                    "(μορφή ημερομηνίας: dd/mm/yyyy).",
                )
                return False
            overrides.append(OpenInvoice(code, date, round(amt, 2)))

        wiz.open_invoices = list(wiz.ledger.open_invoices)
        if overrides:
            old_total = round(sum(
                inv.remaining for inv in wiz.open_invoices if inv.label == "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ"
            ), 2)
            new_total = round(sum(inv.remaining for inv in overrides), 2)
            if abs(old_total - new_total) > 0.01:
                reply = QMessageBox.question(
                    self, "Η ανάλυση δεν ταιριάζει",
                    f"Η ανάλυση που έδωσες αθροίζει σε {fmt_amount(new_total)} €, ενώ το "
                    f"ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ της καρτέλας είναι {fmt_amount(old_total)} €. "
                    "Συνέχεια ούτως ή άλλως;",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return False
            others = [inv for inv in wiz.open_invoices if inv.label != "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ"]
            wiz.open_invoices = overrides + others
            dbmod.save_opening_breakdown(
                DB, afm=wiz.ledger.customer_afm,
                rows=[(inv.label, inv.date, inv.remaining) for inv in overrides],
            )

        return True


# ---------------------------------------------------------------- Page 5 --
class DatesPage(QWizardPage):
    """Ημερομηνίες: γραμμικά βήματα ή σκόρπισμα μέσα σε διάστημα -- βλ.
    core/allocation.py::spread_dates_respecting_invoices (κανόνας: ποτέ πριν
    το τιμολόγιο που κλείνει, ποτέ Σαββατοκύριακο)."""

    def __init__(self):
        super().__init__()
        self.setTitle("Ημερομηνίες & αρίθμηση")

        self.topos = QLineEdit("ΘΕΣΣΑΛΟΝΙΚΗ")
        self.seira = QLineEdit("")
        self.receipt_no = QSpinBox()
        self.receipt_no.setRange(1, 999999)

        self.linear_radio = QRadioButton("Διαδοχικές (+Ν ημέρες μεταξύ τους)")
        self.spread_radio = QRadioButton("Σκορπισμένες μέσα σε διάστημα")
        self.linear_radio.setChecked(True)
        dg = QButtonGroup(self)
        dg.addButton(self.linear_radio)
        dg.addButton(self.spread_radio)

        self.gap_days = QSpinBox()
        self.gap_days.setRange(0, 60)
        self.gap_days.setValue(2)

        self.start_date = QDateEdit(calendarPopup=True)
        self.start_date.setDisplayFormat(DATE_FMT_QT)
        self.start_date.setDate(QDate.currentDate())
        self.end_date = QDateEdit(calendarPopup=True)
        self.end_date.setDisplayFormat(DATE_FMT_QT)
        self.end_date.setDate(QDate.currentDate())

        form = QFormLayout()
        form.addRow("Τόπος:", self.topos)
        form.addRow("Σειρά:", self.seira)
        form.addRow("Αριθμός πρώτης απόδειξης:", self.receipt_no)

        mode_box = QVBoxLayout()
        mode_box.addWidget(self.linear_radio)
        lin_form = QFormLayout()
        lin_form.addRow("Ημέρες μεταξύ αποδείξεων:", self.gap_days)
        lin_form.addRow("Ημερομηνία πρώτης:", self.start_date)
        mode_box.addLayout(lin_form)
        mode_box.addWidget(self.spread_radio)
        spread_form = QFormLayout()
        spread_form.addRow("Από:", self.start_date)
        spread_form.addRow("Έως:", self.end_date)
        mode_box.addLayout(spread_form)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(mode_box)
        self.setLayout(layout)

    def initializePage(self):
        wiz = self.wizard()
        ledger = wiz.ledger
        suggested = dbmod.suggest_next_receipt_no(
            DB, company_id=wiz.company_id, seira=self.seira.text(),
            receipt_prefix=wiz.receipt_prefix,
        )
        self.receipt_no.setValue(suggested if suggested is not None else ledger.next_receipt_no)

    def validatePage(self) -> bool:
        wiz = self.wizard()
        wiz.topos = self.topos.text().strip() or "-"
        wiz.seira = self.seira.text()
        wiz.receipt_no_start = self.receipt_no.value()
        wiz.date_mode = "spread" if self.spread_radio.isChecked() else "linear"
        wiz.gap_days = self.gap_days.value()
        wiz.start_date = _qdate_to_dt(self.start_date.date())
        wiz.end_date = _qdate_to_dt(self.end_date.date())

        if wiz.date_mode == "spread" and wiz.end_date < wiz.start_date:
            QMessageBox.warning(self, "Λάθος διάστημα", "Η ημερομηνία 'Έως' είναι πριν την 'Από'.")
            return False
        return True


# ---------------------------------------------------------------- Page 6 --
class PreviewPage(QWizardPage):
    """Προεπισκόπηση της κατανομής -- οι νομικές προειδοποιήσεις είναι εδώ
    μπλοκάρον banner με ρητή επιβεβαίωση, όχι print() σε κονσόλα."""

    def __init__(self):
        super().__init__()
        self.setTitle("Προεπισκόπηση αποδείξεων")

        self.warnings_label = QLabel("")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setStyleSheet(f"color: {STATUS_DANGER_COLOR}; font-weight: bold;")
        self.ack_checkbox = QCheckBox("Το κατάλαβα, συνέχισε ούτως ή άλλως")
        self.ack_checkbox.toggled.connect(self.completeChanged)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Ημερομηνία", "Αρ. Απόδειξης", "Ποσό", "Αιτιολογία"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.total_label = QLabel("")

        layout = QVBoxLayout()
        layout.addWidget(self.warnings_label)
        layout.addWidget(self.ack_checkbox)
        layout.addWidget(self.table)
        layout.addWidget(self.total_label)
        self.setLayout(layout)

        self._has_warnings = False

    def initializePage(self):
        wiz = self.wizard()
        plan = allocate_receipts(wiz.open_invoices, wiz.amount, wiz.cap, wiz.round_step)
        if wiz.date_mode == "spread":
            dates = spread_dates_respecting_invoices(plan, wiz.start_date, wiz.end_date)
        else:
            dates = linear_dates(wiz.start_date, wiz.gap_days, len(plan))

        wiz.plan = plan
        wiz.dates = dates

        warnings = []
        if wiz.amount > wiz.ledger.total_open + 0.01:
            warnings.append(
                f"Το ποσό εξόφλησης ({fmt_amount(wiz.amount)} €) υπερβαίνει το ανοιχτό "
                f"υπόλοιπο της καρτέλας ({fmt_amount(wiz.ledger.total_open)} €). Η διαφορά "
                "θα καταγραφεί ως \"έναντι μελλοντικών αγορών\"."
            )
        if wiz.date_mode == "linear" and wiz.gap_days == 0 and wiz.amount > wiz.cap:
            warnings.append(
                "ΝΟΜΙΚΗ ΠΡΟΕΙΔΟΠΟΙΗΣΗ: ζήτησες πολλαπλές αποδείξεις με ΙΔΙΑ ημερομηνία. Ο "
                "τεχνητός διαχωρισμός μίας συναλλαγής σε αποδείξεις ≤όριο την ίδια μέρα για "
                "παράκαμψη του ορίου μετρητών μπορεί να θεωρηθεί παράνομος."
            )
        if not wiz.ledger.balance_check_ok:
            warnings.append("Η καρτέλα είχε αποτύχει στον αυτόματο έλεγχο υπολοίπου.")

        self._has_warnings = bool(warnings)
        self.warnings_label.setText("⚠ " + "\n⚠ ".join(warnings) if warnings else "")
        self.ack_checkbox.setVisible(self._has_warnings)
        self.ack_checkbox.setChecked(False)

        self.table.setRowCount(0)
        receipt_no = wiz.receipt_no_start
        for chunk, date_dt in zip(plan, dates):
            date_str = date_dt.strftime("%d/%m/%Y")
            code = f"{wiz.receipt_prefix}{str(receipt_no).zfill(wiz.receipt_padding)}"
            aitiologia = build_aitiologia(chunk["closed"], chunk["partial"])
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(date_str))
            self.table.setItem(row, 1, QTableWidgetItem(code))
            self.table.setItem(row, 2, QTableWidgetItem(f"{fmt_amount(chunk['amount'])} €"))
            self.table.setItem(row, 3, QTableWidgetItem(aitiologia))
            receipt_no += 1

        total = round(sum(c["amount"] for c in plan), 2)
        self.total_label.setText(f"Σύνολο {len(plan)} αποδείξεων, συνολικό ποσό: {fmt_amount(total)} €")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return (not self._has_warnings) or self.ack_checkbox.isChecked()


# ---------------------------------------------------------------- Page 7 --
class GeneratePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Παραγωγή PDF")

        self.out_dir_label = QLabel("(δεν έχει επιλεγεί φάκελος)")
        self.out_dir_label.setWordWrap(True)
        browse_btn = QPushButton("Επιλογή φακέλου εξόδου...")
        browse_btn.clicked.connect(self._browse)

        self.generate_btn = QPushButton("Δημιουργία αποδείξεων")
        self.generate_btn.clicked.connect(self._generate)
        self.generate_btn.setEnabled(False)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)

        self.open_folder_btn = QPushButton("Άνοιγμα φακέλου")
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.open_folder_btn.setVisible(False)

        self.pdf_list = QListWidget()
        self.pdf_list.setMaximumWidth(220)
        self.pdf_list.currentRowChanged.connect(self._show_preview)

        self.pdf_doc = QPdfDocument(self)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_doc)
        self.pdf_view.setPageMode(QPdfView.PageMode.SinglePage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        self.print_btn = QPushButton("Εκτύπωση επιλεγμένου")
        self.print_btn.clicked.connect(self._print_selected)

        pdf_col = QVBoxLayout()
        pdf_col.addWidget(self.pdf_list)
        pdf_col.addWidget(self.print_btn)

        self.preview_box = QGroupBox("Προεπισκόπηση")
        preview_layout = QHBoxLayout()
        preview_layout.addLayout(pdf_col)
        preview_layout.addWidget(self.pdf_view, 1)
        self.preview_box.setLayout(preview_layout)
        self.preview_box.setVisible(False)

        self.email_to = QLineEdit()
        self.email_to.setPlaceholderText("email παραλήπτη")
        email_btn = QPushButton("Αποστολή με email (όλα τα PDF)")
        email_btn.clicked.connect(self._send_email)
        email_row = QHBoxLayout()
        email_row.addWidget(self.email_to)
        email_row.addWidget(email_btn)
        self.email_box = QGroupBox("Αποστολή email")
        self.email_box.setLayout(email_row)
        self.email_box.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(browse_btn)
        layout.addWidget(self.out_dir_label)
        layout.addWidget(self.generate_btn)
        layout.addWidget(self.result_label)
        layout.addWidget(self.open_folder_btn)
        layout.addWidget(self.preview_box, 1)
        layout.addWidget(self.email_box)
        self.setLayout(layout)

        self._out_dir = None
        self._done = False
        self._generated_paths: list[str] = []

    def _print_selected(self):
        row = self.pdf_list.currentRow()
        if row < 0 or row >= len(self._generated_paths):
            QMessageBox.warning(self, "Δεν επιλέχθηκε PDF", "Επίλεξε πρώτα ένα PDF από τη λίστα.")
            return
        path = self._generated_paths[row]
        if sys.platform == "win32":
            os.startfile(path, "print")  # noqa: S606
        else:
            QMessageBox.warning(self, "Μη υποστηριζόμενο", "Η εκτύπωση υποστηρίζεται μόνο σε Windows.")

    def _send_email(self):
        wiz = self.wizard()
        to_addr = self.email_to.text().strip()
        if not self._generated_paths:
            return
        result = email_sender.send_receipt_email(
            smtp_host=wiz.smtp_host, smtp_port=wiz.smtp_port,
            smtp_email=wiz.smtp_email, smtp_password=wiz.smtp_password,
            to_addr=to_addr,
            subject=f"Αποδείξεις -- {wiz.ledger.customer_name}",
            body=f"Επισυνάπτονται {len(self._generated_paths)} απόδειξη/εις για {wiz.ledger.customer_name}.",
            attachments=self._generated_paths,
        )
        if result.get("ok"):
            QMessageBox.information(self, "Στάλθηκε", f"Το email στάλθηκε στο {to_addr}.")
        else:
            QMessageBox.critical(self, "Σφάλμα αποστολής", result.get("error", "Άγνωστο σφάλμα"))

    def _show_preview(self, row: int):
        if row < 0 or row >= len(self._generated_paths):
            return
        self.pdf_doc.load(self._generated_paths[row])

    def initializePage(self):
        wiz = self.wizard()
        if wiz.output_dir:
            self._out_dir = wiz.output_dir
            self.out_dir_label.setText(self._out_dir)
            self.generate_btn.setEnabled(True)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Φάκελος εξόδου")
        if not path:
            return
        self._out_dir = path
        self.out_dir_label.setText(path)
        self.generate_btn.setEnabled(True)

    def _generate(self):
        wiz = self.wizard()
        os.makedirs(self._out_dir, exist_ok=True)
        csv_rows = []
        self._generated_paths = []
        self.pdf_list.clear()
        receipt_no = wiz.receipt_no_start
        for chunk, date_dt in zip(wiz.plan, wiz.dates):
            date_str = date_dt.strftime("%d/%m/%Y")
            code = f"{wiz.receipt_prefix}{str(receipt_no).zfill(wiz.receipt_padding)}"
            aitiologia = build_aitiologia(chunk["closed"], chunk["partial"])
            rdata = ReceiptData(
                topos=wiz.topos,
                date=date_str,
                seira=wiz.seira,
                no=str(receipt_no),
                amount=chunk["amount"],
                customer_name=wiz.ledger.customer_name,
                occupation=wiz.occupation,
                address=wiz.address,
                aitiologia=aitiologia,
                is_payment=wiz.is_payment,
                company_name=wiz.company_name,
                company_subtitle=wiz.company_subtitle,
                company_address_line=wiz.company_address,
                company_ids_line=wiz.company_ids,
                company_email=wiz.company_email,
                signature_path=wiz.signature_path,
            )
            out_name = f"{date_dt.strftime('%Y_%m_%d')} {str(receipt_no).zfill(6)}.pdf"
            out_path = os.path.join(self._out_dir, out_name)
            build_receipt_pdf(out_path, rdata)
            csv_rows.append([date_str, code, f"{chunk['amount']:.2f}", aitiologia, out_name])
            self._generated_paths.append(out_path)
            self.pdf_list.addItem(out_name)
            receipt_no += 1

        csv_path = os.path.join(self._out_dir, "summary.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Ημερομηνία", "Αρ. Απόδειξης", "Ποσό", "Αιτιολογία", "Αρχείο"])
            w.writerows(csv_rows)

        dbmod.record_receipt_run(
            DB, company_id=wiz.company_id, contact_afm=wiz.ledger.customer_afm,
            seira=wiz.seira, receipt_prefix=wiz.receipt_prefix,
            receipt_padding=wiz.receipt_padding,
            direction="payment" if wiz.is_payment else "collection",
            amount=wiz.amount, receipt_no_start=wiz.receipt_no_start,
            receipt_no_end=receipt_no - 1, out_dir=self._out_dir,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        wiz.output_dir = self._out_dir
        wiz.save_company()

        self._done = True
        self.result_label.setText(
            f"Δημιουργήθηκαν {len(wiz.plan)} PDF + summary.csv στο:\n{self._out_dir}"
        )
        self.open_folder_btn.setVisible(True)
        self.preview_box.setVisible(True)
        self.email_box.setVisible(True)
        self.pdf_list.setCurrentRow(0)
        self.completeChanged.emit()

    def _open_folder(self):
        if self._out_dir and sys.platform == "win32":
            os.startfile(self._out_dir)  # noqa: S606

    def isComplete(self) -> bool:
        return self._done


class ReceiptWizard(QWizard):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Αiποδείξεις")
        self.resize(900, 650)
        self.setWizardStyle(QWizard.ModernStyle)

        self.ledger = None
        self.ledger_path = None
        self.excluded_codes = set()
        self.open_invoices = []
        self.plan = []
        self.dates = []
        self.company_id = None
        self.default_cap = None
        self.default_round_step = None
        self.signature_path = None
        self.output_dir = None
        self.smtp_host = None
        self.smtp_port = None
        self.smtp_email = None
        self.smtp_password = None

        self.setPage(PAGE_COMPANY, CompanyPage())
        self.setPage(PAGE_LEDGER, LedgerPage())
        self.setPage(PAGE_CONTACT, ContactPage())
        self.setPage(PAGE_AMOUNT, AmountPage())
        self.setPage(PAGE_DATES, DatesPage())
        self.setPage(PAGE_PREVIEW, PreviewPage())
        self.setPage(PAGE_GENERATE, GeneratePage())
        self.setStartId(PAGE_COMPANY)

    def save_company(self):
        """Upsert στο companies row βάσει της τρέχουσας κατάστασης του
        wizard -- καλείται από κάθε βήμα που ενημερώνει κάτι στην εταιρεία
        (όνομα/στοιχεία, όριο/round-step, φάκελος εξόδου) ώστε καμία στήλη
        να μη σβήνεται κατά λάθος από ένα μερικό upsert."""
        self.company_id = dbmod.upsert_company(
            DB, company_id=self.company_id, name=self.company_name,
            subtitle=self.company_subtitle, address_line=self.company_address,
            ids_line=self.company_ids, email=self.company_email,
            receipt_prefix=self.receipt_prefix, receipt_padding=self.receipt_padding,
            default_cap=self.default_cap, default_round_step=self.default_round_step,
            signature_path=self.signature_path, output_dir=self.output_dir,
            smtp_host=self.smtp_host, smtp_port=self.smtp_port,
            smtp_email=self.smtp_email, smtp_password=self.smtp_password,
        )
        return self.company_id


class HistoryDialog(QDialog):
    """Δευτερεύουσα οθόνη -- λίστα προηγούμενων εκτελέσεων (receipt_runs),
    με άνοιγμα του φακέλου παραγωγής και αντιγραφή των κωδικών της
    επιλεγμένης εκτέλεσης (για επικόλληση στο πεδίο εξαίρεσης του βήματος
    "Κάρτελα", σε σενάριο επανέκδοσης)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ιστορικό αποδείξεων")
        self.resize(860, 420)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "Ημερομηνία", "Εταιρεία", "Πελάτης/Προμηθευτής", "Κατεύθυνση",
            "Ποσό", "Αποδείξεις", "Φάκελος",
        ])
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        open_btn = QPushButton("Άνοιγμα φακέλου")
        open_btn.clicked.connect(self._open_folder)
        copy_btn = QPushButton("Αντιγραφή κωδικών (για εξαίρεση σε επανέκδοση)")
        copy_btn.clicked.connect(self._copy_codes)

        btn_row = QHBoxLayout()
        btn_row.addWidget(open_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self.table)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        self._rows = []
        self._reload()

    def _reload(self):
        self._rows = dbmod.list_receipt_runs(DB)
        self.table.setRowCount(0)
        for r in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            direction = "Πληρωμή" if r["direction"] == "payment" else "Είσπραξη"
            no_range = (
                f"{r['receipt_prefix']}{str(r['receipt_no_start']).zfill(r['receipt_padding'])}"
                f"..{r['receipt_prefix']}{str(r['receipt_no_end']).zfill(r['receipt_padding'])}"
            )
            self.table.setItem(row, 0, QTableWidgetItem((r["created_at"] or "")[:10]))
            self.table.setItem(row, 1, QTableWidgetItem(r["company_name"] or ""))
            self.table.setItem(row, 2, QTableWidgetItem(r["contact_name"] or r["contact_afm"] or ""))
            self.table.setItem(row, 3, QTableWidgetItem(direction))
            self.table.setItem(row, 4, QTableWidgetItem(f"{r['amount']:.2f} €"))
            self.table.setItem(row, 5, QTableWidgetItem(no_range))
            self.table.setItem(row, 6, QTableWidgetItem(r["out_dir"] or ""))

    def _selected_row(self):
        idx = self.table.currentRow()
        if idx < 0 or idx >= len(self._rows):
            return None
        return self._rows[idx]

    def _open_folder(self):
        r = self._selected_row()
        if not r:
            return
        if r["out_dir"] and sys.platform == "win32" and os.path.isdir(r["out_dir"]):
            os.startfile(r["out_dir"])  # noqa: S606
        else:
            QMessageBox.warning(self, "Ο φάκελος δεν βρέθηκε", f"Δεν υπάρχει πλέον:\n{r['out_dir']}")

    def _copy_codes(self):
        r = self._selected_row()
        if not r:
            return
        codes = [
            f"{r['receipt_prefix']}{str(n).zfill(r['receipt_padding'])}"
            for n in range(r["receipt_no_start"], r["receipt_no_end"] + 1)
        ]
        QApplication.clipboard().setText(", ".join(codes))


class SyncSettingsDialog(QDialog):
    """Ρύθμιση του rclone remote (π.χ. mega:AIReceipts) που χρησιμοποιείται
    για sync μεταξύ σταθμών εργασίας -- βλ. core/sync.py. Χρειάζεται ήδη
    ρυθμισμένο rclone remote στο σύστημα (`rclone config`)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ρυθμίσεις συγχρονισμού")
        self.resize(440, 160)

        self.remote_edit = QLineEdit(syncmod.get_remote_path())
        self.is_main_checkbox = QCheckBox("Αυτός είναι ο \"main\" υπολογιστής")
        self.is_main_checkbox.setChecked(syncmod.is_main_machine())
        save_btn = QPushButton("Αποθήκευση")
        save_btn.clicked.connect(self._save)

        form = QFormLayout()
        form.addRow("Remote (rclone):", self.remote_edit)
        form.addRow("", self.is_main_checkbox)

        info = QLabel(
            "π.χ. mega:AIReceipts ή gdrive:AIReceipts -- πρέπει να υπάρχει ήδη "
            "ρυθμισμένο rclone remote σε αυτό το μηχάνημα (`rclone config` σε "
            "τερματικό). Εδώ ορίζεται μόνο ο υποφάκελος που θα χρησιμοποιήσει "
            "το AIReceipts.\n\nΟ \"main\" υπολογιστής είναι το ΕΝΑ συγκεκριμένο "
            "μηχάνημα (π.χ. του γραφείου) στο οποίο καταλήγουν τοπικά, στο "
            "άνοιγμα της εφαρμογής, τα PDF ΟΛΩΝ των σταθμών ανά εταιρεία -- "
            "ώστε να υπάρχει μία πραγματική θέση με όλα τα PDF, όχι μόνο στο "
            "cloud. Μόνο ένας σταθμός θα πρέπει να έχει αυτό το κουτί "
            "τσεκαρισμένο."
        )
        info.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(save_btn)
        self.setLayout(layout)

    def _save(self):
        syncmod.save_remote_path(self.remote_edit.text().strip())
        syncmod.set_main_machine(self.is_main_checkbox.isChecked())
        self.accept()


class LauncherWindow(QWidget):
    """Πρώτο παράθυρο -- sync στο άνοιγμα (pull+merge manifests, heartbeat),
    presence badge (ποιος άλλος είναι ενεργός τώρα -- προειδοποίηση, όχι
    lock, βλ. core/sync.py), και επιλογή μεταξύ νέας απόδειξης/ιστορικού/
    ρυθμίσεων συγχρονισμού."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Αiποδείξεις")
        self.resize(360, 260)

        self.presence_label = QLabel("")
        self.presence_label.setAlignment(Qt.AlignCenter)
        self.presence_label.setWordWrap(True)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #6b7a99;")

        new_btn = QPushButton("Νέα απόδειξη...")
        new_btn.clicked.connect(self._new_receipt)
        history_btn = QPushButton("Ιστορικό...")
        history_btn.clicked.connect(self._show_history)
        sync_btn = QPushButton("Συγχρονισμός τώρα")
        sync_btn.clicked.connect(self._run_startup_sync)
        settings_btn = QPushButton("Ρυθμίσεις συγχρονισμού...")
        settings_btn.clicked.connect(self._show_sync_settings)

        layout = QVBoxLayout()
        layout.addWidget(self.presence_label)
        layout.addWidget(new_btn)
        layout.addWidget(history_btn)
        layout.addWidget(sync_btn)
        layout.addWidget(settings_btn)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

        self._wizard = None
        self._history = None
        self._sync_dialog = None

        self._run_startup_sync()

    def _run_startup_sync(self):
        self.status_label.setText("Συγχρονισμός...")
        QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = syncmod.sync_startup(DB)
            presence.send_heartbeat()
        finally:
            QApplication.restoreOverrideCursor()

        if result.get("skipped"):
            self.status_label.setText("")
        elif result.get("ok"):
            msg = (
                f"Sync OK -- {result.get('machines', 0)} μηχανήματα, "
                f"{result.get('receipt_runs', 0)} νέες αποδείξεις, "
                f"{result.get('companies', 0)} εταιρείες ενημερώθηκαν."
            )
            pulled = result.get("pdfs_pulled")
            if pulled is not None:
                msg += f" PDF ενημερώθηκαν για {len(pulled)} εταιρείες (main υπολογιστής)."
            self.status_label.setText(msg)
        elif result.get("no_internet"):
            self.status_label.setText("Χωρίς σύνδεση -- εργασία με τοπικά δεδομένα.")
        else:
            self.status_label.setText(f"Σφάλμα συγχρονισμού: {result.get('error', '')}")
        self._refresh_presence()

    def _refresh_presence(self):
        others = presence.list_presence()
        if others:
            names = ", ".join(f"{o['user']}@{o['computer']}" for o in others)
            self.presence_label.setText(f"⚠ Ενεργός τώρα και: {names}")
            self.presence_label.setStyleSheet(
                f"background: {STATUS_DANGER_BG}; border: 1px solid {STATUS_DANGER_BORDER}; "
                f"color: {STATUS_DANGER_COLOR}; font-weight: bold; padding: 6px; border-radius: 6px;"
            )
        else:
            self.presence_label.setText("✓ Κανείς άλλος ενεργός αυτή τη στιγμή")
            self.presence_label.setStyleSheet(
                f"background: {STATUS_OK_BG}; border: 1px solid {STATUS_OK_BORDER}; "
                f"color: {STATUS_OK_COLOR}; font-weight: bold; padding: 6px; border-radius: 6px;"
            )

    def _new_receipt(self):
        self._wizard = ReceiptWizard()
        self._wizard.show()

    def _show_history(self):
        self._history = HistoryDialog(self)
        self._history.show()

    def _show_sync_settings(self):
        self._sync_dialog = SyncSettingsDialog(self)
        self._sync_dialog.exec()


def _sync_on_quit():
    try:
        syncmod.sync_shutdown(DB)
    except Exception:  # noqa: BLE001 -- η εφαρμογή κλείνει ούτως ή άλλως,
        pass            # ένα αποτυχημένο sync δεν πρέπει να την μπλοκάρει


def _assets_dir() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller: datas bundled στο _MEIPASS (ή δίπλα στο .exe σε onedir) --
        # βλ. AIReceipts.spec. Διαφορετικό κανόνα από core.db.DB_PATH επίτηδες:
        # τα fonts είναι read-only bundled δεδομένα, όχι εγγράψιμη κατάσταση.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def _load_app_font(app: QApplication) -> None:
    """Ίδια γραμματοσειρά (IBM Plex Sans) με τα αδερφά projects
    (expvault/lab-galatista) -- βλ. lab-galatista/fonts/. Αν τα αρχεία
    λείπουν για οποιονδήποτε λόγο, το Qt απλά πέφτει στο default του
    συστήματος -- δεν μπλοκάρει την εκκίνηση."""
    font_dir = _assets_dir() / "fonts"
    for fname in ("IBMPlexSans-Regular.ttf", "IBMPlexSans-Bold.ttf"):
        path = font_dir / fname
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("IBM Plex Sans", 10))


def main():
    app = QApplication(sys.argv)
    _load_app_font(app)
    win = LauncherWindow()
    win.show()
    app.aboutToQuit.connect(_sync_on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
