"""How hard a Friday Bundle passphrase is to guess (north star §27.2).

A bundle carries the vault passphrase and the governance key inside its
encryption, so anyone who guesses its passphrase can open the vault and forge
receipts. This is the one strength check every place that sets a bundle
passphrase uses: the Doctor page (scheduled and one-off bundles), the command
line, and the uninstall prompt (which runs the command line).

It estimates guessing effort in bits, offline and deterministically (no
model, no network, no word list download):

* a passphrase of separate words counts each word by the size of a large word
  list (about 11 bits for an ordinary word, less for a very common one), plus
  the separators and any digits or symbols added;
* anything else is split into letter runs and the rest: a letter run of four
  or more counts as one word (a guesser's dictionary), the rest by its length
  against the character classes it uses;
* both lose bits for what guessers try first: a known common password or
  phrase, one repeated character, keyboard or alphabet runs, a year or a date,
  the name "friday", and words repeated within the phrase.

`assess()` returns a score 0-4 (like the meters people know), whether it is
strong enough (`ok`: at least MIN_LENGTH characters and FLOOR_BITS bits), and
plain feedback. Nothing is logged or kept.
"""
from __future__ import annotations

import math
import re
from typing import Optional

MIN_LENGTH = 12
MAX_LENGTH = 256
FLOOR_BITS = 50.0
_SCORES = (28.0, 36.0, FLOOR_BITS, 64.0)      # score 1..4 thresholds

#: Passwords and phrases guessers try first (lower case, spaces removed).
_COMMON = {
    "password", "passw0rd", "p@ssword", "password1", "password123", "123456",
    "12345678", "123456789", "1234567890", "qwerty", "qwertyuiop", "letmein",
    "iloveyou", "welcome", "admin", "monkey", "dragon", "football", "baseball",
    "sunshine", "princess", "trustno1", "abc123", "shadow", "master", "superman",
    "correcthorsebatterystaple", "opensesame", "changeme", "secret", "friday",
    "agentfriday", "passphrase", "mypassphrase", "thisismypassword",
    "thequickbrownfoxjumpsoverthelazydog", "letmeinplease", "iloveyouforever",
}

#: Very common English words: a phrase of these is guessed sooner.
_COMMON_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "my", "me", "i",
    "you", "your", "we", "our", "for", "on", "at", "by", "be", "this", "that",
    "with", "from", "love", "password", "secret", "friday", "agent", "backup",
    "bundle", "pass", "phrase", "key", "home", "name", "day", "one", "two",
    # the words password lists are made of
    "monkey", "dragon", "shadow", "master", "sunshine", "princess", "football", "baseball",
    "welcome", "letmein", "charlie", "jennifer", "michael", "jordan", "liverpool", "arsenal",
    "chelsea", "security", "kitty", "cat", "dog", "hello", "summer", "winter", "spring",
    "autumn", "flower", "angel", "baby", "banana", "cookie", "cheese", "freedom", "ninja",
    "killer", "hunter", "tiger", "lover", "power", "slayer", "batman", "superman", "starwars",
    "pokemon", "trustno", "admin", "user", "login", "qwerty", "money", "john", "smith",
}

_RUNS = ("abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm",
         "`1234567890-=", "~!@#$%^&*()_+", "1qaz2wsx3edc4rfv5tgb", "qazwsxedcrfvtgbyhnujm",
         "1234567890", "0123456789", "02468", "13579", "97531", "86420")
#: A shifted number-row key and the digit under it.
_UNSHIFT = str.maketrans({"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
                          "*": "8", "(": "9", ")": "0"})
#: Adjacent pairs that belong to a run, either way round (built once).
_PAIRS = {(r[i], r[i + 1]) for r in _RUNS for i in range(len(r) - 1)}
_PAIRS |= {(b, a) for a, b in _PAIRS}
_LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s",
                       "5": "s", "7": "t", "+": "t", "8": "b", "9": "g", "|": "l"})


def _class_size(s: str) -> int:
    size = 0
    if re.search(r"[a-z]", s):
        size += 26
    if re.search(r"[A-Z]", s):
        size += 26
    if re.search(r"[0-9]", s):
        size += 10
    if re.search(r"[^A-Za-z0-9]", s):
        size += 33
    return max(size, 1)


def _run_length(s: str) -> int:
    """Characters that belong to a keyboard or alphabet run of 3 or more
    (one pass: each neighbouring pair is looked up)."""
    low = s.lower()
    total, streak = 0, 1
    for i in range(1, len(low)):
        if (low[i - 1], low[i]) in _PAIRS:
            streak += 1
        else:
            if streak >= 3:
                total += streak
            streak = 1
    if streak >= 3:
        total += streak
    return total


def _interleaved_runs(s: str) -> bool:
    """Letters and digits that each make a run when taken apart (a1b2c3,
    1q2w3e, and !Q@W#E with the shift key held): a walk, not a choice."""
    s = s.translate(_UNSHIFT)
    letters = "".join(c for c in s.lower() if c.isalpha())
    digits = "".join(c for c in s if c.isdigit())
    if len(letters) < 3 or len(digits) < 3 or len(letters) + len(digits) < 0.8 * len(s):
        return False
    return _run_length(letters) >= len(letters) - 1 and _run_length(digits) >= len(digits) - 1


def _chars_bits(s: str, classes: Optional[str] = None) -> float:
    """Bits for a string taken character by character, after the guessers'
    tricks. `classes`: the whole passphrase, whose character classes count
    (a chunk of one class inside a mixed passphrase is not a choice of 95)."""
    if not s:
        return 0.0
    per = math.log2(_class_size(classes if classes is not None else s))
    bits = 0.0
    prev = None
    repeats = 0
    for ch in s:
        if ch == prev:
            repeats += 1
            bits += 1.0                     # a repeated character is nearly free
        else:
            bits += per
        prev = ch
    run = _run_length(s)
    bits -= run * per * 0.75
    return max(bits, 0.0)


#: One word from a large list (a guesser's dictionary of about 6,000 words).
WORD_BITS = 12.5


def _word_bits(w: str) -> float:
    """A word from a guesser's list: short words are few; a "word" of one or
    two distinct letters, or a run, is no word at all."""
    lw = w.lower()
    if lw in _COMMON_WORDS or len(lw) <= 2:
        return 4.0
    base = 9.0 if len(lw) == 3 else 11.0 if len(lw) == 4 else WORD_BITS
    base += 1.0 if w != lw and w != lw.capitalize() else 0.0
    if len(set(lw)) < 3 or _run_length(lw) >= len(lw) - 1:
        return min(base, _chars_bits(w))
    return min(base, _chars_bits(w))


def _mixed_bits(s: str) -> float:
    """A string without word separators: letter runs of four or more are
    words, the rest goes character by character (classes of the whole
    string); a chunk seen before is free."""
    bits = 0.0
    seen = set()
    for chunk in re.findall(r"[A-Za-z]+|[^A-Za-z]+", s):
        key = chunk.lower()
        if key in seen:
            bits += 1.0
            continue
        seen.add(key)
        if chunk.isalpha() and len(chunk) >= 4:
            bits += min(_chars_bits(chunk, s), _word_bits(chunk))
        else:
            bits += _chars_bits(chunk, s)
    return bits


def _words_bits(words: list[str], seps: int, extras: str) -> float:
    bits = 0.0
    seen = set()
    for w in words:
        lw = w.lower()
        if lw in seen:
            bits += 1.0                     # the same word again adds almost nothing
            continue
        seen.add(lw)
        if lw.isalpha():
            bits += _word_bits(w)
        else:
            bits += _chars_bits(w)
    bits += min(seps, len(words)) * 0.5
    bits += _chars_bits(extras)
    return bits


def _leet_bits(pw: str) -> float:
    """Bits once look-alike digits and symbols are read as letters: each
    letter run of three or more then counts as a word."""
    plain = pw.translate(_LEET)
    bits = 0.0
    seen = set()
    for chunk in re.findall(r"[A-Za-z]+|[^A-Za-z]+", plain):
        key = chunk.lower()
        if key in seen:
            bits += 1.0
            continue
        seen.add(key)
        if chunk.isalpha() and len(chunk) >= 3:
            bits += min(_chars_bits(chunk, pw), _word_bits(chunk)) + 1.0   # +1: which swaps
        else:
            bits += _chars_bits(chunk, pw)
    return bits


def _period(s: str) -> Optional[tuple[str, int]]:
    """(unit, times) when `s` (less a trailing symbol or two) is one short
    unit said again and again, else None."""
    for core in (s, re.sub(r"[\W_]{1,3}$", "", s)):
        n = len(core)
        for p in range(1, n // 2 + 1):
            if all(core[i] == core[i % p] for i in range(p, n)):
                return core[:p], max(2, n // p)
    return None


def _common(s: str) -> bool:
    sq = re.sub(r"[\s\-_.]+", "", s).lower()
    cands = {sq, re.sub(r"\d+$", "", sq), re.sub(r"[\d\W_]+$", "", sq).translate(_LEET),
             sq.translate(_LEET)}
    return any(c in _COMMON for c in cands if c)


def assess(passphrase: str) -> dict:
    """{score 0-4, ok, bits, length, feedback [plain sentences]}."""
    pw = passphrase if isinstance(passphrase, str) else ""
    feedback = []
    if len(pw) > MAX_LENGTH:
        return {"score": 0, "ok": False, "bits": 0.0, "length": len(pw),
                "feedback": ["Use at most %d characters." % MAX_LENGTH], "label": "too long"}
    squashed = re.sub(r"[\s\-_.]+", "", pw).lower()
    tokens = re.findall(r"[A-Za-z]+|[^A-Za-z\s\-_.]+", pw)
    words = [t for t in tokens if t.isalpha() and len(t) >= 2]
    extras = "".join(t for t in tokens if not t.isalpha())
    seps = len(re.findall(r"[\s\-_.]+", pw))
    if len(words) >= 3 and seps >= 2:
        bits = _words_bits(words, seps, extras)
    else:
        bits = _mixed_bits(pw)
    # a second reading with the usual letter swaps undone (Dr4g0n is dragon):
    # the lower of the two counts
    if re.search(r"[A-Za-z]", pw) and pw.translate(_LEET) != pw:
        bits = min(bits, _leet_bits(pw))
    per = _period(re.sub(r"\s+", "", pw))
    if per:
        unit, times = per
        bits = min(bits, _mixed_bits(unit) + math.log2(times) + 1.0)
        feedback.append("Saying the same thing again adds almost nothing.")
    if _common(pw) or (per and _common(per[0])):
        bits = min(bits, 10.0)
        feedback.append("This is one of the first passphrases a guesser tries.")
    if pw and len(set(pw)) <= 2:
        bits = min(bits, 8.0)
        feedback.append("Use more than one or two different characters.")
    if re.search(r"(19|20)\d\d", pw):
        bits -= 4.0
        feedback.append("A year is easy to guess; it adds little.")
    if "friday" in squashed:
        bits -= 6.0
        feedback.append("Leave out the name Friday; guessers try it first.")
    if _interleaved_runs(pw):
        bits = min(bits, 20.0)
        feedback.append("Letters and digits that count up together are easy to guess.")
    if _run_length(pw) >= 4:
        if _run_length(pw) >= len(re.sub(r"\s", "", pw)) - 1:
            bits = min(bits, 16.0)
        feedback.append("Avoid keyboard or alphabet runs such as qwerty or abcd.")
    bits = max(bits, 0.0)
    score = sum(1 for t in _SCORES if bits >= t)
    ok = len(pw) >= MIN_LENGTH and bits >= FLOOR_BITS
    if len(pw) < MIN_LENGTH:
        feedback.insert(0, "Use at least %d characters." % MIN_LENGTH)
    if not ok and len(pw) >= MIN_LENGTH:
        feedback.append("Make it longer: four or five unrelated words with spaces is strong "
                        "and easy to remember.")
    return {"score": score, "ok": ok, "bits": round(bits, 1), "length": len(pw),
            "feedback": feedback,
            "label": ("very weak", "weak", "fair", "strong", "very strong")[score]}


def refusal(passphrase: str):
    """A sentence saying why the passphrase is too weak for a bundle, or None."""
    a = assess(passphrase)
    if a["ok"]:
        return None
    why = " ".join(a["feedback"]) or "It is too easy to guess."
    return "That passphrase is %s. %s" % (a["label"], why)
