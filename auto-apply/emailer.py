"""
Compose and send application emails over Gmail SMTP (STARTTLS). Only used on the
--send path. Credentials are passed in from .env by the caller — never stored here.
"""

import mimetypes
import os
import smtplib
from email.message import EmailMessage


def clean_password(pw):
    """Gmail shows app passwords in 4 space-separated groups; SMTP wants them joined."""
    return (pw or "").replace(" ", "")


def build_message(from_addr, to, subject, body, attachment_path=None):
    """Build an EmailMessage, optionally attaching a file (e.g. resume.pdf)."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_path:
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(attachment_path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(attachment_path))
    return msg


def send_message(msg, host, port, user, password):
    """Send via SMTP STARTTLS. Raises on auth/send failure."""
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, clean_password(password))
        server.send_message(msg)
