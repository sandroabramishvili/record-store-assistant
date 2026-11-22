"""Name normalization, so "The Beatles", "beatles " and "BEATLES" compare as equal."""

import re
import unicodedata


def normalize_name(value: str) -> str:
    """Unicode-normalize, case-fold, collapse whitespace, drop a leading "the "."""
    value = unicodedata.normalize('NFKC', value).casefold().strip()
    value = re.sub(r'\s+', ' ', value)
    if value.startswith('the '):
        value = value[4:]
    return value
