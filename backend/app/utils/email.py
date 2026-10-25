from __future__ import annotations

import smtplib
import time
from email.message import Message
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

from app.config import get_settings
from app.utils.logging import get_logger, mask_email

logger = get_logger(__name__)

# Port 465 speaks TLS from the first byte; every other port upgrades with STARTTLS.
IMPLICIT_TLS_PORT = 465


def smtp_send(
    *, host: str, port: int, username: str, password: str, msg: Message, timeout: float = 30
) -> None:
    """Deliver one message. Raises smtplib.SMTPException or OSError on failure."""
    server: smtplib.SMTP
    if port == IMPLICIT_TLS_PORT:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    with server:
        if port != IMPLICIT_TLS_PORT:
            server.ehlo()
            server.starttls()
        server.ehlo()
        server.login(username, password)
        # Envelope sender is the authenticated mailbox; From may be an alias.
        server.send_message(msg, from_addr=username)


def stamp(msg: Message, sender: str, name: str) -> None:
    """From, Date and Message-ID: messages without the last two score as spam."""
    msg["From"] = formataddr((name, sender))
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)


RESEND_URL = "https://api.resend.com/emails"
RESEND_ATTEMPTS = 3
RESEND_MAX_WAIT = 10.0


def _send_resend(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None,
    reply_to: str | None,
    extra_headers: dict[str, str] | None,
    idempotency_key: str | None,
) -> bool:
    """Send through Resend's HTTPS API (port 443, which hosts that block SMTP
    still allow). Retries network errors, 429 and 5xx with backoff; any other
    4xx is final."""
    import httpx

    settings = get_settings()
    payload: dict = {
        "from": formataddr((settings.from_name, settings.sender_email)),
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        payload["text"] = text_body
    if reply_to:
        payload["reply_to"] = reply_to
    if extra_headers:
        payload["headers"] = dict(extra_headers)
    headers = {"Authorization": f"Bearer {settings.resend_api_key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]

    for attempt in range(1, RESEND_ATTEMPTS + 1):
        wait = float(2 ** (attempt - 1))
        try:
            resp = httpx.post(RESEND_URL, json=payload, headers=headers, timeout=15)
        except httpx.HTTPError as e:
            # Exception text names the URL only, never the key or recipient.
            logger.warning(f"Resend request failed (attempt {attempt}): {type(e).__name__}")
        else:
            if resp.status_code < 300:
                email_id = (resp.json() or {}).get("id", "?")
                logger.info(f"Email sent to {mask_email(to)} via Resend (id {email_id})")
                return True
            if resp.status_code != 429 and resp.status_code < 500:
                # 4xx: bad key, unverified sender domain, invalid recipient. Not retried.
                # The body names the problem and carries no secrets.
                logger.error(f"Resend rejected the email ({resp.status_code}): {resp.text[:300]}")
                return False
            retry_after = resp.headers.get("retry-after", "")
            if retry_after.replace(".", "", 1).isdigit():
                wait = float(retry_after)
            logger.warning(f"Resend returned {resp.status_code} (attempt {attempt})")
        if attempt < RESEND_ATTEMPTS:
            time.sleep(min(wait, RESEND_MAX_WAIT))
    logger.error(f"Resend: giving up on the email to {mask_email(to)}")
    return False


def send_email_sync(
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    reply_to: str | None = None,
    extra_headers: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """
    Send an email through Resend or SMTP (EMAIL_PROVIDER), synchronously.
    Called from async code via asyncio.to_thread() to avoid blocking the event loop.
    """
    settings = get_settings()

    if settings.email_transport == "resend":
        if not settings.email_configured:
            logger.warning("RESEND_API_KEY or FROM_EMAIL not configured: email not sent")
            return False
        return _send_resend(
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            reply_to=reply_to or settings.reply_to_email or None,
            extra_headers=extra_headers,
            idempotency_key=idempotency_key,
        )

    if not (settings.smtp_host and settings.smtp_password):
        logger.warning("SMTP_HOST or SMTP_PASSWORD not configured: email not sent")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        stamp(msg, settings.sender_email, settings.from_name)
        msg["To"] = to
        reply = reply_to or settings.reply_to_email
        if reply:
            msg["Reply-To"] = reply
        for header, value in (extra_headers or {}).items():
            msg[header] = value

        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        smtp_send(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            msg=msg,
        )

        # #198: no email addresses in log lines — the recipient is hashed, and
        # the subject is dropped (it can carry digest content details).
        logger.info(f"Email sent to {mask_email(to)}")
        return True

    except smtplib.SMTPAuthenticationError:
        # The exception text can carry the account email — do not log it (#198).
        logger.error("SMTP authentication failed")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending to {mask_email(to)}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email to {mask_email(to)}: {e}")
        return False


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    reply_to: str | None = None,
    extra_headers: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> bool:
    """
    Async wrapper — runs the blocking send in a thread pool so the event
    loop is never blocked.
    """
    import asyncio

    ok = await asyncio.to_thread(
        send_email_sync,
        to,
        subject,
        html_body,
        text_body,
        reply_to,
        extra_headers,
        idempotency_key,
    )
    # Track delivery success/failure for the deliverability metric (audit 17-6).
    try:
        from app.utils.cache import get_redis

        await get_redis().incr("email:delivered" if ok else "email:failed")
    except Exception:  # pragma: no cover - metrics must never break sending
        pass
    return ok
