# -*- coding: utf-8 -*-
"""Τοπικό SQLite persistence (Φάση 2) -- βλ. DESKTOP_APP_PLAN.md §3.
Θυμάται εταιρείες-εκδότες, επαφές (ΑΦΜ -> επάγγελμα/διεύθυνση) και ιστορικό
εκδόσεων αποδείξεων ανά εταιρεία/σειρά, ώστε να προτείνεται ο επόμενος
αριθμός αντί να ζητείται κάθε φορά. Καμία σύνδεση με server/cloud -- ένα
αρχείο `app_data.db` δίπλα στο project."""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller: __file__ θα έδειχνε στον προσωρινό φάκελο εξαγωγής
    # (onefile) που διαγράφεται μετά το κλείσιμο -- η βάση πρέπει να μένει
    # δίπλα στο πραγματικό .exe.
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = _BASE_DIR / "app_data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    subtitle TEXT,
    address_line TEXT,
    ids_line TEXT,
    email TEXT,
    receipt_prefix TEXT,
    receipt_padding INTEGER,
    default_cap REAL,
    default_round_step REAL,
    signature_path TEXT,
    output_dir TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    afm TEXT PRIMARY KEY,
    name TEXT,
    occupation TEXT,
    address TEXT
);

CREATE TABLE IF NOT EXISTS opening_breakdowns (
    contact_afm TEXT NOT NULL,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (contact_afm, seq)
);

CREATE TABLE IF NOT EXISTS receipt_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    contact_afm TEXT,
    seira TEXT NOT NULL DEFAULT '',
    receipt_prefix TEXT,
    receipt_padding INTEGER,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    receipt_no_start INTEGER NOT NULL,
    receipt_no_end INTEGER NOT NULL,
    out_dir TEXT,
    created_at TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Προσθέτει στήλες σε ήδη υπάρχον app_data.db από παλιότερη έκδοση του
    schema -- το CREATE TABLE IF NOT EXISTS παραπάνω δεν αγγίζει πίνακες που
    ήδη υπάρχουν."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
    if "signature_path" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN signature_path TEXT")
    if "output_dir" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN output_dir TEXT")
    conn.commit()


def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def list_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies ORDER BY name").fetchall()


def upsert_company(
    conn: sqlite3.Connection,
    *,
    company_id: int | None,
    name: str,
    subtitle: str,
    address_line: str,
    ids_line: str,
    email: str,
    receipt_prefix: str,
    receipt_padding: int,
    default_cap: float,
    default_round_step: float,
    signature_path: str | None = None,
    output_dir: str | None = None,
) -> int:
    """Insert ή update -- αν δοθεί company_id ενημερώνει εκείνη τη γραμμή,
    αλλιώς κάνει upsert στο μοναδικό name (νέα εταιρεία ή ίδιο όνομα με
    πριν)."""
    if company_id is not None:
        conn.execute(
            """UPDATE companies SET name=?, subtitle=?, address_line=?, ids_line=?,
               email=?, receipt_prefix=?, receipt_padding=?, default_cap=?,
               default_round_step=?, signature_path=?, output_dir=? WHERE id=?""",
            (name, subtitle, address_line, ids_line, email, receipt_prefix,
             receipt_padding, default_cap, default_round_step, signature_path,
             output_dir, company_id),
        )
        conn.commit()
        return company_id

    row = conn.execute(
        """INSERT INTO companies
               (name, subtitle, address_line, ids_line, email, receipt_prefix,
                receipt_padding, default_cap, default_round_step, signature_path,
                output_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
               subtitle=excluded.subtitle, address_line=excluded.address_line,
               ids_line=excluded.ids_line, email=excluded.email,
               receipt_prefix=excluded.receipt_prefix,
               receipt_padding=excluded.receipt_padding,
               default_cap=excluded.default_cap,
               default_round_step=excluded.default_round_step,
               signature_path=excluded.signature_path,
               output_dir=excluded.output_dir
           RETURNING id""",
        (name, subtitle, address_line, ids_line, email, receipt_prefix,
         receipt_padding, default_cap, default_round_step, signature_path, output_dir),
    ).fetchone()
    conn.commit()
    return row["id"]


def get_contact(conn: sqlite3.Connection, afm: str) -> sqlite3.Row | None:
    if not afm:
        return None
    return conn.execute("SELECT * FROM contacts WHERE afm=?", (afm,)).fetchone()


def upsert_contact(conn: sqlite3.Connection, *, afm: str, name: str, occupation: str, address: str) -> None:
    if not afm or afm == "-":
        return
    conn.execute(
        """INSERT INTO contacts (afm, name, occupation, address) VALUES (?, ?, ?, ?)
           ON CONFLICT(afm) DO UPDATE SET
               name=excluded.name, occupation=excluded.occupation, address=excluded.address""",
        (afm, name, occupation, address),
    )
    conn.commit()


def list_receipt_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Ιστορικό εκδόσεων, πιο πρόσφατο πρώτα, με το όνομα εταιρείας/επαφής
    ήδη joined -- για την οθόνη Ιστορικού."""
    return conn.execute(
        """SELECT rr.*, c.name AS company_name, ct.name AS contact_name
           FROM receipt_runs rr
           JOIN companies c ON c.id = rr.company_id
           LEFT JOIN contacts ct ON ct.afm = rr.contact_afm
           ORDER BY rr.id DESC"""
    ).fetchall()


def get_opening_breakdown(conn: sqlite3.Connection, afm: str) -> list[sqlite3.Row]:
    if not afm:
        return []
    return conn.execute(
        "SELECT * FROM opening_breakdowns WHERE contact_afm=? ORDER BY seq", (afm,),
    ).fetchall()


def save_opening_breakdown(
    conn: sqlite3.Connection, *, afm: str, rows: list[tuple[str, str, float]],
) -> None:
    """Αντικαθιστά ολόκληρη την αποθηκευμένη ανάλυση αρχικού υπολοίπου του
    ΑΦΜ με τη νέα -- δεν καλείται όταν ο πίνακας στο GUI είναι κενός (βλ.
    AmountPage.validatePage), ώστε μια κενή σελίδα να μη σβήνει κατά λάθος
    προηγούμενη αποθηκευμένη ανάλυση."""
    if not afm or afm == "-":
        return
    conn.execute("DELETE FROM opening_breakdowns WHERE contact_afm=?", (afm,))
    conn.executemany(
        "INSERT INTO opening_breakdowns (contact_afm, seq, code, date, amount) VALUES (?, ?, ?, ?, ?)",
        [(afm, i, code, date, amount) for i, (code, date, amount) in enumerate(rows)],
    )
    conn.commit()


def suggest_next_receipt_no(
    conn: sqlite3.Connection, *, company_id: int | None, seira: str, receipt_prefix: str,
) -> int | None:
    if company_id is None:
        return None
    row = conn.execute(
        """SELECT receipt_no_end FROM receipt_runs
           WHERE company_id=? AND seira=? AND receipt_prefix=?
           ORDER BY id DESC LIMIT 1""",
        (company_id, seira, receipt_prefix),
    ).fetchone()
    return (row["receipt_no_end"] + 1) if row else None


def record_receipt_run(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    contact_afm: str,
    seira: str,
    receipt_prefix: str,
    receipt_padding: int,
    direction: str,
    amount: float,
    receipt_no_start: int,
    receipt_no_end: int,
    out_dir: str,
    created_at: str,
) -> None:
    conn.execute(
        """INSERT INTO receipt_runs
               (company_id, contact_afm, seira, receipt_prefix, receipt_padding,
                direction, amount, receipt_no_start, receipt_no_end, out_dir, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (company_id, contact_afm, seira, receipt_prefix, receipt_padding,
         direction, amount, receipt_no_start, receipt_no_end, out_dir, created_at),
    )
    conn.commit()
