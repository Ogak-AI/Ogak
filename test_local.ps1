#!/usr/bin/env pwsh
# ============================================================
# Ogak — Local Production Test Script (PowerShell)
# Run from: C:\Users\USER\Documents\Ogak\
# Prerequisites: Python 3.11+, Ollama installed
# ============================================================

Write-Host "`n=== STEP 1: Install dependencies ===" -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "`n=== STEP 2: Syntax check all files ===" -ForegroundColor Cyan
python -c "
import ast
for f in ['main.py','commands.py','security.py']:
    try:
        ast.parse(open(f).read())
        print(f'  OK  {f}')
    except SyntaxError as e:
        print(f'  ERR {f}: {e}')
"

Write-Host "`n=== STEP 3: Pull LLM model (llama3.1:8b) ===" -ForegroundColor Cyan
Write-Host "  (Skip if already pulled — takes ~5GB download first time)"
# Uncomment to pull:
# ollama pull llama3.1:8b

Write-Host "`n=== STEP 4: Start Ollama in background ===" -ForegroundColor Cyan
Write-Host "  Run this in a SEPARATE PowerShell window:"
Write-Host "  > ollama serve" -ForegroundColor Yellow

Write-Host "`n=== STEP 5: Set env and start FastAPI ===" -ForegroundColor Cyan
Write-Host "  Run this in a SEPARATE PowerShell window:"
Write-Host "  > cd C:\Users\USER\Documents\Ogak" -ForegroundColor Yellow
Write-Host "  > `$env:ENV='dev'; `$env:LLM_BACKEND='ollama'; `$env:LLM_MODEL='llama3.1:8b'; uvicorn main:app --reload --port 8000" -ForegroundColor Yellow

Write-Host "`n=== STEP 6: Run all tests (wait for server to start first) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 5

# --- Health check ---
Write-Host "`n[TEST 1] Health check" -ForegroundColor Green
try {
    $h = Invoke-RestMethod -Uri "http://localhost:8000/health" -Method GET
    Write-Host "  Status : $($h.status)"
    Write-Host "  LLM    : $($h.llm)"
    Write-Host "  Memory : $($h.memory)"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- Basic question ---
Write-Host "`n[TEST 2] Basic question (English)" -ForegroundColor Green
try {
    $body = '{"sender":"+2348012345678","message":"What is the capital of Nigeria?"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/test/sms" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Reply  : $($r.reply)"
    Write-Host "  Length : $($r.length) chars"
    if ($r.length -le 140) { Write-Host "  CHECK  : <= 140 chars PASS" -ForegroundColor Green }
    else { Write-Host "  CHECK  : OVER 140 chars FAIL" -ForegroundColor Red }
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- Pidgin question ---
Write-Host "`n[TEST 3] Pidgin question" -ForegroundColor Green
try {
    $body = '{"sender":"+2348099887766","message":"Wetin cause inflation for Nigeria?"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/test/sms" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Reply  : $($r.reply)"
    Write-Host "  Length : $($r.length) chars"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- STOP command ---
Write-Host "`n[TEST 4] STOP command (opt-out)" -ForegroundColor Green
try {
    $body = '{"sender":"+2348012345678","message":"STOP"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/test/sms" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Reply  : $($r.reply)"
    Write-Host "  Length : $($r.length) chars"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- HELP command ---
Write-Host "`n[TEST 5] HELP command" -ForegroundColor Green
try {
    $body = '{"sender":"+2348011112222","message":"HELP"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/test/sms" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Reply  : $($r.reply)"
    Write-Host "  Length : $($r.length) chars"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- Long message enforcement ---
Write-Host "`n[TEST 6] 140-char enforcement (long prompt)" -ForegroundColor Green
try {
    $body = '{"sender":"+2348055554444","message":"Write me a very detailed essay about the history of Nigeria from 1914 to present day covering all major events"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/test/sms" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Reply  : $($r.reply)"
    Write-Host "  Length : $($r.length) chars"
    if ($r.length -le 140) { Write-Host "  CHECK  : Truncation PASS" -ForegroundColor Green }
    else { Write-Host "  CHECK  : Truncation FAIL" -ForegroundColor Red }
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- Metrics endpoint ---
Write-Host "`n[TEST 7] Metrics endpoint" -ForegroundColor Green
try {
    $m = Invoke-RestMethod -Uri "http://localhost:8000/metrics" -Method GET
    Write-Host "  Total requests: $($m.total_requests)"
    Write-Host "  Topics        : $($m.topics | ConvertTo-Json -Compress)"
} catch { Write-Host "  FAILED: $_" -ForegroundColor Red }

# --- Simulated webhook (HollaTags format) ---
Write-Host "`n[TEST 8] Simulated HollaTags webhook POST" -ForegroundColor Green
try {
    $body = '{"from":"+2348012345678","message":"Who be Wizkid?","shortcode":"55555"}'
    $r = Invoke-RestMethod -Uri "http://localhost:8000/webhook" -Method POST -Body $body -ContentType "application/json"
    Write-Host "  Response: $($r | ConvertTo-Json -Compress)"
    Write-Host "  (Reply queued — check server logs for actual LLM output)"
} catch { Write-Host "  FAILED (expected if WEBHOOK_SECRET set): $_" -ForegroundColor Yellow }

Write-Host "`n=== ALL TESTS COMPLETE ===" -ForegroundColor Cyan
Write-Host "Review telemetry.jsonl for logged conversations:"
Write-Host "> Get-Content telemetry.jsonl | ConvertFrom-Json" -ForegroundColor Yellow
