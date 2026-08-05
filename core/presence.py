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
        r = syncmod.run_rclone(["copyto", tmp_path, dest], timeout=30)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "path": dest}


def list_presence(exclude_self: bool = True) -> list[dict]:
    remote_dir = _remote_dir()
    if remote_dir is None:
        return []

    with tempfile.TemporaryDirectory() as tmp_dir:
        r = syncmod.run_rclone(["copy", remote_dir, tmp_dir, "--include", "*.json"], timeout=60)
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
            result.append({
                "user": data.get("user", ""),
                "computer": data.get("computer", ""),
                "last_seen": data.get("last_seen", ""),
            })
        return result
