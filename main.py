"""
Ogak — Nigeria's Free SMS AI Assistant
FastAPI backend: receives SMS webhook, queries local LLM, replies via aggregator.

Aggregator support: HollaTags (primary), Arkesel, InfoTek (selectable via env).
LLM backend: Ollama (default) or vLLM (set LLM_BACKEND=vllm).
Memory: mem0 per phone number (hashed for privacy).
Telemetry: JSON append + OpenTelemetry traces + optional Supabase.
New in v1.1: async queue (200 OK instantly), NDPR opt-out, HELP command,
             new-user welcome, HMAC webhook signature verification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from pydantic import BaseModel, Field
from mem0 import Memory

# Local modules
from commands import (
    WELCOME_MSG,
    check_command,
    is_new_user,
    is_opted_out,
    mark_seen,
)
from security import verify_webhook_signature

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("ogak")

# ---------------------------------------------------------------------------
# OpenTelemetry — console exporter by default; swap for OTLP in production
# ---------------------------------------------------------------------------
_provider = TracerProvider()
_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(_provider)
tracer = trace.get_tracer("ogak.sms")

# ---------------------------------------------------------------------------
# Environment (all secrets come from .env / Railway env vars)
# ---------------------------------------------------------------------------
AGGREGATOR          = os.getenv("AGGREGATOR", "hollatags")   # hollatags | arkesel | infotek
AGGREGATOR_API_KEY  = os.getenv("AGGREGATOR_API_KEY", "REPLACE_WITH_YOUR_KEY")
AGGREGATOR_SENDER   = os.getenv("AGGREGATOR_SENDER", "OGAK")  # approved sender ID
SHORTCODE           = os.getenv("SHORTCODE", "55555")

LLM_BACKEND         = os.getenv("LLM_BACKEND", "ollama")     # ollama | vllm
OLLAMA_BASE_URL     = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
VLLM_BASE_URL       = os.getenv("VLLM_BASE_URL", "http://localhost:8000")
LLM_MODEL           = os.getenv("LLM_MODEL", "llama3.1:8b")  # or n-atlas-v1, gemma2:9b

TELEMETRY_LOG_PATH  = os.getenv("TELEMETRY_LOG_PATH", "telemetry.jsonl")
SUPABASE_URL        = os.getenv("SUPABASE_URL", "")           # optional
SUPABASE_KEY        = os.getenv("SUPABASE_KEY", "")           # optional

MAX_SMS_CHARS       = 140   # strict GSM-7 single segment
MAX_RETRIM_ATTEMPTS = 3     # how many times to re-prompt for shorter reply

# mem0 config — uses local ChromaDB + HuggingFace sentence-transformers (no OpenAI needed)
MEM0_CONFIG = {
    "embedder": {
        "provider": "huggingface",          # free, local — no OpenAI API key needed
        "config": {
            "model": "multi-qa-MiniLM-L6-cos-v1"  # lightweight 80MB model, fast on CPU
        },
    },
    "vector_store": {
        "provider": "chroma",               # zero-cost local vector store
        "config": {"collection_name": "ogak_memory", "path": "./ogak_chroma_db"},
    },
}
# To use Redis instead, replace with:
# MEM0_CONFIG = {
#     "vector_store": {"provider": "redis", "config": {"url": os.getenv("REDIS_URL")}}
# }

# ---------------------------------------------------------------------------
# System prompt for Ogak
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """You are Ogak — Nigeria's smartest, wittiest and most truthful AI assistant.
You are helpful, direct, and fun. Speak in simple English or light Pidgin when it fits naturally.
Always keep your reply under 140 characters. Be concise but complete.
Never use emojis in the final message.
Today's date is {current_date}. User is texting from Nigeria."""


# ---------------------------------------------------------------------------
# mem0 Memory — initialised once at startup
# ---------------------------------------------------------------------------
memory: Memory | None = None


# ---------------------------------------------------------------------------
# Async SMS work queue — aggregator gets 200 OK immediately; processing happens
# in background so we never time out the aggregator's webhook retry window.
# ---------------------------------------------------------------------------
_sms_queue: asyncio.Queue = None  # type: ignore[assignment]


async def _queue_worker() -> None:
    """Drain the SMS queue one message at a time."""
    while True:
        sender, message = await _sms_queue.get()
        try:
            await _process_sms(sender, message)
        except Exception as exc:
            logger.error("Queue worker error: %s", exc)
        finally:
            _sms_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global memory, _sms_queue
    logger.info("Ogak starting — initialising mem0 memory store...")
    try:
        memory = Memory.from_config(MEM0_CONFIG)
        logger.info("mem0 ready.")
    except Exception as exc:
        logger.warning("mem0 init failed (%s) — running without memory.", exc)
        memory = None

    _sms_queue = asyncio.Queue(maxsize=10_000)
    asyncio.create_task(_queue_worker())
    logger.info("SMS async queue worker started.")
    yield
    logger.info("Ogak shutting down.")


app = FastAPI(title="Ogak SMS AI", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Privacy helpers
# ---------------------------------------------------------------------------

def phone_hash(phone: str) -> str:
    """SHA-256 of phone number, first 16 hex chars. Used as user_id everywhere."""
    return hashlib.sha256(phone.strip().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# LLM inference — Ollama or vLLM, easily swappable
# ---------------------------------------------------------------------------

def _build_messages(system: str, history: list[dict], user_text: str) -> list[dict]:
    msgs = [{"role": "system", "content": system}]
    # inject last 5 turns from mem0 as assistant/user context
    for turn in history[-5:]:
        msgs.append({"role": "user",      "content": turn.get("input", "")})
        msgs.append({"role": "assistant", "content": turn.get("output", "")})
    msgs.append({"role": "user", "content": user_text})
    return msgs


async def _ollama_generate(messages: list[dict]) -> str:
    """Call local Ollama /api/chat endpoint."""
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model":    LLM_MODEL,
        "messages": messages,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": 80},  # cap tokens for speed + cost
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["message"]["content"].strip()


async def _vllm_generate(messages: list[dict]) -> str:
    """Call local vLLM /v1/chat/completions endpoint (OpenAI-compatible)."""
    url = f"{VLLM_BASE_URL}/v1/chat/completions"
    payload = {
        "model":       LLM_MODEL,
        "messages":    messages,
        "max_tokens":  80,
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()


async def generate_reply(system: str, history: list[dict], user_text: str) -> str:
    """Dispatch to the configured LLM backend."""
    msgs = _build_messages(system, history, user_text)
    if LLM_BACKEND == "vllm":
        return await _vllm_generate(msgs)
    return await _ollama_generate(msgs)


# ---------------------------------------------------------------------------
# 140-char enforcement — trim then re-prompt if needed
# ---------------------------------------------------------------------------

def _strip_emojis(text: str) -> str:
    """Remove any characters outside GSM-7 basic set to avoid Unicode billing."""
    gsm7 = set(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./"
        "0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )
    return "".join(c for c in text if c in gsm7)


def enforce_sms_length(text: str) -> str:
    """Strip emojis and hard-truncate to MAX_SMS_CHARS characters."""
    clean = _strip_emojis(text)
    if len(clean) <= MAX_SMS_CHARS:
        return clean
    # truncate cleanly at word boundary
    truncated = clean[:MAX_SMS_CHARS].rsplit(" ", 1)[0]
    return truncated


async def get_safe_reply(
    system: str, history: list[dict], user_text: str
) -> str:
    """Generate, strip, enforce length. Re-prompt up to MAX_RETRIM_ATTEMPTS times."""
    reply = await generate_reply(system, history, user_text)
    reply = enforce_sms_length(reply)

    for attempt in range(MAX_RETRIM_ATTEMPTS):
        if len(reply) <= MAX_SMS_CHARS:
            break
        # Re-prompt with explicit constraint
        short_prompt = (
            f"{user_text}\n\n"
            f"[Your previous reply was too long. Reply in UNDER {MAX_SMS_CHARS} characters. No emojis.]"
        )
        reply = await generate_reply(system, history, short_prompt)
        reply = enforce_sms_length(reply)
        logger.info("Re-trim attempt %d — length now %d", attempt + 1, len(reply))

    # Final safety hard-cut
    return reply[:MAX_SMS_CHARS]


# ---------------------------------------------------------------------------
# mem0 helpers (async wrappers around sync mem0 API)
# ---------------------------------------------------------------------------

async def recall_history(user_id: str, query: str) -> list[dict]:
    if memory is None:
        return []
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: memory.search(query, user_id=user_id, limit=5)
        )
        # mem0 returns list of memory dicts; extract relevant turns
        return [r.get("memory", {}) for r in (results or []) if isinstance(r.get("memory"), dict)]
    except Exception as exc:
        logger.warning("mem0 recall failed: %s", exc)
        return []


async def store_turn(user_id: str, user_text: str, reply: str) -> None:
    if memory is None:
        return
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: memory.add(
                [
                    {"role": "user",      "content": user_text},
                    {"role": "assistant", "content": reply},
                ],
                user_id=user_id,
            ),
        )
    except Exception as exc:
        logger.warning("mem0 store failed: %s", exc)


# ---------------------------------------------------------------------------
# Telemetry logging — JSON-lines file + optional Supabase insert
# ---------------------------------------------------------------------------

SENTIMENT_KEYWORDS = {
    "positive": ["thanks", "good", "great", "nice", "love", "appreciate", "correct", "yes"],
    "negative": ["bad", "wrong", "useless", "rubbish", "nonsense", "hate", "no", "error"],
}


def _hint_sentiment(text: str) -> str:
    lower = text.lower()
    pos = sum(1 for w in SENTIMENT_KEYWORDS["positive"] if w in lower)
    neg = sum(1 for w in SENTIMENT_KEYWORDS["negative"] if w in lower)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _classify_topic(text: str) -> str:
    """Lightweight keyword-based topic tagging."""
    rules = {
        "weather":    ["weather", "rain", "sun", "temperature", "forecast"],
        "health":     ["sick", "doctor", "hospital", "medicine", "health", "malaria"],
        "finance":    ["money", "bank", "transfer", "naira", "price", "pay", "loan"],
        "news":       ["news", "today", "government", "president", "politics"],
        "education":  ["school", "exam", "waec", "jamb", "university", "lesson"],
        "tech":       ["phone", "internet", "data", "app", "computer", "ai"],
        "general":    [],
    }
    lower = text.lower()
    for topic, keywords in rules.items():
        if any(k in lower for k in keywords):
            return topic
    return "general"


async def log_telemetry(
    phone_hash_id: str,
    user_input: str,
    ogak_reply: str,
    latency_ms: int,
) -> None:
    """Append one JSON line to telemetry file. Fire-and-forget Supabase insert."""
    record = {
        "ts":          datetime.now(timezone.utc).isoformat(),
        "phone_hash":  phone_hash_id,
        "topic":       _classify_topic(user_input),
        "sentiment":   _hint_sentiment(user_input),
        "input_len":   len(user_input),
        "reply_len":   len(ogak_reply),
        "latency_ms":  latency_ms,
        # raw text stored only if you opt-in for dataset generation;
        # comment out next two lines for stricter privacy
        "user_input":  user_input[:200],   # cap for storage efficiency
        "ogak_reply":  ogak_reply,
    }
    # JSON-lines append (never loses data even if Supabase is down)
    try:
        with open(TELEMETRY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error("Telemetry file write failed: %s", exc)

    # Optional Supabase insert
    if SUPABASE_URL and SUPABASE_KEY:
        asyncio.create_task(_supabase_insert(record))


async def _supabase_insert(record: dict) -> None:
    url = f"{SUPABASE_URL}/rest/v1/ogak_telemetry"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=minimal",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=record, headers=headers)
    except Exception as exc:
        logger.warning("Supabase insert failed: %s", exc)


# ---------------------------------------------------------------------------
# Aggregator — Send SMS reply
# ---------------------------------------------------------------------------

async def send_sms_hollatags(to: str, message: str) -> None:
    """
    HollaTags Send SMS API.
    CONFIRM WITH HOLLATAGS SALES: exact endpoint URL, payload keys, auth header.
    Placeholders marked with  # <-- CONFIRM
    """
    url = "https://api.hollatags.com/sms/send"  # <-- CONFIRM with HollaTags
    headers = {
        "Authorization": f"Bearer {AGGREGATOR_API_KEY}",  # <-- CONFIRM auth scheme
        "Content-Type":  "application/json",
    }
    payload = {
        "to":      to,          # <-- CONFIRM key name
        "from":    AGGREGATOR_SENDER,  # <-- CONFIRM key name
        "message": message,     # <-- CONFIRM key name
        # "shortcode": SHORTCODE,  # uncomment if HollaTags requires shortcode field
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        logger.info("HollaTags send OK — to=%s status=%s", to[-4:], resp.status_code)


async def send_sms_arkesel(to: str, message: str) -> None:
    """
    Arkesel SMS API v2.
    CONFIRM WITH ARKESEL: endpoint, payload structure, api-key header name.
    """
    url = "https://sms.arkesel.com/api/v2/sms/send"  # <-- CONFIRM
    headers = {
        "api-key": AGGREGATOR_API_KEY,  # <-- CONFIRM header name
    }
    payload = {
        "sender":    AGGREGATOR_SENDER,  # <-- CONFIRM
        "message":   message,            # <-- CONFIRM
        "recipients": [to],              # <-- CONFIRM (list or string?)
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        logger.info("Arkesel send OK — to=%s status=%s", to[-4:], resp.status_code)


async def send_sms_infotek(to: str, message: str) -> None:
    """
    InfoTek Nigeria SMS API.
    CONFIRM WITH INFOTEK: exact endpoint, auth, payload keys.
    """
    url = "https://api.infotek.ng/sms"  # <-- CONFIRM
    payload = {
        "apikey":    AGGREGATOR_API_KEY,  # <-- CONFIRM
        "sender_id": AGGREGATOR_SENDER,   # <-- CONFIRM
        "to":        to,                  # <-- CONFIRM
        "message":   message,             # <-- CONFIRM
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        logger.info("InfoTek send OK — to=%s status=%s", to[-4:], resp.status_code)


AGGREGATOR_DISPATCH = {
    "hollatags": send_sms_hollatags,
    "arkesel":   send_sms_arkesel,
    "infotek":   send_sms_infotek,
}


async def send_reply(to: str, message: str) -> None:
    fn = AGGREGATOR_DISPATCH.get(AGGREGATOR.lower())
    if fn is None:
        raise ValueError(f"Unknown aggregator: {AGGREGATOR}")
    await fn(to, message)


# ---------------------------------------------------------------------------
# Webhook payload models — each aggregator has its own format
# We normalise to a common InboundSMS struct.
# ---------------------------------------------------------------------------

class InboundSMS(BaseModel):
    sender:  str   # originating MSISDN e.g. +2348012345678
    message: str   # raw text body


def parse_hollatags(data: dict) -> InboundSMS:
    """
    HollaTags inbound webhook payload normaliser.
    CONFIRM WITH HOLLATAGS: exact JSON keys they POST to your /webhook.
    """
    return InboundSMS(
        sender=data.get("from") or data.get("sender") or data["msisdn"],  # <-- CONFIRM key
        message=data.get("message") or data.get("text") or data["msg"],   # <-- CONFIRM key
    )


def parse_arkesel(data: dict) -> InboundSMS:
    """
    Arkesel inbound webhook normaliser.
    CONFIRM WITH ARKESEL: exact keys in their delivery POST.
    """
    return InboundSMS(
        sender=data.get("sender") or data["from"],     # <-- CONFIRM
        message=data.get("message") or data["text"],   # <-- CONFIRM
    )


def parse_infotek(data: dict) -> InboundSMS:
    """
    InfoTek inbound webhook normaliser.
    CONFIRM WITH INFOTEK: exact keys.
    """
    return InboundSMS(
        sender=data.get("mobile") or data["from"],     # <-- CONFIRM
        message=data.get("message") or data["text"],   # <-- CONFIRM
    )


PARSER_DISPATCH = {
    "hollatags": parse_hollatags,
    "arkesel":   parse_arkesel,
    "infotek":   parse_infotek,
}


# ---------------------------------------------------------------------------
# Core SMS handler — the brain of Ogak
# ---------------------------------------------------------------------------

async def _process_sms(sender: str, message: str) -> None:
    """
    Full pipeline (runs in background queue):
    opt-out guard → command check → new-user welcome → LLM → telemetry → send.
    Telemetry logged BEFORE send so data is never lost.
    """
    with tracer.start_as_current_span("process_sms") as span:
        t_start = time.monotonic()
        uid = phone_hash(sender)
        span.set_attribute("user_id", uid)
        span.set_attribute("input_length", len(message))

        opted_out = is_opted_out(sender)

        # ── 1. Special commands (STOP / START / HELP) ───────────────────────
        cmd_reply = check_command(sender, message)
        if cmd_reply is not None:
            # Telemetry skipped if user opted out
            if not opted_out:
                await log_telemetry(uid, message, cmd_reply,
                                    int((time.monotonic() - t_start) * 1000))
            await send_reply(sender, cmd_reply)
            return

        # ── 2. New-user welcome (send BEFORE their first real reply) ────────
        new_user = is_new_user(sender)
        if new_user:
            mark_seen(sender)
            try:
                await send_reply(sender, WELCOME_MSG)
            except Exception as exc:
                logger.warning("Welcome send failed: %s", exc)

        # ── 3. Build system prompt ──────────────────────────────────────────
        system = SYSTEM_PROMPT_TEMPLATE.format(current_date=str(date.today()))

        # ── 4. Memory recall ────────────────────────────────────────────────
        history = await recall_history(uid, message)

        # ── 5. LLM generate ─────────────────────────────────────────────────
        try:
            reply = await get_safe_reply(system, history, message)
        except Exception as exc:
            logger.error("LLM inference error: %s", exc)
            reply = "No wahala, system dey rest small. Try again soon."
            reply = reply[:MAX_SMS_CHARS]

        latency_ms = int((time.monotonic() - t_start) * 1000)
        span.set_attribute("reply_length", len(reply))
        span.set_attribute("latency_ms", latency_ms)

        # ── 6. Telemetry (BEFORE send; skip if opted out) ───────────────────
        if not opted_out:
            await log_telemetry(uid, message, reply, latency_ms)

        # ── 7. Persist to mem0 (non-blocking) ──────────────────────────────
        asyncio.create_task(store_turn(uid, message, reply))

        # ── 8. Send reply ───────────────────────────────────────────────────
        try:
            await send_reply(sender, reply)
        except Exception as exc:
            logger.error("SMS send failed to ...%s: %s", sender[-4:], exc)


async def handle_sms(sender: str, message: str) -> str:
    """
    Legacy sync-style helper used by /test/sms endpoint.
    Returns reply string directly (does not send via aggregator).
    """
    uid = phone_hash(sender)
    system = SYSTEM_PROMPT_TEMPLATE.format(current_date=str(date.today()))
    history = await recall_history(uid, message)
    try:
        reply = await get_safe_reply(system, history, message)
    except Exception as exc:
        logger.error("LLM inference error: %s", exc)
        reply = "No wahala, system dey rest small. Try again soon."[:MAX_SMS_CHARS]
    await log_telemetry(uid, message, reply, 0)
    asyncio.create_task(store_turn(uid, message, reply))
    return reply


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def webhook(request: Request) -> Response:
    """
    Receives inbound SMS from aggregator.
    Returns 200 OK immediately; all processing happens in the async queue.
    Aggregator must POST to: https://YOUR_DOMAIN/webhook
    """
    # ── Signature verification (HMAC-SHA256) ─────────────────────────────
    try:
        raw_body = await verify_webhook_signature(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        import json as _json
        data: dict = _json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info("Webhook received: %s", str(data)[:200])

    parser = PARSER_DISPATCH.get(AGGREGATOR.lower())
    if parser is None:
        raise HTTPException(status_code=500, detail=f"No parser for aggregator: {AGGREGATOR}")

    try:
        sms = parser(data)
    except (KeyError, TypeError) as exc:
        logger.error("Payload parse error — data=%s exc=%s", data, exc)
        raise HTTPException(status_code=422, detail=f"Cannot parse payload: {exc}")

    if not sms.message.strip():
        return JSONResponse({"status": "ignored", "reason": "empty message"})

    # ── Enqueue — respond 200 immediately so aggregator never retries ─────
    try:
        _sms_queue.put_nowait((sms.sender, sms.message.strip()))
    except asyncio.QueueFull:
        logger.error("SMS queue full — dropping message from ...%s", sms.sender[-4:])
        return JSONResponse({"status": "overloaded"}, status_code=503)

    return JSONResponse({"status": "queued"})


@app.get("/")
async def root() -> dict:
    """Root endpoint — HuggingFace Spaces health probe."""
    return {"service": "Ogak SMS AI", "status": "alive", "docs": "/docs"}


@app.get("/health")
async def health() -> dict:
    return {
        "status":     "alive",
        "service":    "Ogak SMS AI",
        "aggregator": AGGREGATOR,
        "llm":        f"{LLM_BACKEND}/{LLM_MODEL}",
        "memory":     "mem0" if memory else "disabled",
        "ts":         datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def metrics() -> dict:
    """Simple telemetry summary — last 1000 lines of the JSONL log."""
    lines: list[dict] = []
    try:
        with open(TELEMETRY_LOG_PATH, encoding="utf-8") as f:
            raw = f.readlines()
        lines = [json.loads(l) for l in raw[-1000:] if l.strip()]
    except FileNotFoundError:
        return {"total_requests": 0}

    topics: dict[str, int] = {}
    sentiments: dict[str, int] = {}
    for r in lines:
        topics[r.get("topic", "?")] = topics.get(r.get("topic", "?"), 0) + 1
        sentiments[r.get("sentiment", "?")] = sentiments.get(r.get("sentiment", "?"), 0) + 1

    return {
        "total_requests":  len(lines),
        "topics":          topics,
        "sentiments":      sentiments,
        "avg_latency_ms":  (
            sum(r.get("latency_ms", 0) for r in lines) // max(len(lines), 1)
        ),
    }


# ---------------------------------------------------------------------------
# Dev / local testing helper — simulate an inbound SMS without an aggregator
# ---------------------------------------------------------------------------

class TestSMSRequest(BaseModel):
    sender:  str = Field(default="+2348099999999")
    message: str

@app.post("/test/sms")
async def test_sms(req: TestSMSRequest) -> dict:
    """
    LOCAL TESTING ONLY — remove or add auth before production.
    Send POST {"sender": "+234...", "message": "..."} to simulate an inbound SMS.
    Reply is returned in JSON instead of being sent to the phone.
    """
    if os.getenv("ENV", "dev") != "dev":
        raise HTTPException(status_code=403, detail="Test endpoint disabled in production.")
    reply = await handle_sms(req.sender, req.message)
    return {"reply": reply, "length": len(reply)}
