# Explainer

## Why the ledger is modeled this way

The ledger stays append-only and balances are always aggregated in SQL. A payout hold reserves funds immediately. When a payout reaches a terminal state:

- Success writes `release` and `debit`
- Failure writes `release`

That extra `release` on success is intentional. It clears the held balance while preserving the final debit, which keeps both balance formulas derivable from raw ledger totals without mutating historical holds.

Payout-backed ledger rows now use an explicit `payout` foreign key instead of generic string references. Conditional unique constraints enforce one `hold`, one `release`, and one `debit` per payout at the database layer.

Ledger writes are funneled through explicit service methods instead of open-ended `LedgerEntry.objects.create()` calls in business code:

- `credit_customer_payment`
- `place_hold`
- `release_hold`
- `capture_debit`

Those services enforce payout-backed sequencing rules such as "release/debit require an existing hold" and "debit is only captured after the hold release has been written."

The assignment deliberately stops short of DB triggers. Normal Django write paths are protected with model validation and append-only save/delete guards, while the database enforces duplicate-row and shape invariants directly.

## Concurrency model

Payout creation wraps the full balance check and hold creation in one transaction and locks the merchant row with `select_for_update()`. That serializes payout creation per merchant and prevents two concurrent requests from both spending the same balance.

## Idempotency model

The `IdempotencyKey` table stores a unique `(merchant, key)` pair, a SHA256 request hash, and a response snapshot. A repeated request with the same merchant and active key returns the exact stored payload and status code instead of creating a new payout.

Expired idempotency keys are retained as tombstones instead of being deleted on read. That keeps late client retries from silently creating a fresh payout after the original key has aged out.

Malformed schema validation errors are intentionally excluded from idempotency replay in this assignment. Idempotency starts after DRF request validation so the persistence scope stays focused on business-valid payout attempts and business-rule failures such as insufficient funds.

## Retry model

The worker and beat split responsibilities:

- `process_pending_payouts` starts new pending payouts
- `schedule_processing_retries` marks stale processing payouts with a future `next_retry_at`
- `process_due_retries` re-attempts due processing payouts

Retries preserve the `processing` state, which avoids introducing an illegal backward transition to `pending`. Staleness is measured with `processing_started_at` rather than `updated_at`, so unrelated model saves do not skew retry timing.

`process_due_retry` also enforces due-ness in the service layer. The scheduler filters due payouts, but the service still rejects early retries so backoff is an invariant rather than just a task convention.

## Thin views and service boundaries

Views only validate request shape and map exceptions to API responses. The business rules live in:

- `balance_service.py`
- `idempotency_service.py`
- `ledger_service.py`
- `payout_service.py`
- `payout_processor.py`
- `retry_service.py`

There is no full authentication flow in this assignment, so `X-Merchant-Id` is required on every API request and acts as a stand-in for authenticated tenant context. The API no longer falls back to an arbitrary merchant record.

## Production tradeoffs beyond assignment scope

For a production system beyond take-home scope, the next hardening steps would typically include stricter tenant/auth boundaries, operational reconciliation tooling, stronger audit logging, and potentially DB-trigger enforcement for append-only tables. Those are intentionally omitted here to keep the design pragmatic and reviewable.

## Frontend assumptions

There is no auth flow in this assignment, so the dashboard targets the seeded merchant by default. The React app polls the dashboard every 5 seconds and submits payouts with a fresh UUID idempotency key per request.
