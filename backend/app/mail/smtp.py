"""Sending one message through a mailbox's SMTP: the Proton Bridge on the loopback, Gmail with the app
password, or any server. Called only by the approvals service, only after the owner approved or a standing
allowance covers the recipient."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from ..models import MailAccount
from .imap import sidecar_port

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "bridge", "host.docker.internal")


def _context(host: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if host in LOCAL_HOSTS:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # the Bridge uses a self-signed certificate on the loopback
    return context


def send_message(account: MailAccount, password: str, *, to: str, subject: str, body: str, timeout: int = 30) -> str:
    """Send and return the Message-ID. Raises on any failure; the caller records it."""
    if not account.smtp_host or not account.smtp_port:
        raise RuntimeError(f"{account.label} has no SMTP server set")
    message = EmailMessage()
    message["From"] = account.username
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=account.username.split("@")[-1] if "@" in account.username else None)
    message.set_content(body)
    port = sidecar_port(account.smtp_host, account.smtp_port)
    if account.smtp_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(account.smtp_host, port, context=_context(account.smtp_host), timeout=timeout)
    else:
        server = smtplib.SMTP(account.smtp_host, port, timeout=timeout)
    with server:
        if not account.smtp_ssl and account.smtp_starttls:
            server.starttls(context=_context(account.smtp_host))
        server.login(account.username, password)
        server.send_message(message)
    return str(message["Message-ID"])
