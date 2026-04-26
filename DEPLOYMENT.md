# Ogak — Deployment Guide
## Railway.app (zero-cost, step-by-step)

---

### Prerequisites
- GitHub account (free)
- Railway account at https://railway.app (free tier: 500 hours/month)
- HollaTags / Arkesel account with approved shortcode
- A machine or VPS to run Ollama (Railway free tier does not run GPU workloads)

---

## PART 1 — Deploy FastAPI on Railway

### Step 1: Push code to GitHub
```bash
git init
git add .
git commit -m "Initial Ogak deployment"
git remote add origin https://github.com/YOUR_USERNAME/ogak.git
git push -u origin main
```

### Step 2: Create Railway project
1. Go to https://railway.app → New Project
2. Click "Deploy from GitHub repo"
3. Select your ogak repo
4. Railway auto-detects Python via nixpacks — no Dockerfile needed

### Step 3: Set environment variables in Railway
In your Railway project dashboard → Variables tab, add:

```
AGGREGATOR          = hollatags
AGGREGATOR_API_KEY  = your_real_key_here
AGGREGATOR_SENDER   = OGAK
SHORTCODE           = 55555
LLM_BACKEND         = ollama
OLLAMA_BASE_URL     = https://YOUR_OLLAMA_HOST:11434
LLM_MODEL           = llama3.1:8b
TELEMETRY_LOG_PATH  = /data/telemetry.jsonl
ENV                 = production
```

### Step 4: Add a Railway Volume for persistent telemetry
1. Railway dashboard → your service → Volumes → Add Volume
2. Mount path: /data
3. This persists telemetry.jsonl across deploys

### Step 5: Get your public URL
Railway gives you: https://ogak-production.up.railway.app
Your webhook URL is: https://ogak-production.up.railway.app/webhook

---

## PART 2 — Deploy Ollama (LLM inference)

Railway free tier does not have GPU. Options:

### Option A — Free: Render.com GPU instance (waitlist)
1. https://render.com → New → Web Service
2. Use Docker image: ollama/ollama
3. After deploy, pull model:
   ```bash
   curl https://YOUR_RENDER_URL/api/pull -d '{"name":"llama3.1:8b"}'
   ```

### Option B — Cheap: Hetzner VPS (€4.51/month, GPU optional)
```bash
# On Hetzner Ubuntu 22.04 VPS:
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
# Start with public access (protect with firewall/auth in production):
OLLAMA_HOST=0.0.0.0 ollama serve
```
Set OLLAMA_BASE_URL=http://YOUR_HETZNER_IP:11434 in Railway.

### Option C — For 300M+ scale: vLLM on GPU cluster
See scaling notes section.

---

## PART 3 — Connect HollaTags / Arkesel

### What to tell HollaTags sales team:
1. "We want a dedicated reverse-billed (toll-free) shortcode for inbound + outbound SMS."
2. "We need a POST webhook to our HTTPS URL when an inbound SMS arrives."
3. "We will call your Send SMS API to reply."
4. Ask them:
   - Exact webhook POST payload format (JSON keys for sender phone and message body)
   - Exact Send SMS endpoint URL and authentication method
   - Pricing per outbound SMS segment (should be zero to end user — you pay corporate rate)

### Webhook configuration (give this to HollaTags):
```
Webhook URL:   https://ogak-production.up.railway.app/webhook
Method:        POST
Content-Type:  application/json
Event:         Inbound SMS on shortcode 55555
```

### Arkesel alternative:
- Dashboard → SMS → Inbound → Configure Webhook
- Same webhook URL as above
- Payload parser already in main.py — confirm keys with Arkesel support

---

## PART 4 — Test Before Going Live

### Local test (no aggregator needed):
```bash
# Terminal 1 — start Ollama
ollama pull llama3.1:8b
ollama serve

# Terminal 2 — start Ogak
pip install -r requirements.txt
cp .env.example .env   # edit .env with your keys
ENV=dev uvicorn main:app --reload

# Terminal 3 — simulate an inbound SMS
curl -X POST http://localhost:8000/test/sms \
  -H "Content-Type: application/json" \
  -d '{"sender": "+2348012345678", "message": "Wetin be the capital of Nigeria?"}'
```

Expected response:
```json
{"reply": "Abuja na the capital of Nigeria. Lagos na d biggest city but not capital.", "length": 75}
```

### Test 140-char enforcement:
```bash
curl -X POST http://localhost:8000/test/sms \
  -H "Content-Type: application/json" \
  -d '{"sender": "+2348012345678", "message": "Write me a very long essay about everything"}'
```
Reply must be ≤140 characters.

### Health check:
```bash
curl http://localhost:8000/health
```

### Simulate aggregator webhook (once you have their payload format):
```bash
# HollaTags example — confirm exact keys with them
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -d '{"from": "+2348012345678", "message": "Who win Super Eagles match?", "shortcode": "55555"}'
```

---

## PART 5 — Scaling to 300 Million Daily Users

### Architecture for 300M/day (~3,500 SMS/second peak)

```
                        ┌─────────────────────────────────┐
 Aggregator POST ──────▶│  Load Balancer (Railway / Nginx) │
                        └────────────┬────────────────────┘
                                     │
               ┌─────────────────────┼────────────────────┐
               ▼                     ▼                    ▼
        [FastAPI pod 1]       [FastAPI pod 2]     [FastAPI pod N]
               │                     │                    │
               └──────────┬──────────┘                    │
                           ▼                               │
                    [Redis Queue]  ◀─────────────────────── ┘
                           │
               ┌───────────┼──────────────┐
               ▼           ▼              ▼
          [vLLM node 1][vLLM node 2][vLLM node N]
          (A100/H100 GPU, 8x parallelism each)
               │
               ▼
          [mem0 + PostgreSQL cluster]
               │
               ▼
          [Telemetry Kafka → Supabase / ClickHouse]
```

### Key scaling switches in .env:
```
LLM_BACKEND=vllm
VLLM_BASE_URL=http://vllm-cluster-lb:8000
LLM_MODEL=llama3.1:70b-instruct   # or n-atlas-v1 quantised
```

### vLLM startup command (per GPU node):
```bash
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --tensor-parallel-size 4 \
  --max-model-len 512 \
  --gpu-memory-utilization 0.90 \
  --disable-log-requests
```

### mem0 at scale:
- Replace ChromaDB with Qdrant cluster or Pinecone
- Add Redis for hot-path caching of frequent phone hashes

### Telemetry at scale:
- Replace JSONL append with Kafka producer
- Consume into ClickHouse for sub-second analytics
- Keep Supabase for dashboard queries

### Cost estimate at 300M/day:
- SMS (reverse billed): ~₦0.80–₦1.50 per message pair = ₦240M–₦450M/day
  → Must be covered by telemetry data revenue
- vLLM GPU cluster (4× A100 80GB): ~$4,000/month AWS/GCP
- Inference cost per message: ~0.3ms × 3,500/s = negligible per-message
