"""
security.py — Webhook signature verification.
Validates HMAC-SHA256 signatures from HollaTags / Arkesel to prevent spoofed POSTs.
Set WEBHOOK_SECRET in .env; leave blank to disable (dev mode only).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

from fastapi import HTTPException, Request

logger = logging.getLogger("ogak.security")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # HMAC secret agreed with aggregator


async def verify_webhook_signature(request: Request) -> bytes:
    """
    Read raw body and verify HMAC-SHA256 signature header.
    Returns raw body bytes (caller must re-parse JSON from this).

    CONFIRM WITH AGGREGATOR:
      - Which header carries the signature?      # <-- CONFIRM
      - Is the signature hex or base64?          # <-- CONFIRM
      - Is the secret passed as-is or hashed?   # <-- CONFIRM

    HollaTags likely header: X-HollaTags-Signature   # <-- CONFIRM
    Arkesel likely header:   X-Arkesel-Signature     # <-- CONFIRM
    """
    body = await request.body()

    if not WEBHOOK_SECRET:
        # Secret not configured — skip verification (dev only)
        logger.warning("WEBHOOK_SECRET not set — signature check DISABLED.")
        return body

    # --- HollaTags / Arkesel HMAC-SHA256 verification ---
    # CONFIRM the exact header name with your aggregator:
    sig_header = (
        request.headers.get("X-HollaTags-Signature")   # <-- CONFIRM
        or request.headers.get("X-Arkesel-Signature")  # <-- CONFIRM
        or request.headers.get("X-Signature")          # <-- CONFIRM fallback
    )

    if not sig_header:
        logger.error("Missing signature header on webhook request.")
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig_header.lower()):
        logger.error("Webhook signature mismatch — possible spoofed request.")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return body
