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
import sys
import tempfile
from pathlib import Path

from core import db as dbmod

# rclone.exe είναι console app -- χωρίς αυτό, κάθε κλήση ανοιγοκλείνει ένα
# ορατό console παράθυρο πίσω από το (windowed, χωρίς console) GUI μας.
# Στο άνοιγμα της εφαρμογής γίνονται αρκετές τέτοιες κλήσεις πίσω-πίσω
# (manifests, heartbeat, pdf pull) -- εξ ου και το αισθητό "flash"
# παραθύρου πολλές φορές.
_SUBPROCESS_KWARGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

RCLONE_BIN = os.environ.get("AIRECEIPTS_RCLONE_PATH") or "rclone"
DEFAULT_REMOTE = "mega:AIReceipts"


def _config_path() -> Path:
    return dbmod.DB_PATH.parent / "sync_config.json"


def _load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(**updates) -> None:
    cfg = _load_config()
    cfg.update(updates)
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def get_remote_path() -> str:
    return _load_config().get("remote_path") or DEFAULT_REMOTE


def save_remote_path(remote_path: str) -> None:
    _save_config(remote_path=remote_path)


def is_main_machine() -> bool:
    """Τοπική ρύθμιση ανά σταθμό -- ΔΕΝ συγχρονίζεται. Ο "main" υπολογιστής
    (συγκεκριμένο φυσικό μηχάνημα, π.χ. του γραφείου) είναι αυτός στον
    οποίο καταλήγουν τοπικά (pull-down, βλ. pull_pdfs) τα PDF όλων των
    σταθμών, ανά εταιρεία, μέσα στο ήδη υπάρχον output_dir της -- ώστε να
    υπάρχει μία πραγματική θέση με όλα τα PDF μιας εταιρείας, όχι μόνο στο
    cloud."""
    return bool(_load_config().get("is_main"))


def set_main_machine(value: bool) -> None:
    _save_config(is_main=bool(value))


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


def is_missing_dir_error(error: str | None) -> bool:
    """True όταν το rclone απέτυχε επειδή ο φάκελος-πηγή δεν υπάρχει ακόμα
    -- φυσιολογικό στην πρώτη ποτέ χρήση (κανένα μηχάνημα δεν έχει κάνει
    ακόμα sync), όχι πραγματικό σφάλμα."""
    return bool(re.search(r"directory not found", error or "", re.I))


def run_rclone(args: list[str], timeout: int = 30) -> dict:
    try:
        proc = subprocess.run(
            [RCLONE_BIN, *args], capture_output=True, text=True, timeout=timeout,
            **_SUBPROCESS_KWARGS,
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
# διαδρομές αρχείων χωρίς νόημα να φύγουν από το μηχάνημα (signature_path/
# output_dir). Το SMTP (smtp_host/port/email/password) ΣΥΓΧΡΟΝΙΖΕΤΑΙ πλέον
# κανονικά σαν τα υπόλοιπα στοιχεία εταιρείας -- ρητή απόφαση του χρήστη
# (2026-08-06) ώστε κάθε σταθμός να έχει αυτόματα το σωστό email ΚΑΘΕ
# εταιρείας, όχι μόνο της πρώτης που θα προλάβει ένα one-shot μοίρασμα.
# Trade-off που αποδέχτηκε ρητά: το app password κάθεται plaintext μόνιμα
# στο shared manifest (ιδιωτικό Mega account).
_COMPANY_LOCAL_ONLY_COLS = ("signature_path", "output_dir")


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
    """Last-write-wins σε updated_at, με deleted_at ως tombstone -- μια
    διαγραφή σε ένα μηχάνημα είναι απλά μια ενημέρωση με deleted_at
    γεμάτο, οπότε ταξιδεύει με το ίδιο ρολόι/σύγκριση με κάθε άλλη αλλαγή
    (όποια αλλαγή -- edit ή delete -- έχει το πιο πρόσφατο updated_at
    κερδίζει, βλ. core/db.py::delete_company). Χωρίς αυτό, ένα φρέσκο/άλλο
    μηχάνημα θα ξαναέφερνε για πάντα μια ήδη διαγραμμένη εταιρεία σε κάθε
    sync (πραγματικό περιστατικό: το "ΕΤΑΙΡΕΙΑ Χ" test data)."""
    name = c.get("name")
    if not name:
        return False
    remote_updated = c.get("updated_at") or ""
    remote_deleted_at = c.get("deleted_at")
    local = conn.execute(
        "SELECT updated_at FROM companies WHERE name=?", (name,),
    ).fetchone()
    if local:
        if (local["updated_at"] or "") >= remote_updated:
            return False
        # ΔΕΝ αγγίζουμε signature_path/output_dir -- τοπικές διαδρομές
        # αρχείων του κάθε σταθμού, νόημα μόνο τοπικά. Το SMTP ΤΟ αγγίζουμε
        # πλέον (βλ. σχόλιο στο _COMPANY_LOCAL_ONLY_COLS).
        conn.execute(
            """UPDATE companies SET subtitle=?, address_line=?, ids_line=?, email=?,
                   receipt_prefix=?, receipt_padding=?, default_cap=?, default_round_step=?,
                   smtp_host=?, smtp_port=?, smtp_email=?, smtp_password=?,
                   updated_at=?, deleted_at=?
               WHERE name=?""",
            (c.get("subtitle"), c.get("address_line"), c.get("ids_line"), c.get("email"),
             c.get("receipt_prefix"), c.get("receipt_padding"), c.get("default_cap"),
             c.get("default_round_step"), c.get("smtp_host"), c.get("smtp_port"),
             c.get("smtp_email"), c.get("smtp_password"), remote_updated, remote_deleted_at, name),
        )
        return True
    if remote_deleted_at:
        # Tombstone για εταιρεία που αυτό το μηχάνημα δεν γνώρισε ποτέ ζωντανή
        # -- τίποτα να διαγράψει τοπικά, μη δημιουργείς άχρηστη γραμμή.
        return False
    conn.execute(
        """INSERT INTO companies (name, subtitle, address_line, ids_line, email,
               receipt_prefix, receipt_padding, default_cap, default_round_step,
               smtp_host, smtp_port, smtp_email, smtp_password, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, c.get("subtitle"), c.get("address_line"), c.get("ids_line"), c.get("email"),
         c.get("receipt_prefix"), c.get("receipt_padding"), c.get("default_cap"),
         c.get("default_round_step"), c.get("smtp_host"), c.get("smtp_port"),
         c.get("smtp_email"), c.get("smtp_password"), remote_updated),
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
    """Insert-if-missing βάσει uuid, ΚΑΙ tombstone-aware -- ίδιο πρόβλημα με
    το _merge_company αν δεν το χειριζόμασταν: χωρίς αυτό, μια διαγραφή
    (core/db.py::delete_receipt_run) σε ένα μηχάνημα θα ξαναδιάβαζε το
    ίδιο uuid από το manifest ενός άλλου μηχανήματος που δεν το έμαθε ποτέ
    διαγραμμένο. Δεν χρειάζεται LWW/updated_at εδώ όπως στις εταιρείες --
    μια διαγραφή είναι μονής κατεύθυνσης (δεν "ξαναζωντανεύει" ποτέ ένα
    receipt_run, αφού κάθε νέα έκδοση παίρνει καινούργιο uuid), οπότε ένα
    tombstone που βρέθηκε κάπου κερδίζει πάντα."""
    ruuid = r.get("uuid")
    if not ruuid:
        return False
    existing = conn.execute(
        "SELECT deleted_at FROM receipt_runs WHERE uuid=?", (ruuid,),
    ).fetchone()
    if existing:
        if r.get("deleted_at") and not existing["deleted_at"]:
            conn.execute(
                "UPDATE receipt_runs SET deleted_at=? WHERE uuid=?", (r["deleted_at"], ruuid),
            )
            return True
        return False
    if r.get("deleted_at"):
        return False  # tombstone για run που αυτό το μηχάνημα δεν είχε ποτέ -- τίποτα να διαγράψει
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


# --------------------------------------------------------- orchestration --
def sync_startup(conn) -> dict:
    """Κατεβάζει ΟΛΑ τα manifests (κάθε μηχανήματος, μαζί με το δικό μας --
    αβλαβές, idempotent) και τα κάνει merge (companies -- SMTP included --
    /contacts/opening_breakdowns/receipt_runs). Καλείται όταν ανοίγει η
    εφαρμογή."""
    remote = get_remote_path()
    manifests_dir = f"{remote.rstrip('/')}/manifests"
    stats = {"companies": 0, "contacts": 0, "receipt_runs": 0, "opening_breakdowns": 0}
    machines = 0
    with tempfile.TemporaryDirectory() as tmp:
        r = run_rclone(["copy", manifests_dir, tmp, "--include", "*.json"], timeout=90)
        if not r["ok"] and not is_missing_dir_error(r["error"]):
            return {"ok": False, "error": r["error"], "no_internet": is_network_error(r["error"])}
        for f in Path(tmp).glob("*.json"):
            try:
                manifest = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = import_manifest(conn, manifest)
            for k in stats:
                stats[k] += s[k]
            machines += 1
    pdfs_pulled = pull_pdfs(conn) if is_main_machine() else None
    return {
        "ok": True, "machines": machines, "pdfs_pulled": pdfs_pulled, **stats,
    }


def pull_pdfs(conn) -> dict:
    """Μόνο στο "main" μηχάνημα (is_main_machine): κατεβάζει (rclone copy,
    additive -- ποτέ delete) τα PDF ΟΛΩΝ των σταθμών, ανά εταιρεία, μέσα
    στο ήδη υπάρχον output_dir της -- ώστε να υπάρχει μία πραγματική τοπική
    θέση με όλα τα PDF μιας εταιρείας, όχι μόνο στο cloud."""
    remote = get_remote_path().rstrip("/")
    results = {}
    for company in conn.execute(
        "SELECT name, output_dir FROM companies WHERE output_dir IS NOT NULL AND output_dir != ''"
    ):
        if not os.path.isdir(company["output_dir"]):
            continue
        src = f"{remote}/pdf/{sanitize(company['name'])}"
        r = run_rclone(
            ["copy", src, company["output_dir"], "--checksum", "--include", "*.pdf", "--include", "*.csv"],
            timeout=180,
        )
        if not r["ok"] and is_missing_dir_error(r["error"]):
            continue  # κανείς άλλος σταθμός δεν έχει ανεβάσει ακόμα PDF για αυτή την εταιρεία
        results[company["name"]] = r["ok"]
    return results


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
        # --ignore-times σε ΚΑΙ τα δύο copyto εδώ -- βλ. core/presence.py::
        # send_heartbeat για το γιατί: το rclone/Mega backend σιωπηλά
        # προσπερνάει ένα ανέβασμα αν το νέο αρχείο έχει ίδιο μέγεθος με το
        # ήδη αποθηκευμένο στο ίδιο path -- εντελώς εύλογο για ένα manifest
        # μετά από μια μικρή αλλαγή (π.χ. ίδιου μήκους επωνυμία) ή για ένα
        # SQLite backup μετά από λίγες γραμμές αλλαγή (σταθερό page size).
        # Χωρίς αυτό, sync_shutdown θα ανέφερε επιτυχία ενώ το remote θα
        # έμενε αθόρυβα μπαγιάτικο.
        r_manifest = run_rclone(
            ["copyto", manifest_path, f"{remote}/manifests/{hostname}.json", "--ignore-times"],
            timeout=60,
        )

    r_backup = run_rclone(
        ["copyto", str(dbmod.DB_PATH), f"{remote}/backup/{hostname}/app_data.db", "--ignore-times"],
        timeout=60,
    )

    pdf_results = []
    for company in conn.execute(
        "SELECT name, output_dir FROM companies WHERE output_dir IS NOT NULL AND output_dir != ''"
    ):
        if not os.path.isdir(company["output_dir"]):
            continue
        dest = f"{remote}/pdf/{sanitize(company['name'])}"
        r = run_rclone(
            # --include *.pdf/*.csv -- άμυνα αν το output_dir δεν είναι
            # αποκλειστικός φάκελος αποδείξεων (π.χ. έχει κι άλλα αρχεία
            # μέσα), ώστε να μην ανέβει ό,τι τύχει να υπάρχει εκεί.
            ["copy", company["output_dir"], dest, "--checksum", "--create-empty-src-dirs",
             "--include", "*.pdf", "--include", "*.csv"],
            timeout=180,
        )
        pdf_results.append({"company": company["name"], "ok": r["ok"], "error": r.get("error")})

    ok = r_manifest["ok"] and r_backup["ok"] and all(p["ok"] for p in pdf_results)
    return {"ok": ok, "manifest": r_manifest, "backup": r_backup, "pdf": pdf_results}
