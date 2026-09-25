"""Beta feedback: one form in the header, one email to the operator.

Received, emailed, discarded. Nothing here writes to disk, the session or a
log line that carries what the visitor typed: the email is the only copy.

The email goes FROM the configured Sweep sender and has the visitor as
REPLY-TO, so the operator can answer with the Reply button. Sending as the
visitor would fail SPF/DMARC on every domain that publishes a policy.

Delivery is Resend's HTTPS API over the standard library. Not SMTP: Render
Free blocks outbound 25/465/587, so the Gmail-SMTP helper in auto-apply/
cannot reach anything from the beta. `send_with_resend` is the only function
that knows which provider this is; swapping it swaps the provider.
"""

import datetime
import html
import json
import os
import re
import urllib.error
import urllib.request

from flask import jsonify, request

from sweep import public

TO_ENV = "SWEEP_FEEDBACK_EMAIL"
FROM_ENV = "SWEEP_FEEDBACK_FROM"
KEY_ENV = "RESEND_API_KEY"

TYPES = {"bug": "Bug", "search": "Search results", "feature": "Feature request",
         "ui": "UI / usability", "other": "Other"}

MAX_EMAIL, MAX_SUBJECT, MIN_FEEDBACK, MAX_FEEDBACK = 254, 150, 10, 5000
# 5,000 characters of JSON-escaped text is ~30 KB at worst; anything much
# bigger is not a feedback form.
MAX_BODY_BYTES = 64 * 1024

# Per IP per hour, and for everyone together: the inbox and the provider's
# free quota are what this protects. The beta code already stands in front.
PER_IP_PER_HOUR, TOTAL_PER_HOUR = 5, 30

FAILED = "Couldn't send your feedback right now. Please try again."
UNAVAILABLE = "Feedback isn't available right now. Please try again later."
LIMITED = "You've sent several messages in the last hour. Please try again later."

# No whitespace, no angle brackets, commas or quotes: one plain address, so
# Reply-To can never become a list or smuggle a display name.
_EMAIL_RE = re.compile(r'[^@\s<>,;"\\]+@[^@\s<>,;"\\]+\.[^@\s<>,;"\\.]{2,}')
_PAGE_RE = re.compile(r"/[A-Za-z0-9._/-]{0,100}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Invalid(ValueError):
    """A field the visitor can fix. The message is ours, safe to show."""


class DeliveryFailed(OSError):
    """The provider refused. The message is a status we wrote, safe to log."""


def one_line(text):
    """Every control character and line break becomes a space, runs of
    space collapse. What a header value needs to be, whatever arrived."""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def _multiline(text):
    """Keep line breaks and tabs, drop every other control character."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c.isprintable() or c in "\n\t").strip()


def validate(form):
    """The fields, cleaned, or Invalid. Server-side and authoritative: the
    browser's own checks are a convenience a script skips."""
    def text(name):
        value = form.get(name, "")
        if not isinstance(value, str):
            raise Invalid(FAILED)
        return value

    email = text("email").strip()
    if not email:
        raise Invalid("Enter your email so I can reply.")
    if (len(email) > MAX_EMAIL or not email.isprintable()
            or not _EMAIL_RE.fullmatch(email)):
        raise Invalid("Enter a valid email address.")
    kind = text("type")
    if kind not in TYPES:
        raise Invalid("Choose what this is about.")
    subject = one_line(text("subject"))
    if len(subject) > MAX_SUBJECT:
        raise Invalid(f"Keep the subject under {MAX_SUBJECT} characters.")
    body = _multiline(text("feedback"))
    if len(body) < MIN_FEEDBACK:
        raise Invalid(f"Tell me a little more — at least {MIN_FEEDBACK} characters.")
    if len(body) > MAX_FEEDBACK:
        raise Invalid(f"Keep your feedback under {MAX_FEEDBACK:,} characters.")
    return {"email": email, "type": TYPES[kind], "subject": subject,
            "feedback": body}


def context(app, form, now=None):
    """What the operator needs to reproduce a report, and nothing else.

    Named facts only, each read from somewhere that cannot hold a visitor's
    data: the path they were on, the stage it belongs to, which kind of
    search they chose, the run's opaque id, their browser, this build. No
    résumé, profile, query, job, key or account — nothing is copied out of
    state wholesale, so a field added to state later cannot leak in here.
    """
    from sweep.logic import PUBLIC_STAGES

    page = form.get("page")
    page = page if isinstance(page, str) and _PAGE_RE.fullmatch(page) else None
    stage = None
    if page:
        try:
            endpoint, _ = app.url_map.bind("sweep").match(page, method="GET")
        except Exception:  # NotFound, MethodNotAllowed, RequestRedirect
            endpoint = None
        stage = next((label for _link, label, members, _facts in PUBLIC_STAGES
                      if endpoint in members), None)

    state = app.state
    if state.get("free_only"):
        mode = "Free"
    elif state.get("allow_partial_paid_sweep"):
        mode = "Partial"
    elif state.get("byok_keys") or state.get("cap_usd") is not None:
        mode = "Paid"
    else:
        mode = "Not chosen yet"

    run_id = public.current_run_id()
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return {
        "Page": page or "unknown",
        "Stage": stage or "—",
        "Sweep mode": mode,
        "Run ID": run_id if isinstance(run_id, str) and _TOKEN_RE.fullmatch(run_id) else "none",
        "Browser": one_line(request.headers.get("User-Agent", ""))[:300] or "unknown",
        "Version": commit[:12] if re.fullmatch(r"[0-9a-f]{7,40}", commit) else "local",
        "Submitted": now.isoformat(timespec="seconds"),
    }


def compose(fields, ctx, to, sender):
    """A provider-neutral message: addresses, subject, text and HTML."""
    subject = f"[Sweep Feedback] {fields['type']}"
    if fields["subject"]:
        subject += f" — {fields['subject']}"
    main = [("Type", fields["type"]), ("User email", fields["email"]),
            ("Subject", fields["subject"] or "(none)"),
            ("Feedback", fields["feedback"])]
    text = "Sweep feedback\n\n"
    text += "\n\n".join(f"{k}:\n{v}" for k, v in main)
    text += "\n\n-------------------------\n\n"
    text += "\n\n".join(f"{k}:\n{v}" for k, v in ctx.items()) + "\n"

    e = html.escape
    rows = "".join(f'<p><b>{e(k)}</b><br><span style="white-space:pre-wrap">'
                   f"{e(v)}</span></p>" for k, v in main)
    meta = "<br>".join(f"{e(k)}: {e(v)}" for k, v in ctx.items())
    body = (f'<div style="font-family:system-ui,sans-serif;font-size:14px">'
            f"<h2>Sweep feedback</h2>{rows}<hr>"
            f'<p style="color:#666;font-size:12px">{meta}</p></div>')
    return {"to": to, "from": sender, "reply_to": fields["email"],
            "subject": one_line(subject), "text": text, "html": body}


def send_with_resend(message, api_key, urlopen=urllib.request.urlopen):
    """POST the message to Resend. Raises on anything but success."""
    req = urllib.request.Request(
        "https://api.resend.com/emails", method="POST",
        data=json.dumps(message).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 # urllib's default agent is blocked by some API edges.
                 "User-Agent": "sweep-feedback/1"})
    try:
        with urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                raise DeliveryFailed(f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        # Its body is the provider's explanation, which can quote the
        # message back. Closed here, and only the status travels on.
        exc.close()
        raise DeliveryFailed(f"HTTP {exc.code}") from None


def register(app):
    """POST /feedback, JSON in and out like /estimate.

    Public mode lists it in PUBLIC_ENDPOINTS, so the gate in front of it is
    the beta code and the same-origin check every other POST gets. Requiring
    a JSON body adds the CORS preflight a cross-site page cannot pass.
    """
    app.send_feedback = send_with_resend
    # DailyLimit is the derivation budget's mechanism — reserve under a lock,
    # release on failure — at an hour's window. Its own message names résumés,
    # so it is never shown here.
    app.feedback_limit = public.DailyLimit(PER_IP_PER_HOUR, TOTAL_PER_HOUR,
                                           window=60 * 60)

    @app.post("/feedback")
    def feedback():
        if (request.content_length or 0) > MAX_BODY_BYTES:
            return jsonify({"error": FAILED}), 413
        form = request.get_json(silent=True)
        if not isinstance(form, dict):
            return jsonify({"error": FAILED}), 400
        # The honeypot: invisible to people, filled in by form bots. They are
        # told it worked, so there is nothing to learn by retrying.
        if form.get("website"):
            return jsonify({"ok": True})
        try:
            fields = validate(form)
        except Invalid as exc:
            return jsonify({"error": str(exc)}), 400

        to, sender, key = (os.environ.get(n, "").strip()
                           for n in (TO_ENV, FROM_ENV, KEY_ENV))
        if not (to and sender and key):
            missing = [n for n, v in ((TO_ENV, to), (FROM_ENV, sender),
                                      (KEY_ENV, key)) if not v]
            app.logger.error("feedback is not configured: %s unset",
                             ", ".join(missing))
            return jsonify({"error": UNAVAILABLE}), 503

        try:
            ticket = app.feedback_limit.reserve(public.client_ip())
        except public.BetaLimited:
            return jsonify({"error": LIMITED}), 429
        try:
            app.send_feedback(compose(fields, context(app, form), to, sender), key)
        except Exception as exc:
            # Our own status line or the class name, never str(exc) of anything
            # else: an error can echo the message it refused, visitor's email
            # included.
            app.feedback_limit.release(ticket)
            app.logger.warning("feedback delivery failed: %s",
                               exc if isinstance(exc, DeliveryFailed)
                               else type(exc).__name__)
            return jsonify({"error": FAILED}), 502
        return jsonify({"ok": True})
