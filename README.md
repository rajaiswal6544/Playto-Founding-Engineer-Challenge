# Playto Pay Payout Engine Simulation

Production-style payout engine simulation built with Django, Django REST Framework, PostgreSQL, Celery, Redis, React, and Tailwind CSS.

## What it does

- Holds merchant funds atomically during payout creation.
- Enforces merchant-scoped idempotency keys.
- Derives balances from an append-only ledger.
- Processes payouts asynchronously with success, failure, and stuck outcomes.
- Retries stuck payouts with exponential backoff.
- Exposes a polling dashboard for balances, ledger history, and payout status.

## Quick start

1. Copy `.env.example` to `.env` if you want to override defaults.
2. Run `docker-compose up --build`.
3. Open `http://localhost:3000` for the React dashboard.
4. Open `http://localhost:8000/api/v1/dashboard` for the API dashboard payload.

## Core API

- `GET /api/v1/dashboard`
- `GET /api/v1/payouts`
- `POST /api/v1/payouts`

`POST /api/v1/payouts` requires an `Idempotency-Key` header and a JSON body:

```json
{
  "amount_paise": 5000,
  "bank_account_id": "bank_001"
}
```

## Merchant selection

Authentication is intentionally omitted for this simulation. The API still requires an explicit merchant context:

- `X-Merchant-Id` request header, which is required on every request and simulates authenticated tenant context for the assignment

## Running tests

Run backend tests in the backend container:

```bash
docker-compose run --rm backend python manage.py test
```

## Seed data

The backend container runs `python manage.py seed_demo_data` on startup. It creates:

- One merchant
- One initial credit ledger entry
- One completed payout
- One failed payout
- One pending payout

## Design notes

- Money is stored only as paise in `BigIntegerField`.
- The ledger is append-only in normal Django write paths, with DB constraints preventing duplicate payout-backed hold/debit/release rows.
- Ledger writes in business code go through explicit service methods for customer-payment credits, hold placement, hold release, and debit capture.
- Payout creation locks the merchant row with `SELECT FOR UPDATE`.
- Idempotent retries bind a request hash, retain expired keys as tombstones, and return the original business response snapshot while active.
- Successful terminal settlement writes both `release` and `debit` entries so held balance clears while the debit permanently reduces funds.

More detail lives in [EXPLAINER.md](EXPLAINER.md).
