"""'Test' buttons on the setup screen (#220).

Each returns (ok, message). Messages are written here, never copied from a
provider or SMTP error, so a key, password or username can't leak back.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText

import httpx

from app.config import get_settings
from app.utils.logging import get_logger, mask_email

logger = get_logger(__name__)

# Tests swap in an httpx.MockTransport.
_TRANSPORT: httpx.AsyncBaseTransport | None = None


async def check_llm(*, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    if not api_key:
        return False, "No API key entered."
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 5,
    }
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=False, transport=_TRANSPORT
        ) as client:
            resp = await client.post(
                url, json=payload, headers={"Authorization": f"Bearer {api_key}"}
            )
    except httpx.HTTPError:
        return False, f"Couldn't reach {base_url}."
    code = resp.status_code
    if code == 200:
        return True, f"Connected: {model} answered."
    if code in (401, 403):
        return False, "The provider rejected the API key."
    if code == 404 or (code == 400 and "model" in resp.text.lower()):
        return False, f"The provider doesn't serve the model {model!r}."
    if code == 429:
        return False, "The key works but is rate-limited or out of quota right now."
    return False, f"The provider answered HTTP {code}."


def _send_smtp(*, host: str, port: int, username: str, password: str, to: str) -> tuple[bool, str]:
    from app.utils.email import IMPLICIT_TLS_PORT, smtp_send, stamp

    s = get_settings()
    msg = MIMEText("This is a test from your Weirling setup screen. Email delivery works.", "plain")
    msg["Subject"] = "Weirling test email"
    # The From the app will really use, so a provider that refuses it fails here.
    stamp(msg, s.from_email or username, s.from_name)
    msg["To"] = to
    try:
        smtp_send(host=host, port=port, username=username, password=password, msg=msg, timeout=20)
    except smtplib.SMTPAuthenticationError:
        return False, "The SMTP server rejected the username or password."
    except (smtplib.SMTPNotSupportedError, smtplib.SMTPHeloError):
        other = 587 if port == IMPLICIT_TLS_PORT else IMPLICIT_TLS_PORT
        return False, f"The SMTP server didn't accept TLS on port {port} (try {other})."
    except smtplib.SMTPSenderRefused:
        return False, "The SMTP server won't send as the From address (check FROM_EMAIL)."
    except smtplib.SMTPException:
        return False, "The SMTP server refused the message."
    except OSError:
        return False, f"Couldn't connect to {host}:{port}."
    logger.info(f"Setup test email sent to {mask_email(to)}")
    return True, f"Sent a test email to {to}."


async def check_smtp(
    *, host: str, port: int, username: str, password: str, to: str
) -> tuple[bool, str]:
    if not (host and username and password):
        return False, "Enter the SMTP host, username and password."
    return await asyncio.to_thread(
        _send_smtp, host=host, port=port, username=username, password=password, to=to
    )
