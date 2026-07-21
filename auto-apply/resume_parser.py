"""
Résumé PDF -> text, with a plain-text cache next to the PDF. Re-extraction only
happens when the PDF is newer than the cache, so repeat runs are fast.
"""

import os

from pypdf import PdfReader


def extract_text(pdf_path):
    """Extract all text from a PDF using pypdf."""
    reader = PdfReader(pdf_path)
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts).strip()


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
