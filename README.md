---
title: Ogak SMS AI
emoji: 🤖
colorFrom: green
colorTo: gray
sdk: docker
pinned: false
license: mit
app_port: 7860
---

# Ogak — Nigeria's Free SMS AI Assistant


> **Text any question to shortcode 55555. Get a witty, truthful answer instantly. No data, no app, no charge to the user.**

---

## Project Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Nigerian Telco Network                      │
│          User texts 55555 ─── FREE (reverse-billed)              │
└────────────────────────────┬─────────────────────────────────────┘
                             │  POST /webhook (JSON)
                             ▼
               ┌─────────────────────────┐
               │  SMS Aggregator          │
               │  HollaTags / Arkesel /   │
               │  InfoTek                 │
               └────────────┬────────────┘
                             │  HMAC-signed webhook POST
                             ▼
               ┌─────────────────────────┐
               │  FastAPI  (main.py)      │  ← Railway.app / Render
               │  /webhook               │
               │  → signature verify     │
               │  → 200 OK immediately   │
               │  → asyncio.Queue        │
               └────────────┬────────────┘
                             │  background worker
                             ▼
          ┌──────────────────────────────────────┐
          │           _process_sms()             │
          │  1. NDPR opt-out guard               │
          │  2. Special command? (STOP/HELP)      │
          │  3. New-user welcome message          │
          │  4. mem0 memory recall (per hash)     │
          │  5. LLM inference (Ollama / vLLM)    │
          │  6. GSM-7 enforce ≤140 chars         │
          │  7. Telemetry → JSONL + Supabase     │
          │  8. Send reply via aggregator API    │
          └──────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   [Ollama local]    [mem0 + ChromaDB]  [telemetry.jsonl]
   [vLLM cluster]   [Redis at scale]   [Supabase opt.]
```

---

## File Structure

```
ogak/
├── main.py             # FastAPI app — full pipeline
├── commands.py         # STOP / START / HELP command handlers + welcome msg
├── security.py         # HMAC-SHA256 webhook signature verification
├── requirements.txt    # Python dependencies
├── .env.example        # All environment variables (copy to .env)
├── railway.json        # Railway.app deployment config
├── Procfile            # Fallback process definition
├── DEPLOYMENT.md       # Step-by-step Railway + Ollama setup
├── NDPR_COMPLIANCE.md  # Nigeria Data Protection checklist
├── telemetry_reports.md # 3 sample monthly reports to sell
└── README.md           # This file
```

---

## Quick Start (Local Dev)

```bash
# 1. Clone and install
git clone https://github.com/YOUR_USERNAME/ogak.git
cd ogak
pip install -r requirements.txt

# 2. Pull and start Ollama (separate terminal)
ollama pull llama3.1:8b
ollama serve

# 3. Configure env
cp .env.example .env
# Edit .env — set AGGREGATOR_API_KEY and other values

# 4. Start Ogak
ENV=dev uvicorn main:app --reload --port 8000

# 5. Simulate an SMS (no aggregator needed)
curl -X POST http://localhost:8000/test/sms \
  -H "Content-Type: application/json" \
  -d '{"sender": "+2348012345678", "message": "Wetin be the capital of Nigeria?"}'
# → {"reply": "Abuja na the capital. Lagos na the biggest city but not the capital.", "length": 71}

# 6. Test STOP command
curl -X POST http://localhost:8000/test/sms \
  -H "Content-Type: application/json" \
  -d '{"sender": "+2348012345678", "message": "STOP"}'

# 7. Health check
curl http://localhost:8000/health
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AGGREGATOR` | ✅ | `hollatags` | `hollatags` \| `arkesel` \| `infotek` |
| `AGGREGATOR_API_KEY` | ✅ | — | API key from aggregator dashboard |
| `AGGREGATOR_SENDER` | ✅ | `OGAK` | Approved sender ID |
| `SHORTCODE` | ✅ | `55555` | Your dedicated shortcode |
| `WEBHOOK_SECRET` | ✅ prod | — | HMAC secret shared with aggregator |
| `LLM_BACKEND` | — | `ollama` | `ollama` or `vllm` |
| `OLLAMA_BASE_URL` | — | `http://localhost:11434` | Ollama server URL |
| `VLLM_BASE_URL` | — | `http://localhost:8000` | vLLM server URL |
| `LLM_MODEL` | — | `llama3.1:8b` | Model name (must be loaded) |
| `TELEMETRY_LOG_PATH` | — | `telemetry.jsonl` | JSONL log file path |
| `SUPABASE_URL` | — | — | Supabase project URL (optional) |
| `SUPABASE_KEY` | — | — | Supabase anon key (optional) |
| `OPTOUT_FILE` | — | `optout.json` | NDPR opt-out store path |
| `SEEN_USERS_FILE` | — | `seen_users.json` | First-visit tracker path |
| `ENV` | — | `dev` | Set `production` to lock /test/sms |
| `PORT` | — | `8000` | HTTP port (auto-set by Railway) |

---

## SMS Commands (user-facing)

| Text this | What happens |
|-----------|-------------|
| Any question | Ogak answers (≤140 chars, no emojis) |
| `STOP` / `PRIVACYOFF` | Opt out of telemetry logging; still get AI replies |
| `START` / `PRIVACYON` | Opt back in to telemetry |
| `HELP` | Shows command list |

**First message from a new number:** Ogak sends a welcome + privacy notice before the real reply.

---

## Reply Rules (Cost Control)

| Rule | Why |
|------|-----|
| Hard limit: 140 GSM-7 chars | One SMS segment = lowest cost |
| No emojis | Emoji forces Unicode → doubles SMS cost |
| Auto re-prompt if over limit | Up to 3 attempts, then hard-truncate |
| 200 OK to aggregator instantly | No webhook retry floods |

---

## Aggregator Integration

### HollaTags (preferred)
Contact HollaTags sales and ask for:
1. **Reverse-billed (toll-free) shortcode** — users pay zero.
2. **Inbound webhook** — they POST to your URL on every incoming SMS.
3. **Send SMS API** — you call this to reply.

**Give them:**
```
Webhook URL:   https://YOUR_RAILWAY_URL/webhook
Method:        POST
Content-Type:  application/json
Shortcode:     55555
```

**Confirm with HollaTags (update main.py accordingly):**
- Exact JSON keys in their inbound POST (`from` / `sender` / `msisdn`?)  ← CONFIRM
- Exact Send SMS endpoint and auth scheme  ← CONFIRM
- HMAC secret for `X-HollaTags-Signature` header  ← CONFIRM

### Arkesel
- Dashboard → SMS → Inbound → Configure Webhook → paste URL above
- Send API: `POST https://sms.arkesel.com/api/v2/sms/send` with header `api-key`
- Confirm `recipients` is array vs string  ← CONFIRM

### InfoTek Nigeria
- Contact support for webhook setup + API docs  ← CONFIRM all keys

---

## Switching LLM Model

```bash
# In .env — swap model without touching code:
LLM_MODEL=n-atlas-v1        # N-ATLAS (Yoruba/Hausa/Igbo/Pidgin native)
LLM_MODEL=gemma2:9b         # Good Pidgin quality
LLM_MODEL=llama3.1:70b      # Best quality, needs GPU
```

```bash
# Pull new model in Ollama:
ollama pull gemma2:9b
```

---

## Telemetry & Revenue

Every non-opted-out conversation writes one JSON line to `telemetry.jsonl`:

```json
{
  "ts": "2026-04-26T15:00:00Z",
  "phone_hash": "a3f7e2c8b1d94e01",
  "topic": "health",
  "sentiment": "negative",
  "input_len": 42,
  "reply_len": 98,
  "latency_ms": 380,
  "user_input": "How do I treat malaria at home?",
  "ogak_reply": "Drink plenty water, take Coartem if confirmed. See a doctor fast."
}
```

See `telemetry_reports.md` for 3 sample monthly reports (health, finance, general) you can sell to research firms, NGOs, and fintechs.

**Live metrics:** `GET /metrics` returns topic/sentiment breakdown of last 1,000 requests.

---

## Scaling to 300 Million Daily Users

See `DEPLOYMENT.md → PART 5` for full architecture diagram. Key switches:

```bash
# Switch to vLLM cluster:
LLM_BACKEND=vllm
VLLM_BASE_URL=http://vllm-cluster-lb:8000
LLM_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
```

```bash
# vLLM startup (per A100 node):
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --tensor-parallel-size 4 \
  --max-model-len 512 \
  --gpu-memory-utilization 0.90
```

At 300M/day (~3,500 SMS/sec peak), run:
- **N × FastAPI pods** behind a load balancer (Railway scales horizontally)
- **Redis** for shared asyncio queue (replace in-process Queue)
- **vLLM** on A100 cluster (~4 nodes for 3,500 req/s)
- **Kafka → ClickHouse** for telemetry ingestion

---

## NDPR Compliance

See `NDPR_COMPLIANCE.md` for full checklist. Key points:
- Raw phone numbers are **never written to disk** — only SHA-256 first 16 hex chars
- Users who text `STOP` are excluded from all telemetry
- Telemetry buyers get **aggregated stats only** — no row-level data
- Register as Data Controller with NITDA if >1,000 users

---

## Next Steps After Code Generation

1. **Get aggregator quotes** — email HollaTags (hollatags.com) and Arkesel (arkesel.com). Request: reverse-billed shortcode pricing, webhook setup, API docs.
2. **Provision Ollama host** — cheapest option: Hetzner CX52 (€17/month, 16 vCPU). Pull `llama3.1:8b`.
3. **Deploy to Railway** — see `DEPLOYMENT.md → PART 1` (15 minutes).
4. **Set `WEBHOOK_SECRET`** — agree value with aggregator and add to Railway env vars.
5. **Update CONFIRM comments** — paste real JSON keys from aggregator docs into `main.py` parser functions.
6. **Send test SMS** from your own phone to shortcode.
7. **Register with NITDA** as Data Controller (nitda.gov.ng/data-protection/).
8. **Approach first telemetry buyers** — NCDC, WHO Nigeria, Nigerian fintech startups.
