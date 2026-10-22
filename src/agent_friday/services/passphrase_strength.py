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

MIN_LENGTH = 12
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
}

_RUNS = ("abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm",
         "01234567890", "1qaz2wsx3edc")


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
    """Characters that belong to a keyboard or alphabet run of 3 or more."""
    low = s.lower()
    hit = [False] * len(low)
    for run in _RUNS:
        for direction in (run, run[::-1]):
            for i in range(len(low) - 2):
                for j in range(len(low), i + 2, -1):
                    if low[i:j] in direction:
                        for k in range(i, j):
                            hit[k] = True
                        break
    return sum(hit)


def _chars_bits(s: str) -> float:
    """Bits for a string taken character by character, after the guessers' tricks."""
    if not s:
        return 0.0
    per = math.log2(_class_size(s))
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
    lw = w.lower()
    if lw in _COMMON_WORDS or len(lw) <= 2:
        return 4.0
    return WORD_BITS + (1.0 if w != lw and w != lw.capitalize() else 0.0)


def _mixed_bits(s: str) -> float:
    """A string without word separators: letter runs of four or more are
    words, the rest goes character by character; a chunk seen before is free."""
    bits = 0.0
    seen = set()
    for chunk in re.findall(r"[A-Za-z]+|[^A-Za-z]+", s):
        key = chunk.lower()
        if key in seen and len(chunk) >= 3:
            bits += 1.0
            continue
        seen.add(key)
        if chunk.isalpha() and len(chunk) >= 4:
            bits += min(_chars_bits(chunk), _word_bits(chunk))
        else:
            bits += _chars_bits(chunk)
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


def assess(passphrase: str) -> dict:
    """{score 0-4, ok, bits, length, feedback [plain sentences]}."""
    pw = passphrase if isinstance(passphrase, str) else ""
    feedback = []
    squashed = re.sub(r"[\s\-_.]+", "", pw).lower()
    tokens = re.findall(r"[A-Za-z]+|[^A-Za-z\s\-_.]+", pw)
    words = [t for t in tokens if t.isalpha() and len(t) >= 2]
    extras = "".join(t for t in tokens if not t.isalpha())
    seps = len(re.findall(r"[\s\-_.]+", pw))
    if len(words) >= 3 and seps >= 2:
        bits = _words_bits(words, seps, extras)
    else:
        bits = _mixed_bits(pw)
    if squashed in _COMMON or re.sub(r"\d+$", "", squashed) in _COMMON:
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
    if _run_length(pw) >= 4:
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
