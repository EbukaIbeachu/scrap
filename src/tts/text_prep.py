"""Make narration text sound natural when read by a TTS voice."""
from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
        "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (10**3, "thousand")]

ABBREVIATIONS = {
    r"\bDr\.": "Doctor", r"\bMr\.": "Mister", r"\bMrs\.": "Missus", r"\bProf\.": "Professor",
    r"\bGov\.": "Governor", r"\bSen\.": "Senator", r"\bGovt\b": "Government", r"\bgovt\b": "government",
    r"\bvs\.?\b": "versus", r"\bSt\.": "Saint", r"\be\.g\.": "for example", r"\bi\.e\.": "that is",
}
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
YEAR_CONTEXT = rf"(?:{MONTHS}|in|since|by|until|till|from|during|before|after|year|of|early|late|mid|fy)"
CURRENCIES = {"₦": "naira", "NGN": "naira", "N": "naira", "$": "dollars", "£": "pounds", "€": "euros"}
MAGNITUDES = {"k": "thousand", "m": "million", "mn": "million", "bn": "billion", "b": "billion", "tn": "trillion",
              "thousand": "thousand", "million": "million", "billion": "billion", "trillion": "trillion"}


def int_to_words(n: int) -> str:
    """0 -> 'zero', 1250 -> 'one thousand, two hundred and fifty'."""
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return f"{ONES[n // 100]} hundred" + (f" and {int_to_words(rest)}" if rest else "")
    for value, name in SCALES:
        if n >= value:
            head, rest = divmod(n, value)
            out = f"{int_to_words(head)} {name}"
            if rest:
                out += (" and " if rest < 100 else ", ") + int_to_words(rest)
            return out
    return str(n)  # pragma: no cover


def year_to_words(y: int) -> str:
    """1999 -> 'nineteen ninety-nine', 2026 -> 'twenty twenty-six', 2005 -> 'two thousand and five'."""
    if y == 2000:
        return "two thousand"
    if 2001 <= y <= 2009:
        return f"two thousand and {ONES[y - 2000]}"
    head, tail = divmod(y, 100)
    if tail == 0:
        return f"{int_to_words(head)} hundred"
    if tail < 10:
        return f"{int_to_words(head)} oh {ONES[tail]}"
    return f"{int_to_words(head)} {int_to_words(tail)}"


def number_to_words(token: str) -> str:
    """'1,250' -> words; '3.5' -> 'three point five'."""
    token = token.replace(",", "")
    if "." in token:
        whole, frac = token.split(".", 1)
        return f"{int_to_words(int(whole or 0))} point " + " ".join(ONES[int(d)] for d in frac if d.isdigit())
    return int_to_words(int(token))


def _spell_money(match: re.Match) -> str:
    symbol, amount, mag = match.group(1), match.group(2), (match.group(3) or "").lower()
    words = number_to_words(amount)
    if mag in MAGNITUDES:
        words += " " + MAGNITUDES[mag]
    return f"{words} {CURRENCIES.get(symbol, symbol)}"


def apply_pronunciations(text: str, mapping: dict[str, str] | None) -> str:
    """Replace whole words using the user's pronunciation dictionary (longest keys first)."""
    for word in sorted((mapping or {}), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", mapping[word], text, flags=re.I)
    return text


def prepare_text(text: str, pronunciations: dict[str, str] | None = None) -> str:
    """Clean and expand narration so a TTS voice reads it naturally."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>~|]+", " ", text)
    for pattern, repl in ABBREVIATIONS.items():
        text = re.sub(pattern, repl, text)
    text = apply_pronunciations(text, pronunciations)
    # money: ₦5 billion, N2,500, $1.2m, NGN 300
    text = re.sub(r"(₦|NGN|\$|£|€|\bN(?=\d))\s?(\d[\d,]*(?:\.\d+)?)\s?((?i:thousand|million|billion|trillion|bn|mn|tn|k|m|b))?\b",
                  _spell_money, text)
    text = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s?%", lambda m: f"{number_to_words(m.group(1))} percent", text)
    text = re.sub(rf"(?i)\b({YEAR_CONTEXT})(\s+)((?:19|20)\d\d)\b(?![,\d])",
                  lambda m: f"{m.group(1)}{m.group(2)}{year_to_words(int(m.group(3)))}", text)
    text = re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: number_to_words(m.group(0).rstrip(",")), text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])["\')]*\s+(?=["\'(]?[A-Z0-9])', text.strip())
    return [p.strip() for p in parts if p.strip()]


def group_sentences(sentences: list[str], min_chars: int = 40, max_chars: int = 240) -> list[str]:
    """Merge very short sentences into the next one; keep chunks under ``max_chars`` when possible."""
    groups: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + 1 + len(s) > max_chars:
            groups.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
        if len(buf) >= min_chars:
            groups.append(buf)
            buf = ""
    if buf:
        if groups and len(buf) < min_chars and len(groups[-1]) + 1 + len(buf) <= max_chars:
            groups[-1] += " " + buf
        else:
            groups.append(buf)
    return groups
