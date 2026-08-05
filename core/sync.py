# -*- coding: utf-8 -*-
"""Πολυ-σταθμική χρήση μέσω rclone -- ίδιο σχήμα με backend/backup.py +
modules/cloud-sync.js του expvault/lab-galatista, προσαρμοσμένο στο ότι το
AIReceipts κρατάει τη δική του τοπική SQLite βάση σε κάθε σταθμό εργασίας
(όχι ζωντανό κοινό αρχείο -- θα κόλλαγε).

Τρία πράγματα συγχρονίζονται μέσω rclone, καθένα με διαφορετική λογική:

1. **manifests/<hostname>.json** -- πλήρης εξαγωγή companies/contacts/
   receipt_runs/opening_breakdowns από ΤΗ ΔΙΚΗ ΜΑΣ βάση. Κάθε μηχάνημα
   γράφει μόνο το δικό του αρχείο (rclone copyto, ποτέ delete αλλού).
   Στο άνοιγμα της εφαρμογής κατεβάζουμε ΟΛΑ τα manifests (δικό μας +
   άλλων) και τα κάνουμε merge στην τοπική βάση -- εταιρείες/επαφές με
   last-write-wins (updated_at), receipt_runs με insert-if-missing βάσει
   uuid (ώστε το ίδιο αρχείο να μπορεί να ξαναδιαβαστεί χωρίς διπλότυπα).
   Αυτό είναι που κάνει την πρόταση επόμενου αριθμού απόδειξης
   (core/db.py::suggest_next_receipt_no) να βλέπει ό,τι έχουν εκδώσει και
   άλλα μηχανήματα -- ΟΧΙ ζωντανά, μόνο μετά το επόμενο sync.
2. **backup/<hostname>/app_data.db** -- στιγμιότυπο της τοπικής βάσης,
   μόνο για emergency restore, δεν διαβάζεται αυτόματα από πουθενά.
3. **pdf/<company>/** -- one-way αντίγραφο (rclone copy, additive) του
   output_dir κάθε εταιρείας προς το remote, όπως ζητήθηκε ρητά ("τα pdf
   one way sync στο main").

ΔΕΝ υπάρχει real-time δέσμευση αριθμού/lock μεταξύ σταθμών -- το rclone
remote δεν υποστηρίζει atomic locking αξιόπιστα, και θα πρόσθετε
πολυπλοκότητα χωρίς πραγματική εγγύηση. Αντ' αυτού: sync στο άνοιγμα ΚΑΙ
στο κλείσιμο της εφαρμογής (ελαχιστοποιεί το παράθυρο ασυμφωνίας) + το
heartbeat/presence (core/presence.py) σαν ορατή προειδοποίηση όταν κάποιος
άλλος έχει ανοιχτή την εφαρμογή αυτή τη στιγμή. Παραμένει ένα θεωρητικό
περιθώριο σύγκρουσης αριθμού αν δύο σταθμοί δουλέψουν *ταυτόχρονα* στην
ΙΔΙΑ εταιρεία χωρίς να κλείσουν/ανοίξουν ενδιάμεσα -- τεκμηριωμένος
γνωστός περιορισμός, όχι κρυμμένος."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path

from core import db as dbmod

RCLONE_BIN = os.environ.get("AIRECEIPTS_RCLONE_PATH") or "rclone"
DEFAULT_REMOTE = "mega:AIReceipts"


def _config_path() -> Path:
    return dbmod.DB_PATH.parent / "sync_config.json"


def get_remote_path() -> str:
    path = _config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("remote_path") or DEFAULT_REMOTE
        except (OSError, ValueError):
            return DEFAULT_REMOTE
    return DEFAULT_REMOTE


def save_remote_path(remote_path: str) -> None:
    _config_path().write_text(json.dumps({"remote_path": remote_path}), encoding="utf-8")


def sanitize(name: str) -> str:
    """Ασφαλές όνομα αρχείου/φακέλου -- ΔΕΝ κάνει strip σε non-ASCII, μόνο
    στους πραγματικά προβληματικούς χαρακτήρες για Windows/cloud paths.
    Τα ονόματα εταιρειών εδώ είναι πάντα ελληνικά -- ένα ASCII-only allowlist
    (όπως στο αρχικό expvault/lab-galatista pattern, που χρησιμοποιείται
    μόνο για hostname/username) θα κατέληγε όλα σε πανομοιότυπα "_______",
    ανακατεύοντας τα PDF διαφορετικών εταιρειών στον ίδιο remote φάκελο."""
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "machine"


def _hostname() -> str:
    return sanitize(socket.gethostname())


def is_network_error(error: str | None) -> bool:
    return bool(re.search(r"network|connect|timeout|unreachable|no route", error or "", re.I))


def run_rclone(args: list[str], timeout: int = 30) -> dict:
    try:
        proc = subprocess.run(
            [RCLONE_BIN, *args], capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Το rclone δεν βρέθηκε στο PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout ({timeout}s)"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()}
    return {"ok": True, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


# ------------------------------------------------------------ manifest --
# Στήλες companies που ΔΕΝ ανεβαίνουν ποτέ στο manifest -- τοπικές
# διαδρομές αρχείων ή διαπιστευτήρια, χωρίς νόημα (signature_path/
# output_dir) ή επικίνδυνο (smtp_password σε plaintext στο cloud) να
# φύγουν από το μηχάνημα.
_COMPANY_LOCAL_ONLY_COLS = (
    "signature_path", "output_dir", "smtp_host", "smtp_port", "smtp_email", "smtp_password",
)


def export_manifest(conn) -> dict:
    receipt_runs = conn.execute(
        """SELECT rr.*, c.name AS company_name FROM receipt_runs rr
           JOIN companies c ON c.id = rr.company_id"""
    ).fetchall()
    companies = []
    for r in conn.execute("SELECT * FROM companies"):
        d = dict(r)
        for col in _COMPANY_LOCAL_ONLY_COLS:
            d.pop(col, None)
        companies.append(d)
    return {
        "companies": companies,
        "contacts": [dict(r) for r in conn.execute("SELECT * FROM contacts")],
        "opening_breakdowns": [dict(r) for r in conn.execute("SELECT * FROM opening_breakdowns")],
        "receipt_runs": [dict(r) for r in receipt_runs],
    }


def _merge_company(conn, c: dict) -> bool:
    name = c.get("name")
    if not name:
        return False
    remote_updated = c.get("updated_at") or ""
    local = conn.execute(
        "SELECT updated_at FROM companies WHERE name=?", (name,),
    ).fetchone()
    if local:
        if (local["updated_at"] or "") >= remote_updated:
            return False
        # ΔΕΝ αγγίζουμε signature_path/output_dir -- τοπικές διαδρομές
        # αρχείων του κάθε σταθμού, νόημα μόνο τοπικά.
        conn.execute(
            """UPDATE companies SET subtitle=?, address_line=?, ids_line=?, email=?,
                   receipt_prefix=?, receipt_padding=?, default_cap=?, default_round_step=?,
                   updated_at=?
               WHERE name=?""",
            (c.get("subtitle"), c.get("address_line"), c.get("ids_line"), c.get("email"),
             c.get("receipt_prefix"), c.get("receipt_padding"), c.get("default_cap"),
             c.get("default_round_step"), remote_updated, name),
        )
        return True
    conn.execute(
        """INSERT INTO companies (name, subtitle, address_line, ids_line, email,
               receipt_prefix, receipt_padding, default_cap, default_round_step, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, c.get("subtitle"), c.get("address_line"), c.get("ids_line"), c.get("email"),
         c.get("receipt_prefix"), c.get("receipt_padding"), c.get("default_cap"),
         c.get("default_round_step"), remote_updated),
    )
    return True


def _merge_contact(conn, c: dict) -> bool:
    afm = c.get("afm")
    if not afm:
        return False
    remote_updated = c.get("updated_at") or ""
    local = conn.execute("SELECT updated_at FROM contacts WHERE afm=?", (afm,)).fetchone()
    if local and (local["updated_at"] or "") >= remote_updated:
        return False
    conn.execute(
        """INSERT INTO contacts (afm, name, occupation, address, updated_at) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(afm) DO UPDATE SET
               name=excluded.name, occupation=excluded.occupation, address=excluded.address,
               updated_at=excluded.updated_at""",
        (afm, c.get("name"), c.get("occupation"), c.get("address"), remote_updated),
    )
    return True


def _merge_receipt_run(conn, r: dict) -> bool:
    ruuid = r.get("uuid")
    if not ruuid:
        return False
    if conn.execute("SELECT 1 FROM receipt_runs WHERE uuid=?", (ruuid,)).fetchone():
        return False
    company = conn.execute(
        "SELECT id FROM companies WHERE name=?", (r.get("company_name"),),
    ).fetchone()
    if not company:
        return False  # η εταιρεία δεν έχει έρθει ακόμα -- θα ξαναπροσπαθήσει στο επόμενο sync
    conn.execute(
        """INSERT INTO receipt_runs
               (uuid, company_id, contact_afm, seira, receipt_prefix, receipt_padding,
                direction, amount, receipt_no_start, receipt_no_end, out_dir, created_at,
                source_machine)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ruuid, company["id"], r.get("contact_afm"), r.get("seira"), r.get("receipt_prefix"),
         r.get("receipt_padding"), r.get("direction"), r.get("amount"), r.get("receipt_no_start"),
         r.get("receipt_no_end"), r.get("out_dir"), r.get("created_at"),
         r.get("source_machine") or r.get("company_name") and _hostname()),
    )
    return True


def _merge_opening_breakdown(conn, afm: str, rows: list[dict]) -> bool:
    if not afm or not rows:
        return False
    remote_updated = rows[0].get("updated_at") or ""
    local = conn.execute(
        "SELECT MAX(updated_at) AS u FROM opening_breakdowns WHERE contact_afm=?", (afm,),
    ).fetchone()
    if local and (local["u"] or "") >= remote_updated:
        return False
    conn.execute("DELETE FROM opening_breakdowns WHERE contact_afm=?", (afm,))
    conn.executemany(
        """INSERT INTO opening_breakdowns (contact_afm, seq, code, date, amount, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(afm, row["seq"], row["code"], row["date"], row["amount"], remote_updated) for row in rows],
    )
    return True


def import_manifest(conn, manifest: dict) -> dict:
    stats = {"companies": 0, "contacts": 0, "receipt_runs": 0, "opening_breakdowns": 0}
    for c in manifest.get("companies", []):
        if _merge_company(conn, c):
            stats["companies"] += 1
    for c in manifest.get("contacts", []):
        if _merge_contact(conn, c):
            stats["contacts"] += 1
    for r in manifest.get("receipt_runs", []):
        if _merge_receipt_run(conn, r):
            stats["receipt_runs"] += 1
    grouped: dict[str, list[dict]] = {}
    for row in manifest.get("opening_breakdowns", []):
        grouped.setdefault(row["contact_afm"], []).append(row)
    for afm, rows in grouped.items():
        rows.sort(key=lambda r: r["seq"])
        if _merge_opening_breakdown(conn, afm, rows):
            stats["opening_breakdowns"] += 1
    conn.commit()
    return stats


# ------------------------------------------------- SMTP credential share --
# Το smtp_password ΔΕΝ ταξιδεύει ποτέ μέσα στο κανονικό manifest (βλ.
# _COMPANY_LOCAL_ONLY_COLS) -- ένας σταθμός εργασίας δεν σημαίνει αυτόματα
# διαφορετική εταιρεία, οπότε δύο σταθμοί μπορεί να χρειάζονται το ΙΔΙΟ
# SMTP password. Αντί να κάθεται μόνιμα στο cloud (plaintext), το μοίρασμα
# είναι ρητή, μονομιάς ενέργεια: ανεβαίνει σε ξεχωριστό φάκελο
# smtp-credentials/, και διαγράφεται μόλις το πάρει ΕΝΑ άλλο μηχάνημα.
# Γνωστός περιορισμός: αν παραπάνω από ένας άλλος σταθμός το χρειάζεται,
# μόνο ο πρώτος που θα κάνει sync θα το πάρει αυτόματα -- χρειάζεται νέο
# "μοίρασμα" για τον επόμενο.
def share_smtp_password(conn, company_name: str) -> dict:
    row = conn.execute(
        "SELECT smtp_host, smtp_port, smtp_email, smtp_password FROM companies WHERE name=?",
        (company_name,),
    ).fetchone()
    if not row or not row["smtp_password"]:
        return {"ok": False, "error": "Δεν έχουν οριστεί τοπικά SMTP στοιχεία για αυτή την εταιρεία."}
    remote = get_remote_path().rstrip("/")
    payload = {
        "company_name": company_name,
        "smtp_host": row["smtp_host"], "smtp_port": row["smtp_port"],
        "smtp_email": row["smtp_email"], "smtp_password": row["smtp_password"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        fname = f"{sanitize(company_name)}.json"
        path = os.path.join(tmp, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return run_rclone(["copyto", path, f"{remote}/smtp-credentials/{fname}"], timeout=30)


def _pull_smtp_credentials(conn) -> int:
    """Καλείται από sync_startup. Παίρνει (μόνο) ό,τι διαμοιρασμένο
    password αφορά εταιρεία που ξέρουμε ήδη τοπικά αλλά χωρίς δικό μας
    password, το αποθηκεύει, και διαγράφει το αρχείο από το remote -- ώστε
    να μη μένει εκεί. Το μηχάνημα που το μοιράστηκε (έχει ήδη το δικό του
    password) το προσπερνάει σιωπηλά, χωρίς να το διαγράψει πρόωρα."""
    remote = get_remote_path().rstrip("/")
    creds_dir = f"{remote}/smtp-credentials"
    consumed = []
    with tempfile.TemporaryDirectory() as tmp:
        r = run_rclone(["copy", creds_dir, tmp, "--include", "*.json"], timeout=30)
        if not r["ok"]:
            return 0
        for f in Path(tmp).glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            company_name = data.get("company_name")
            if not company_name:
                continue
            local = conn.execute(
                "SELECT id, smtp_password FROM companies WHERE name=?", (company_name,),
            ).fetchone()
            if not local or local["smtp_password"]:
                continue  # άγνωστη εδώ ακόμα, ή ήδη έχουμε δικό μας -- δεν το αγγίζουμε
            conn.execute(
                "UPDATE companies SET smtp_host=?, smtp_port=?, smtp_email=?, smtp_password=? WHERE id=?",
                (data.get("smtp_host"), data.get("smtp_port"), data.get("smtp_email"),
                 data.get("smtp_password"), local["id"]),
            )
            consumed.append(f.name)
        conn.commit()
    for fname in consumed:
        run_rclone(["deletefile", f"{creds_dir}/{fname}"], timeout=15)
    return len(consumed)


# --------------------------------------------------------- orchestration --
def sync_startup(conn) -> dict:
    """Κατεβάζει ΟΛΑ τα manifests (κάθε μηχανήματος, μαζί με το δικό μας --
    αβλαβές, idempotent) και τα κάνει merge, μετά ελέγχει για διαμοιρασμένα
    SMTP passwords (βλ. _pull_smtp_credentials). Καλείται όταν ανοίγει η
    εφαρμογή."""
    remote = get_remote_path()
    manifests_dir = f"{remote.rstrip('/')}/manifests"
    with tempfile.TemporaryDirectory() as tmp:
        r = run_rclone(["copy", manifests_dir, tmp, "--include", "*.json"], timeout=90)
        if not r["ok"]:
            return {"ok": False, "error": r["error"], "no_internet": is_network_error(r["error"])}
        stats = {"companies": 0, "contacts": 0, "receipt_runs": 0, "opening_breakdowns": 0}
        machines = 0
        for f in Path(tmp).glob("*.json"):
            try:
                manifest = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = import_manifest(conn, manifest)
            for k in stats:
                stats[k] += s[k]
            machines += 1
    smtp_received = _pull_smtp_credentials(conn)
    return {"ok": True, "machines": machines, "smtp_received": smtp_received, **stats}


def sync_shutdown(conn) -> dict:
    """Ανεβάζει το δικό μας manifest, ένα backup της τοπικής βάσης, και τα
    PDF κάθε εταιρείας (one-way, additive) προς το remote. Καλείται όταν
    κλείνει η εφαρμογή."""
    remote = get_remote_path().rstrip("/")
    hostname = _hostname()

    manifest = export_manifest(conn)
    with tempfile.TemporaryDirectory() as tmp:
        manifest_path = os.path.join(tmp, f"{hostname}.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, default=str)
        r_manifest = run_rclone(
            ["copyto", manifest_path, f"{remote}/manifests/{hostname}.json"], timeout=60,
        )

    r_backup = run_rclone(
        ["copyto", str(dbmod.DB_PATH), f"{remote}/backup/{hostname}/app_data.db"], timeout=60,
    )

    pdf_results = []
    for company in conn.execute(
        "SELECT name, output_dir FROM companies WHERE output_dir IS NOT NULL AND output_dir != ''"
    ):
        if not os.path.isdir(company["output_dir"]):
            continue
        dest = f"{remote}/pdf/{sanitize(company['name'])}"
        r = run_rclone(
            ["copy", company["output_dir"], dest, "--checksum", "--create-empty-src-dirs"],
            timeout=180,
        )
        pdf_results.append({"company": company["name"], "ok": r["ok"], "error": r.get("error")})

    ok = r_manifest["ok"] and r_backup["ok"] and all(p["ok"] for p in pdf_results)
    return {"ok": ok, "manifest": r_manifest, "backup": r_backup, "pdf": pdf_results}
