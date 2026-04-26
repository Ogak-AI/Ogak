# NDPR Compliance Checklist for Ogak Telemetry
# Nigeria Data Protection Regulation (2019) + NDPA (2023)

## What data Ogak collects
- phone_hash: SHA-256 first 16 chars of phone number — NOT reversible, NOT personal data
- user_input: raw message text (see opt-out below)
- ogak_reply: AI reply
- topic, sentiment, latency, timestamp

## Checklist

### Lawful Basis (Section 2.2 NDPR)
- [x] Legitimate interest: aggregated anonymised analytics for public benefit
- [ ] Add consent notice in onboarding reply:
      "Ogak logs anonymised usage for research. Text STOP to opt out."
- [ ] Store opt-out list (hashed phone) and skip telemetry for opted-out users

### Data Minimisation (Section 2.4)
- [x] Phone number is hashed — raw MSISDN never written to disk
- [x] Truncate user_input to 200 chars in telemetry record
- [ ] Consider removing user_input from telemetry entirely if selling to third parties
      (store only topic + sentiment)

### Data Subject Rights (Section 3.1)
- [ ] Publish privacy notice at ogak.ng/privacy (or equivalent URL in SMS welcome)
- [ ] Provide email address for data deletion requests (e.g. privacy@ogak.ng)
- [ ] On deletion request: purge all records matching phone_hash from JSONL + Supabase

### Security (Section 2.6)
- [x] telemetry.jsonl must not be publicly accessible
- [ ] Encrypt telemetry file at rest (use Railway volume with disk encryption)
- [ ] Use HTTPS-only endpoints (Railway provides TLS automatically)
- [ ] Rotate AGGREGATOR_API_KEY every 90 days

### Third-Party Data Sharing
- [ ] Buyers of telemetry reports receive ONLY aggregated statistics — no row-level data
- [ ] Sign Data Processing Agreement (DPA) with each buyer
- [ ] Register as Data Controller with NITDA (mandatory if processing >1000 users)
      URL: https://nitda.gov.ng/data-protection/

### Retention
- [ ] Define retention period in privacy notice (recommended: 12 months)
- [ ] Implement automated purge of JSONL records older than retention period

### Children
- [ ] SMS is available to all phone owners; add age caveat to terms if needed
      (NDPA 2023 Section 34 — children's data requires parental consent)

## Privacy Notice Template (send as first reply to new users)
-----
Hi! I'm Ogak, ur free AI on 55555. I log anon usage stats 4 research.
No personal data stored. Text PRIVACYOFF 2 opt out. Reply HELP 4 info.
-----
(128 chars — fits one SMS)
