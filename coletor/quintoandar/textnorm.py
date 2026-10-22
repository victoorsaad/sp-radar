"""Shared name normalization so neighborhood/region variants collapse.

'Tatuapé', 'Tatuape', 'Tatuapé ' -> 'TATUAPE'. Used by the pipeline (on ingest),
the one-time backfill, and matches how the API filters/groups.
"""
import unicodedata


def norm_name(s):
    if not s:
        return None
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.upper().split()) or None
