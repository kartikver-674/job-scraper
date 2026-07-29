"""HTTP + text helpers shared by every free source adapter. stdlib only.

Anything that needs a paid Apify actor stays in scraper.py; everything in
sources/ is free and unauthenticated.
"""
import html
import http.client
import json
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (compatible; job-scraper)"
_TAG_RE = re.compile(r"<[^>]+>")


def get_bytes(url, timeout=25, retries=2):
    """GET with a BOUNDED timeout and backoff on transient failures.

    Bounded is the point: an unbounded read is exactly how the Apify path used
    to wedge a whole sweep (see the note in scraper.scrape_search). 4xx is not
    retried — a dead board token should fail on the first try, not the third.
    """
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        # A truncated body is transient and is NOT an OSError, so without this it
        # escaped the retry loop entirely and killed the fetch (seen on ~200KB+
        # JD pages). http.client.HTTPException is the base of IncompleteRead.
        except http.client.HTTPException as exc:
            last = exc
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise last


def get_json(url, **kw):
    return json.loads(get_bytes(url, **kw).decode("utf-8", "replace"))


def get_xml(url, **kw):
    return ET.fromstring(get_bytes(url, **kw))


def strip_html(s):
    """Unescape entities FIRST (Greenhouse returns entity-encoded markup), then
    drop tags and collapse whitespace."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html.unescape(s or ""))).strip()


def dig(obj, path):
    """Walk a dotted path through nested dicts; None if any hop is missing.

    "location.name" -> obj["location"]["name"]. This is what lets an ATS
    adapter be a dict of field -> path instead of a function.
    """
    if not path:
        return None
    for hop in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(hop)
        if obj is None:
            return None
    return obj


def flat(value):
    """Any JSON value -> readable string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "yes" if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(flat(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        for k in ("name", "label", "text", "fullLocation", "value"):
            if k in value:
                return flat(value[k])
    return str(value)
