# -*- coding: utf-8 -*-
"""Κατανομή ενός συνολικού ποσού εξόφλησης σε αποδείξεις ≤ όριο (default
500€), με αιτιολογία βάσει FIFO κατανομής στα ανοιχτά τιμολόγια, και επιλογή
ημερομηνιών που είτε προχωρούν γραμμικά είτε σκορπίζονται σε ένα διάστημα
χωρίς ποτέ να "πληρώνουν" τιμολόγιο πριν αυτό εκδοθεί.

ΠΡΟΣΟΧΗ - ΝΟΜΙΚΟ ΘΕΜΑ: ο τεχνητός διαχωρισμός ΜΙΑΣ συναλλαγής σε πολλές
αποδείξεις ≤500€ την ΙΔΙΑ ημέρα για να παρακαμφθεί το όριο μετρητών (άρθρο 20
ν.3842/2010 όπως ισχύει) είναι παράνομος -- γι' αυτό κάθε απόδειξη παίρνει
διαφορετική ημερομηνία.
"""
from __future__ import annotations

from datetime import datetime, timedelta

GREEK_AND = "και"

SPECIAL_LABELS = {
    "ΥΠΟΛΟΙΠΟ ΕΝΑΡΞΗΣ": "το αρχικό υπόλοιπο",
    "ΕΝΑΝΤΙ ΜΕΛΛΟΝΤΙΚΩΝ ΑΓΟΡΩΝ / ΠΡΟΚΑΤΑΒΟΛΗ": "μελλοντικές αγορές (προκαταβολή)",
}


def _short_date(d: str) -> str:
    """"12/5/2026" -> "12-05-26" (όπως στις πραγματικές αιτιολογίες)."""
    dt = datetime.strptime(d, "%d/%m/%Y")
    return dt.strftime("%d-%m-%y")


def _join_greek(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {GREEK_AND} " + items[-1]


def _bare_ref(label: str, date: str | None) -> str:
    """Χωρίς 'τιμ' μπροστά -- μόνο CODE/date, όπως στα στοιχεία μιας λίστας."""
    if label in SPECIAL_LABELS:
        return SPECIAL_LABELS[label]
    if date is None or date == "-":
        return label
    return f"{label}/{_short_date(date)}"


def _invoice_ref(label: str, date: str | None) -> str:
    if label in SPECIAL_LABELS:
        return SPECIAL_LABELS[label]
    return f"τιμ {_bare_ref(label, date)}"


def build_aitiologia(closed: list[tuple[str, str]], partial: tuple[str, str] | None) -> str:
    closed_refs = [_bare_ref(c, d) for c, d in closed]
    if closed_refs and partial:
        return f"Εξόφληση τιμ {_join_greek(closed_refs)} και έναντι {_invoice_ref(*partial)}"
    if closed_refs and not partial:
        return f"Εξόφληση τιμ {_join_greek(closed_refs)}"
    if partial and not closed_refs:
        return f"Έναντι {_invoice_ref(*partial)}"
    return "Έναντι λογαριασμού"


def allocate_receipts(open_invoices, amount: float, cap: float, round_step: float = 100.0):
    """Αναπαράγει το πραγματικό σκεπτικό πίσω από τις χειρόγραφες αποδείξεις:
    - Κλείνει πάντα ΟΛΟΚΛΗΡΑ τιμολόγια FIFO μέσα στο όριο (cap).
    - Αν μείνει χώρος κάτω από το cap που δεν χωράει άλλο ολόκληρο τιμολόγιο,
      παίρνει ΣΤΡΟΓΓΥΛΟ 'έναντι' ποσό (πολλαπλάσιο του round_step, π.χ. 100€)
      από το επόμενο τιμολόγιο -- όχι ό,τι χρειάζεται για να φτάσει ακριβώς
      στο cap. Αν ο στρογγυλεμένος χώρος είναι < round_step, δεν παίρνει
      καθόλου partial (κλείνει την απόδειξη μόνο με ό,τι έκλεισε).
    - Η ΤΕΛΕΥΤΑΙΑ απόδειξη (αυτή που καλύπτει ό,τι απομένει από το συνολικό
      ζητούμενο ποσό) παίρνει ακριβώς το υπόλοιπο, χωρίς στρογγυλοποίηση.
    Επαληθεύτηκε ότι αναπαράγει ακριβώς πραγματικές αποδείξεις πάνω σε
    πραγματικά δεδομένα καρτέλας.
    Returns list of dicts: {amount, closed:[(code,date)], partial:(code,date)|None, overflow:bool}
    """
    queue = [[inv.label, inv.date, inv.remaining] for inv in open_invoices]
    remaining_to_allocate = round(amount, 2)
    receipts = []

    while remaining_to_allocate > 0.005:
        is_final_chunk = remaining_to_allocate <= cap + 0.005
        chunk_cap = remaining_to_allocate if is_final_chunk else cap
        chunk_amount = 0.0
        closed: list[tuple[str, str]] = []
        partial = None

        while queue:
            room = round(chunk_cap - chunk_amount, 2)
            if room <= 0.005:
                break
            label, date, head_remaining = queue[0]
            if head_remaining <= room + 0.005:
                chunk_amount = round(chunk_amount + head_remaining, 2)
                closed.append((label, date))
                queue.pop(0)
                continue
            if is_final_chunk:
                pay = room  # τελευταία απόδειξη: παίρνει ό,τι ακριβώς απομένει
            else:
                pay = (room // round_step) * round_step  # στρογγυλό top-up
            if pay <= 0.005:
                break  # δεν αξίζει partial -> κλείνει η απόδειξη ως έχει
            queue[0][2] = round(head_remaining - pay, 2)
            chunk_amount = round(chunk_amount + pay, 2)
            partial = (label, date)
            break

        overflow = False
        if not queue and chunk_amount < chunk_cap - 0.005:
            # Το ζητούμενο ποσό ξεπερνά το σύνολο των ανοιχτών τιμολογίων
            shortfall = round(chunk_cap - chunk_amount, 2)
            chunk_amount = round(chunk_amount + shortfall, 2)
            partial = ("ΕΝΑΝΤΙ ΜΕΛΛΟΝΤΙΚΩΝ ΑΓΟΡΩΝ / ΠΡΟΚΑΤΑΒΟΛΗ", None)
            overflow = True

        receipts.append({
            "amount": chunk_amount,
            "closed": closed,
            "partial": partial,
            "overflow": overflow,
        })
        remaining_to_allocate = round(remaining_to_allocate - chunk_amount, 2)
        if chunk_amount <= 0.005:
            break  # safety net, should not normally happen

    return receipts


def chunk_floor_dates(plan: list[dict], period_start: datetime) -> list[datetime]:
    """Για κάθε απόδειξη του plan, η παλαιότερη επιτρεπτή ημερομηνία: δεν
    μπορεί να είναι πριν από το πιο πρόσφατο τιμολόγιο που αναφέρει (κλεισμένο
    ή έναντι), αλλιώς η καρτέλα θα έδειχνε αρνητικό υπόλοιπο εκείνη τη
    στιγμή -- θα πλήρωνε κάτι που δεν είχε ακόμα εκδοθεί. Το period_start
    είναι επιπλέον ένα σκληρό κάτω όριο για ΟΛΕΣ τις αποδείξεις (π.χ. όταν
    θέλουμε να περιορίσουμε το εύρος ημερομηνιών σε ένα συγκεκριμένο
    διάστημα, ακόμα κι αν τα τιμολόγια που κλείνουν είναι παλαιότερα)."""
    floors = []
    for chunk in plan:
        refs = [d for _, d in chunk["closed"] if d and d != "-"]
        if chunk["partial"] and chunk["partial"][1] and chunk["partial"][1] != "-":
            refs.append(chunk["partial"][1])
        floor = max(datetime.strptime(d, "%d/%m/%Y") for d in refs) if refs else period_start
        floors.append(max(floor, period_start))
    return floors


def spread_dates_respecting_invoices(
    plan: list[dict], period_start: datetime, end_date: datetime,
) -> list[datetime]:
    """Σκορπίζει τις ημερομηνίες των αποδείξεων ομοιόμορφα μέσα στο
    [period_start, end_date], αλλά ποτέ πριν από το floor της κάθε απόδειξης
    (βλ. chunk_floor_dates) -- ώστε το υπόλοιπο στην καρτέλα να είναι πάντα
    >=0 σε κάθε χρονική στιγμή, μηδενίζοντας μόνο στην τελευταία απόδειξη.
    Υπολογίζει πρώτα ομοιόμορφα σημεία σε όλο το διάστημα και μετά τα σπρώχνει
    μπροστά όσο χρειάζεται για να σεβαστούν το floor τους -- ΟΧΙ ανάμεσα σε
    διαδοχικά floors, γιατί αυτό μαζεύει τις πρώτες αποδείξεις μαζί όποτε
    αρκετά floors στη σειρά συμπίπτουν (π.χ. όταν είναι όλα παλαιότερα από
    το period_start και άρα ισούνται με αυτό)."""
    floors = chunk_floor_dates(plan, period_start)
    n = len(floors)
    if n == 1:
        targets = [end_date]
    else:
        span = (end_date - period_start).days
        targets = [period_start + timedelta(days=round(span * i / (n - 1))) for i in range(n)]

    dates: list[datetime] = []
    prev = None
    for i in range(n):
        d = max(targets[i], floors[i])
        if prev is not None and d <= prev:
            d = prev + timedelta(days=1)
        while d.weekday() >= 5:  # 5=Σάββατο, 6=Κυριακή
            d += timedelta(days=1)
        dates.append(d)
        prev = d
    return dates


def linear_dates(start_date: datetime, gap_days: int, n: int) -> list[datetime]:
    return [start_date + timedelta(days=gap_days * i) for i in range(n)]
