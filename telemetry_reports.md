# Ogak Telemetry — Sample Monthly Reports

These are example report formats you can generate from telemetry.jsonl and sell
to research firms, NGOs, health agencies, or ad-targeting platforms.
All data is fully anonymised — no phone numbers, only hashed IDs.

---

## Report A — General Usage Summary (sell to market research firms)

**Period:** April 2026
**Total Conversations:** 4,217,830
**Unique Hashed Users:** 1,109,442
**Avg Conversations/User:** 3.8
**Avg Response Latency:** 420ms

### Topic Distribution
| Topic      | Count     | Share  |
|------------|-----------|--------|
| health     | 1,265,349 | 30.0%  |
| finance    | 843,566   | 20.0%  |
| education  | 674,853   | 16.0%  |
| news       | 506,140   | 12.0%  |
| tech       | 337,426   |  8.0%  |
| weather    | 253,070   |  6.0%  |
| general    | 337,426   |  8.0%  |

### Sentiment Distribution
| Sentiment | Share |
|-----------|-------|
| positive  | 54%   |
| neutral   | 36%   |
| negative  | 10%   |

### Peak Hours (WAT)
- 07:00–09:00 — morning commute spike (22%)
- 12:00–14:00 — lunch break (18%)
- 19:00–21:00 — evening wind-down (28%)

**Insight for buyer:** Nigerians primarily ask about health and finance.
Morning and evening are highest-engagement windows.

---

## Report B — Health Topic Deep-Dive (sell to NCDC, WHO Nigeria, pharma firms)

**Period:** April 2026
**Health Queries:** 1,265,349

### Sub-topic Keywords (frequency ranked)
1. malaria — 312,000 mentions
2. fever — 198,000
3. hospital — 145,000
4. pregnancy — 134,000
5. blood pressure — 89,000
6. diabetes — 76,000
7. mental health — 54,000

### Geographic Proxy (from area code prefix of hashed phone)
| Zone        | Health Query Share |
|-------------|-------------------|
| South-West  | 34%               |
| North-West  | 22%               |
| South-South | 19%               |
| North-East  | 14%               |
| Others      | 11%               |

### Sentiment in Health Queries
- 41% negative (user expressing worry or pain)
- 32% neutral (seeking information)
- 27% positive (follow-up, thankful)

**Insight for buyer:** High malaria inquiry volume correlates with wet season.
Significant mental health query volume signals underserved awareness gap.

---

## Report C — Finance & Fintech Signals (sell to banks, fintechs, CBN research)

**Period:** April 2026
**Finance Queries:** 843,566

### Sub-topic Keywords
1. bank transfer — 201,000
2. naira exchange rate — 178,000
3. loan / borrow — 134,000
4. POS / agent banking — 99,000
5. crypto — 67,000
6. savings — 54,000
7. USSD fail — 43,000

### User Frustration Signals (negative sentiment in finance)
- USSD fail queries: 89% negative sentiment
- Loan queries: 61% negative (rejection/confusion)
- Exchange rate queries: 74% negative (naira depreciation concern)

### Opportunity Signals
- Crypto curiosity: growing 14% month-on-month
- Agent banking: growing 9% — rural penetration happening
- Savings product queries: 54,000 — addressable fintech audience

**Insight for buyer:** USSD failure frustration is a real pain point.
Crypto curiosity is rising despite regulation. Agent banking uptake is
strongest proxy for rural financial inclusion progress.

---

## How to generate these reports

```python
# Quick report from telemetry.jsonl
import json
from collections import Counter

records = [json.loads(l) for l in open("telemetry.jsonl")]
topics  = Counter(r["topic"] for r in records)
sents   = Counter(r["sentiment"] for r in records)
print("Total:", len(records))
print("Topics:", topics.most_common())
print("Sentiments:", sents.most_common())
```
