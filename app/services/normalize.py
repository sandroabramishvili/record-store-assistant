"""Name normalization: one comparable key per artist / title.

"The Beatles", "beatles ", "BEATLES" and "Beatles, The" → "beatles"
"Motörhead" → "motorhead",  "Simon & Garfunkel" → "simon and garfunkel"
"Sgt. Pepper's Lonely Hearts Club Band" → "sgt peppers lonely hearts club band"

Used wherever two names must be compared: duplicates in the collection/wantlist,
"don't recommend what the customer owns", wantlist matching, taste score,
and finding catalog titles in the assistant's answer.
The result is a comparison key only; the original spelling is kept for display.
"""

import re
import unicodedata

_APOSTROPHES_AND_DOTS = re.compile(r"['’‘`´.]")  # removed: "pepper's" → "peppers", "r.e.m." → "rem"
_OTHER_PUNCTUATION = re.compile(r'[\W_]+')        # anything not a letter/digit → space
_TRAILING_THE = re.compile(r',\s*the$')            # library style: "beatles, the"


def _strip_latin_accents(value: str) -> str:
    """Remove accents from Latin letters only (ö → o, é → e).

    Marks on other scripts are kept: in Japanese, for example, the mark in が is
    part of the letter, and removing it would turn it into a different one.
    """
    out: list[str] = []
    for ch in unicodedata.normalize('NFKD', value):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue  # accent sitting on a plain Latin letter → drop it
        out.append(ch)
    return unicodedata.normalize('NFC', ''.join(out))


def normalize_name(value: str) -> str:
    value = unicodedata.normalize('NFKC', value)  # unify look-alike characters (full-width, ligatures)
    value = _strip_latin_accents(value).casefold().strip()
    value = _TRAILING_THE.sub('', value)
    value = value.replace('&', ' and ')
    value = _APOSTROPHES_AND_DOTS.sub('', value)
    value = _OTHER_PUNCTUATION.sub(' ', value)
    value = ' '.join(value.split())               # collapse whitespace
    if value.startswith('the ') and len(value) > 4:
        value = value[4:]
    return value
