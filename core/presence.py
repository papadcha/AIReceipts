# -*- coding: utf-8 -*-
"""Heartbeat-based presence detection πάνω στο ίδιο rclone remote που
χρησιμοποιεί το core/sync.py -- ίδιο ακριβώς σχήμα με το backend/presence.py
του expvault και το modules/presence.js του lab-galatista: κάθε
εγκατάσταση γράφει περιοδικά το δικό της <computer>__<user>.json κάτω από
<remote>/presence/, το list_presence() τα συγχωνεύει όλα σε μία λίστα.
Ταυτότητα = OS username + hostname, όχι login/password -- καμία πρόθεση για
πραγματικό access control, μόνο "ποιος άλλος έχει ανοιχτή την εφαρμογή
τώρα" σαν προειδοποίηση (βλ. core/sync.py για το γιατί δεν κλειδώνουμε)."""
from __future__ import annotations

import json
import os
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core import sync as syncmod

PRESENCE_SUBDIR = "presence"

# Πόσο "φρέσκο" πρέπει να είναι το last_seen για να μετράει ως ενεργός τώρα.
# Πρέπει να είναι αρκετά μεγαλύτερο από το interval του heartbeat retry
# (βλ. gui/wizard.py -- ξαναστέλνεται κάθε 60s όσο η εφαρμογή είναι ανοιχτή)
# ώστε ένα απλό δίκτυο lag να μην κάνει κάποιον να φαίνεται εσφαλμένα
# "έφυγε". Χωρίς αυτό το φίλτρο, ένα heartbeat από μια εφαρμογή που έκλεισε
# απότομα (crash/kill, όχι καθαρό κλείσιμο) έμενε "ενεργός" επ' άπειρον.
PRESENCE_TTL_SECONDS = 180


def _identity() -> dict:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "άγνωστος"
    computer = socket.gethostname() or "machine"
    return {"user": user, "computer": computer}


def whoami() -> dict:
    return _identity()


def _remote_dir() -> str | None:
    remote = syncmod.get_remote_path()
    if not remote:
        return None
    return f"{remote.rstrip('/')}/{PRESENCE_SUBDIR}"


def send_heartbeat() -> dict:
    if not syncmod.is_sync_enabled():
        return {"ok": True, "skipped": True, "reason": "sync_disabled"}
    remote_dir = _remote_dir()
    if remote_dir is None:
        return {"ok": True, "skipped": True, "reason": "no_remote_configured"}

    ident = _identity()
    payload = {
        "user": ident["user"],
        "computer": ident["computer"],
        "last_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    dest = f"{remote_dir}/{syncmod.sanitize(ident['computer'])}__{syncmod.sanitize(ident['user'])}.json"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = tmp.name
    try:
        # --ignore-times: το payload έχει σχεδόν πάντα το ίδιο μέγεθος byte
        # (μόνο η ώρα αλλάζει μέσα σε ένα σταθερό ISO timestamp) -- χωρίς
        # αυτό, το rclone/Mega backend βλέπει "ίδιο μέγεθος" και σιωπηλά
        # ΔΕΝ ξανανεβάζει το αρχείο, οπότε το last_seen θα έμενε παγωμένο
        # στην πρώτη ποτέ αποστολή ό,τι κι αν έκανε το περιοδικό heartbeat.
        r = syncmod.run_rclone(["copyto", tmp_path, dest, "--ignore-times"], timeout=30)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "path": dest}


def clear_presence() -> dict:
    """Best-effort διαγραφή του δικού μας presence αρχείου -- καλείται στο
    καθαρό κλείσιμο της εφαρμογής, ώστε οι άλλοι σταθμοί να δουν άμεσα ότι
    φύγαμε αντί να περιμένουν το PRESENCE_TTL_SECONDS να λήξει. Δεν είναι το
    μόνο μέτρο -- σε crash/kill αυτό δεν προλαβαίνει να τρέξει, γι' αυτό
    υπάρχει και το TTL φίλτρο στο list_presence()."""
    if not syncmod.is_sync_enabled():
        return {"ok": True, "skipped": True, "reason": "sync_disabled"}
    remote_dir = _remote_dir()
    if remote_dir is None:
        return {"ok": True, "skipped": True, "reason": "no_remote_configured"}
    ident = _identity()
    dest = f"{remote_dir}/{syncmod.sanitize(ident['computer'])}__{syncmod.sanitize(ident['user'])}.json"
    return syncmod.run_rclone(["deletefile", dest], timeout=15)


def _is_stale(last_seen: str) -> bool:
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return True  # κατεστραμμένη/άγνωστη μορφή -- πιο ασφαλές να αγνοηθεί
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - seen).total_seconds()
    return age > PRESENCE_TTL_SECONDS


def list_presence(exclude_self: bool = True, timeout: int = 60) -> list[dict]:
    if not syncmod.is_sync_enabled():
        return []
    remote_dir = _remote_dir()
    if remote_dir is None:
        return []

    with tempfile.TemporaryDirectory() as tmp_dir:
        r = syncmod.run_rclone(["copy", remote_dir, tmp_dir, "--include", "*.json"], timeout=timeout)
        if not r["ok"]:
            # π.χ. presence/ δεν υπάρχει ακόμα -- καμία εγκατάσταση δεν έχει
            # στείλει heartbeat ποτέ -- άδεια λίστα, όχι σφάλμα
            return []

        me = _identity()
        result = []
        for f in Path(tmp_dir).glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # αγνόησε κατεστραμμένο/μερικώς-γραμμένο αρχείο
            if not (data.get("user") and data.get("last_seen")):
                continue
            if exclude_self and data.get("user") == me["user"] and data.get("computer") == me["computer"]:
                continue
            if _is_stale(data["last_seen"]):
                continue
            result.append({
                "user": data.get("user", ""),
                "computer": data.get("computer", ""),
                "last_seen": data.get("last_seen", ""),
            })
        return result
