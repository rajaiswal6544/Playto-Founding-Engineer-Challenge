# Backend Testing Guide

This file is a practical checklist for validating the Playto payout engine, especially the backend.

The goal is not just "does the server start" but:

- balances stay correct
- holds and releases behave correctly
- idempotency works under retries
- payout states move safely
- async worker behavior is visible
- retries do not corrupt money

## 1. Start the backend stack

Run this from the project root:

```bash
docker-compose up --build db redis backend worker beat
```

What should happen:

- `db` starts successfully
- `redis` starts successfully
- `backend` runs migrations
- `backend` runs `seed_demo_data`
- `backend` listens on `http://localhost:8000`
- `worker` starts Celery
- `beat` starts periodic jobs

Useful logs:

```bash
docker-compose logs -f backend
```

```bash
docker-compose logs -f worker
```

```bash
docker-compose logs -f beat
```

If startup fails, stop here and fix that first.

## 2. Basic health check

Open:

```text
http://localhost:8000/api/v1/dashboard
```

Expected:

- JSON response
- merchant object present
- `available_balance` present
- `held_balance` present
- `recent_ledger_entries` present
- `payout_history` present

This confirms:

- Django is running
- DB is reachable
- seed data exists
- serializers and routing work

## 3. Inspect the seeded merchant

The app defaults to the first merchant if `X-Merchant-Id` is not provided.

You can inspect the database from the backend container:

```bash
docker-compose exec backend sh
```

Then:

```bash
python manage.py shell
```

Inside Django shell:

```python
from payouts.models import Merchant, Payout, LedgerEntry
from payouts.services.balance_service import BalanceService

merchant = Merchant.objects.order_by("id").first()
merchant
BalanceService.get_balances(merchant.id)
Payout.objects.filter(merchant=merchant).values("id", "status", "amount_paise", "retry_count", "next_retry_at")
LedgerEntry.objects.filter(merchant=merchant).values("entry_type", "amount_paise", "reference_type", "reference_id")
```

What to verify:

- there is a merchant
- there are seeded ledger entries
- there is at least one payout
- balances are sensible

## 4. Create a payout manually

Use PowerShell:

```powershell
$headers = @{
  "Content-Type" = "application/json"
  "Idempotency-Key" = "manual-test-1"
}

$body = @{
  amount_paise = 5000
  bank_account_id = "bank_manual_001"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/payouts" -Headers $headers -Body $body
```

Expected:

- response status is success
- a payout is returned
- payout status is `pending`

What this proves:

- API validation works
- transaction completed
- hold entry should have been written
- idempotency key was accepted

## 5. Confirm hold ledger entry was created

Inside Django shell:

```python
from payouts.models import Merchant, Payout, LedgerEntry
from payouts.services.balance_service import BalanceService

merchant = Merchant.objects.order_by("id").first()
payout = Payout.objects.order_by("-id").first()

payout.id, payout.status, payout.amount_paise
LedgerEntry.objects.filter(reference_type="payout", reference_id=str(payout.id)).values("entry_type", "amount_paise")
BalanceService.get_balances(merchant.id)
```

Expected right after creation:

- payout is `pending` or already `processing/completed/failed` if worker picked it up quickly
- there should be at least one `hold` ledger entry for that payout
- held balance should increase if the payout is still unresolved

## 6. Test idempotency

Send the exact same request again with the same `Idempotency-Key`:

```powershell
$headers = @{
  "Content-Type" = "application/json"
  "Idempotency-Key" = "manual-test-1"
}

$body = @{
  amount_paise = 5000
  bank_account_id = "bank_manual_001"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/payouts" -Headers $headers -Body $body
```

Expected:

- same payout id as the first request
- no new payout row
- no extra hold ledger entry

Validate in Django shell:

```python
from payouts.models import Payout, LedgerEntry

Payout.objects.count()
LedgerEntry.objects.filter(entry_type="hold").count()
```

This is one of the most important backend checks.

## 7. Watch async payout processing

The worker should eventually move `pending` payouts forward.

Watch worker logs:

```bash
docker-compose logs -f worker
```

Watch beat logs:

```bash
docker-compose logs -f beat
```

Then refresh:

```text
http://localhost:8000/api/v1/dashboard
```

Expected payout outcomes:

- `completed`
- `failed`
- or temporarily `processing`

Meaning:

- `completed` should have `release` and `debit`
- `failed` should have `release`
- `processing` may later retry if it gets stuck

## 8. Verify ledger correctness after completion or failure

Inside Django shell:

```python
from payouts.models import Payout, LedgerEntry

payout = Payout.objects.order_by("-id").first()
list(
    LedgerEntry.objects
    .filter(reference_type="payout", reference_id=str(payout.id))
    .values("entry_type", "amount_paise", "created_at")
)
```

Expected:

If payout is `completed`:

- one `hold`
- one `release`
- one `debit`

If payout is `failed`:

- one `hold`
- one `release`
- no `debit`

This is the most important financial integrity check.

## 9. Verify balances are still mathematically correct

Inside Django shell:

```python
from payouts.models import Merchant
from payouts.services.balance_service import BalanceService

merchant = Merchant.objects.order_by("id").first()
BalanceService.get_balances(merchant.id)
```

Interpretation:

- `held_balance` should only reflect unresolved holds
- `available_balance` should decrease only after successful debits
- failed payouts should not permanently reduce available balance

## 10. Force-test retry logic

This project simulates retries through the worker system. The easiest way to inspect retry behavior is from Django shell.

Inside Django shell:

```python
from datetime import timedelta
from django.utils import timezone
from payouts.models import Merchant, Payout
from payouts.services.payout_service import PayoutService
from payouts.services.payout_processor import PayoutProcessor
from payouts.services.retry_service import RetryService

merchant = Merchant.objects.order_by("id").first()

result = PayoutService.create_payout(
    merchant_id=merchant.id,
    amount_paise=2000,
    bank_account_id="bank_retry_manual",
    idempotency_key="retry-manual-1",
)

payout_id = result.payload["id"]
PayoutProcessor.process_pending_payout(payout_id, outcome=PayoutProcessor.STUCK)

Payout.objects.filter(pk=payout_id).update(updated_at=timezone.now() - timedelta(seconds=31))
RetryService.schedule_retry(payout_id, now=timezone.now())

payout = Payout.objects.get(pk=payout_id)
payout.status, payout.retry_count, payout.next_retry_at
```

Expected:

- payout remains `processing`
- `retry_count` increments
- `next_retry_at` is set

Then force completion:

```python
Payout.objects.filter(pk=payout_id).update(next_retry_at=timezone.now() - timedelta(seconds=1))
PayoutProcessor.retry_processing_payout(payout_id, outcome=PayoutProcessor.SUCCESS)
Payout.objects.get(pk=payout_id).status
```

Expected:

- payout becomes `completed`

This validates the retry path without waiting around.

## 11. Run the automated backend tests

From the project root:

```bash
docker-compose run --rm backend python manage.py test
```

What these tests currently cover:

- idempotent create returns the same payout
- invalid state transition via `transition_to()` is blocked
- failed payout releases held funds
- retry scheduling and retry completion path
- concurrency case where only one of two simultaneous payouts succeeds

If tests fail, read the failing assertion before changing code.

## 12. Manual concurrency test

The assignment specifically asks for this scenario:

- merchant balance = 10000
- two simultaneous payout requests of 6000
- exactly one succeeds

This is already covered by automated tests, but you can manually inspect the result by running:

```bash
docker-compose run --rm backend python manage.py test payouts.tests.test_services.PayoutConcurrencyTests
```

Expected:

- one payout created
- one request fails with insufficient funds

## 13. Red flags to watch for

If you see any of these, the backend is not financially safe:

- two payouts created from the same idempotency key
- two `hold` entries for one successful request
- `release` without a matching `hold`
- `debit` created for a failed payout
- negative or obviously wrong balances
- payout moving from `completed` back to `pending`
- retries happening endlessly
- worker crashes during payout processing

## 14. Quick backend confidence checklist

Before saying the backend is working, confirm all of these:

- backend starts cleanly
- dashboard endpoint returns valid JSON
- payout creation works
- idempotency returns the same payout
- ledger entries are created correctly
- completed payouts create `debit`
- failed payouts create `release`
- balances remain correct after payout outcomes
- retry path can schedule and recover a stuck payout
- automated tests pass

## 15. Best testing order for beginners

Use this exact order:

1. Start Docker services.
2. Open `/api/v1/dashboard`.
3. Create one payout with `Invoke-RestMethod`.
4. Repeat the same request with the same idempotency key.
5. Inspect payout and ledger rows in Django shell.
6. Watch worker and beat logs.
7. Run `python manage.py test`.
8. Run the concurrency test separately.

If you want, the next useful step would be for me to create a second file like `api_test_examples.md` with copy-paste commands for every endpoint and expected responses. 
