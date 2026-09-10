"""
Résumé PDF -> text, with a plain-text cache next to the PDF. Re-extraction only
happens when the PDF is newer than the cache, so repeat runs are fast.
"""

import os
import re
import unicodedata

from pypdf import PdfReader

# Characters a PDF text layer carries that no downstream matcher expects.
# Zero-width joiners and soft hyphens sit INSIDE words, so a term carrying
# one never matches the same term without it.
_INVISIBLE = re.compile("[\u00ad\u200b\u200c\u200d\ufeff]")


def normalise(text):
    """PDF text as the rest of the system assumes text looks like.

    NFKC because exporters emit typographic ligatures: Chrome renders
    "airflow" as "air\ufb02ow" and "snowflake" as "snow\ufb02ake" with a
    single ﬂ glyph, and every exact match downstream then misses. The
    benchmark corpus in bench/ hit this on two of eight résumés before a
    model was involved at all.

    NFKC and not NFKD-plus-strip-combining: that pair is right for the
    profile NAME slug, where "María" must become a filename, and wrong for
    body text, where flattening every accent corrupts employers and
    institutions that legitimately carry them.
    """
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))


def extract_text(pdf_path):
    """Extract all text from a PDF using pypdf, normalised."""
    reader = PdfReader(pdf_path)
    parts = [page.extract_text() or "" for page in reader.pages]
    return normalise("\n".join(parts)).strip()


def load_resume(pdf_path, cache_path, extractor=extract_text):
    """Return résumé text, using cache_path when it's at least as new as the PDF."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"Résumé not found at {pdf_path}. Place your resume as resume.pdf there."
        )
    if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(pdf_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    text = extractor(pdf_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
