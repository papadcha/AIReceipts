# -*- coding: utf-8 -*-
"""Convert a euro amount to Greek words, in the style used on printed
receipts (e.g. 436.87 -> "Τετρακόσια τριάντα έξι ευρώ και ογδόντα επτά
λεπτά").
"""

ONES = ["", "ένα", "δύο", "τρία", "τέσσερα", "πέντε", "έξι", "επτά", "οκτώ", "εννέα"]
ONES_FEM = ["", "μία", "δύο", "τρεις", "τέσσερις", "πέντε", "έξι", "επτά", "οκτώ", "εννέα"]
TEENS = [
    "δέκα", "έντεκα", "δώδεκα", "δεκατρία", "δεκατέσσερα", "δεκαπέντε",
    "δεκαέξι", "δεκαεπτά", "δεκαοκτώ", "δεκαεννέα",
]
TENS = ["", "δέκα", "είκοσι", "τριάντα", "σαράντα", "πενήντα", "εξήντα", "εβδομήντα", "ογδόντα", "ενενήντα"]
HUNDREDS = ["", "εκατό", "διακόσια", "τριακόσια", "τετρακόσια", "πεντακόσια", "εξακόσια", "επτακόσια", "οκτακόσια", "εννιακόσια"]


def _under_100(n: int, ones=ONES) -> str:
    if n < 10:
        return ones[n]
    if n < 20:
        return TEENS[n - 10]
    t, o = divmod(n, 10)
    if o == 0:
        return TENS[t]
    return f"{TENS[t]} {ones[o]}"


def _under_1000(n: int, ones=ONES) -> str:
    if n < 100:
        return _under_100(n, ones)
    h, rest = divmod(n, 100)
    if rest == 0:
        return HUNDREDS[h]
    hundred_word = "εκατόν" if h == 1 else HUNDREDS[h]
    return f"{hundred_word} {_under_100(rest, ones)}"


def number_to_words(n: int) -> str:
    """Cardinal number in words, neuter/plural forms as used for ευρώ/λεπτά."""
    if n == 0:
        return "μηδέν"
    if n < 1000:
        return _under_1000(n, ONES)

    thousands, rest = divmod(n, 1000)
    if thousands == 1:
        thousands_word = "χίλια"
    else:
        thousands_word = f"{_under_1000(thousands, ONES_FEM)} χιλιάδες"
    if rest == 0:
        return thousands_word
    return f"{thousands_word} {_under_1000(rest, ONES)}"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def amount_to_words(amount: float) -> str:
    """e.g. 436.87 -> 'Τετρακόσια τριάντα έξι ευρώ και ογδόντα επτά λεπτά'
    e.g. 170.00 -> 'Εκατόν εβδομήντα ευρώ'
    """
    cents_total = round(amount * 100)
    euros, cents = divmod(cents_total, 100)

    euro_unit = "ευρώ"
    euro_part = f"{number_to_words(euros)} {euro_unit}"

    if cents == 0:
        return _cap(euro_part)

    cent_unit = "λεπτό" if cents == 1 else "λεπτά"
    cents_part = f"{number_to_words(cents)} {cent_unit}"
    return _cap(f"{euro_part} και {cents_part}")
