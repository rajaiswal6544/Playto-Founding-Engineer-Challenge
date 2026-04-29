# Explainer

This implementation is intentionally narrow: it tries to make payout correctness easy to reason about under concurrency, retries, and duplicate client requests without introducing unnecessary infrastructure or hidden state. The main design choice throughout is that the ledger remains the source of truth for balances, while payout rows track workflow state.

## 1. The Ledger

The ledger is append-only. I did not keep a mutable `available_balance` column on `Merchant`, because that turns every payout path into a cache invalidation problem. Instead, balances are derived from raw credits, debits, holds, and releases.

For payout-backed movements, I used an explicit `payout` foreign key rather than generic string references. That lets the database enforce "at most one hold / release / debit per payout" directly, and it makes payout-linked ledger history much easier to audit.

The exact balance aggregation is:

```python
class BalanceService:
    @staticmethod
    def get_balances(merchant_id: int) -> dict[str, int]:
        totals = LedgerEntry.objects.filter(merchant_id=merchant_id).aggregate(
            credits=Coalesce(
                Sum(
                    Case(
                        When(entry_type=LedgerEntry.EntryType.CREDIT, then=F("amount_paise")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            debits=Coalesce(
                Sum(
                    Case(
                        When(entry_type=LedgerEntry.EntryType.DEBIT, then=F("amount_paise")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            holds=Coalesce(
                Sum(
                    Case(
                        When(entry_type=LedgerEntry.EntryType.HOLD, then=F("amount_paise")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            releases=Coalesce(
                Sum(
                    Case(
                        When(entry_type=LedgerEntry.EntryType.RELEASE, then=F("amount_paise")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        held_balance = totals["holds"] - totals["releases"]
        available_balance = totals["credits"] - totals["debits"] - held_balance
        return {
            "available_balance": int(available_balance),
            "held_balance": int(held_balance),
        }
```

The important accounting choice is that a successful payout writes both `release` and `debit`, not just `debit`. That is what allows held balance to clear without mutating the original `hold` row. A failed payout writes only `release`. The formulas then stay simple:

- `held_balance = holds - releases`
- `available_balance = credits - debits - held_balance`

The ledger shape is constrained in both the model and the database. The most important parts are:

```python
models.UniqueConstraint(
    fields=("payout",),
    condition=models.Q(payout__isnull=False, entry_type="hold"),
    name="uniq_hold_entry_per_payout",
),
models.UniqueConstraint(
    fields=("payout",),
    condition=models.Q(payout__isnull=False, entry_type="debit"),
    name="uniq_debit_entry_per_payout",
),
models.UniqueConstraint(
    fields=("payout",),
    condition=models.Q(payout__isnull=False, entry_type="release"),
    name="uniq_release_entry_per_payout",
),
```

and the append-only guard:

```python
def save(self, *args, **kwargs):
    if not self._state.adding:
        raise ValidationError("Ledger entries are append-only and cannot be updated.")
    self.full_clean()
    super().save(*args, **kwargs)

def delete(self, *args, **kwargs):
    raise ValidationError("Ledger entries are append-only and cannot be deleted.")
```

Why this design:

- It keeps balance derivation auditable from raw movements.
- It avoids mutable "current balance" state drifting away from history.
- It makes payout settlement idempotent at the ledger layer as well as at the API layer.

Alternatives I deliberately did not use:

- A mutable balance column on `Merchant`: simpler to query, but much easier to corrupt under races or partial failures.
- Generic ledger references only: harder to enforce payout-specific invariants.
- DB triggers for sequencing: stronger, but heavier than I wanted for an assignment-sized codebase.

Remaining tradeoff:

- Cross-row sequencing rules like "debit requires prior release" are enforced in application validation, not in the database, because I intentionally stopped short of triggers / stored procedures.

## 2. The Lock

The critical race in this system is two payouts spending the same merchant funds at the same time. The lock is around payout creation, not around asynchronous settlement. Settlement does not create new financial exposure; creation does.

The exact locking code is:

```python
@staticmethod
@transaction.atomic
def create_payout(*, merchant_id: int, amount_paise: int, bank_account_id: str, idempotency_key: str) -> PayoutCreationResult:
    if not idempotency_key:
        raise MissingIdempotencyKeyError("Idempotency-Key header is required.")

    merchant = Merchant.objects.select_for_update().get(pk=merchant_id)
    request_payload = {
        "amount_paise": amount_paise,
        "bank_account_id": bank_account_id,
    }
    existing_record = IdempotencyService.get_key(merchant.id, idempotency_key)
    ...

    balances = BalanceService.get_balances(merchant.id)
    if balances["available_balance"] < amount_paise:
        ...

    payout = Payout.objects.create(
        merchant=merchant,
        bank_account_id=bank_account_id,
        amount_paise=amount_paise,
        status=Payout.Status.PENDING,
    )
    LedgerService.place_hold(payout=payout)
    ...
```

What this does:

- opens one transaction
- locks the merchant row with `select_for_update()`
- checks balance inside the transaction
- creates the payout
- writes the hold before commit

Why I locked the merchant row specifically:

- The business invariant is merchant-scoped spendability.
- Locking individual ledger rows is awkward because balance is an aggregate over many rows, not a single mutable record.
- A merchant-row lock serializes payout creation per merchant cleanly and predictably.

Why the balance check must happen after the lock:

- If the check ran before the lock, two requests could both observe the same available balance and both place holds.
- By doing the read and write under one transaction, the second request sees the first hold before it can create its own.

Tradeoff:

- This reduces concurrency for a single merchant under load, but that is an acceptable trade for a payout engine. I would much rather serialize payout creation per merchant than debug rare overspend bugs in production.

## 3. The Idempotency

Idempotency is merchant-scoped and request-hash aware. A reused key with the same payload replays the original response. A reused key with a different payload returns `409`. Expired keys are retained as tombstones instead of being deleted, so an old client retry cannot silently create a new payout after the original key ages out.

The lookup and replay logic is:

```python
existing_record = IdempotencyService.get_key(merchant.id, idempotency_key)
if existing_record:
    if IdempotencyService.is_expired(existing_record):
        return PayoutCreationResult(
            payload={"idempotency_key": ["This key has expired and cannot be reused for a new payout request."]},
            status_code=409,
            created=False,
        )
    if not IdempotencyService.payload_matches(existing_record, request_payload):
        return PayoutCreationResult(
            payload={"idempotency_key": ["This key has already been used with a different request payload."]},
            status_code=409,
            created=False,
        )
    snapshot = existing_record.response_snapshot
    return PayoutCreationResult(
        payload=snapshot["body"],
        status_code=snapshot["status_code"],
        created=False,
    )
```

The hashing and snapshot code is:

```python
@staticmethod
def normalize_request_payload(request_payload: dict) -> str:
    return json.dumps(request_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

@staticmethod
def hash_request_payload(request_payload: dict) -> str:
    normalized = IdempotencyService.normalize_request_payload(request_payload)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

@staticmethod
def payload_matches(record: IdempotencyKey, request_payload: dict) -> bool:
    request_hash = IdempotencyService.hash_request_payload(request_payload)
    if record.request_hash:
        return record.request_hash == request_hash
    return record.request_snapshot == request_payload

@staticmethod
def create_snapshot(*, merchant_id: int, key: str, request_payload: dict, payload: dict, status_code: int, payout=None) -> IdempotencyKey:
    return IdempotencyKey.objects.create(
        merchant_id=merchant_id,
        key=key,
        payout=payout,
        request_hash=IdempotencyService.hash_request_payload(request_payload),
        request_snapshot=request_payload,
        response_snapshot={
            "status_code": status_code,
            "body": payload,
        },
        expires_at=IdempotencyService.ttl_expiry(),
    )
```

One implementation detail that matters: idempotency starts after DRF schema validation:

```python
serializer = PayoutCreateSerializer(data=request.data)
serializer.is_valid(raise_exception=True)
```

That was deliberate. I did not want to persist and replay malformed envelopes like missing fields or wrong types. The idempotency surface here is "business-valid payout attempt", including insufficient funds, not "every possible bad HTTP payload."

Why this design:

- Merchant-scoping prevents one merchant from colliding with another merchant's key.
- Request hashing prevents a client from reusing a key with a different amount or bank account.
- Response snapshots let the API replay the original business outcome exactly instead of recomputing it.

Alternatives I rejected:

- Key-only uniqueness with no request hash: too easy to replay the wrong request accidentally.
- Deleting expired keys on read: creates a subtle duplicate-payout hazard for late retries.
- Starting idempotency before request validation: broader persistence surface for little business value in this assignment.

Tradeoff:

- Schema-validation failures are not replayed. In a public payment API, I might choose to persist a wider request envelope depending on client contract expectations.

## 4. The State Machine

I kept the persisted payout state machine deliberately small:

- `pending`
- `processing`
- `completed`
- `failed`

There is no separate persisted `stuck` state. "Stuck" is a processor outcome that leaves the payout in `processing`, after which retry scheduling decides what to do next. I found that cleaner than adding a state whose only job is to mean "still not terminal."

The transition guard lives on the model:

```python
ALLOWED_TRANSITIONS = {
    Status.PENDING: {Status.PROCESSING},
    Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
    Status.COMPLETED: set(),
    Status.FAILED: set(),
}

@classmethod
def validate_transition(cls, current_status: str, next_status: str) -> None:
    if next_status not in cls.ALLOWED_TRANSITIONS.get(current_status, set()):
        raise ValidationError(f"Illegal payout state transition: {current_status} -> {next_status}")

def transition_to(self, next_status: str) -> None:
    self.validate_transition(self.status, next_status)
    self.status = next_status
```

I also enforce timing/state consistency on the model:

```python
def clean(self):
    if self.status == self.Status.PENDING and (self.next_retry_at is not None or self.processing_started_at is not None):
        raise ValidationError("Pending payouts cannot have retry scheduling or processing timestamps.")
    if self.status == self.Status.PROCESSING and self.processing_started_at is None:
        raise ValidationError({"processing_started_at": "Processing payouts must track when the current attempt started."})
    if self.status in {self.Status.COMPLETED, self.Status.FAILED} and (
        self.next_retry_at is not None or self.processing_started_at is not None
    ):
        raise ValidationError("Terminal payouts cannot have retry scheduling or processing timestamps.")
```

And the processor uses those transitions explicitly:

```python
def process_pending_locked(payout: Payout, outcome: str | None = None, now=None) -> str:
    if payout.status != Payout.Status.PENDING:
        raise InvalidProcessorStateError(f"Pending payout processor cannot run from state {payout.status}.")
    now = now or timezone.now()
    payout.transition_to(Payout.Status.PROCESSING)
    payout.processing_started_at = now
    payout.next_retry_at = None
    payout.save(update_fields=["status", "processing_started_at", "next_retry_at", "updated_at"])
    return PayoutProcessor._apply_outcome(payout, outcome or PayoutProcessor.simulate_bank_result())
```

Why this design:

- The workflow stays easy to inspect.
- Illegal backward transitions like `processing -> pending` are prevented centrally.
- Retry bookkeeping is tied to state validity, not left as ad hoc task behavior.

Alternatives I rejected:

- A separate persisted `stuck` state: more state without adding real business clarity.
- Allowing retries to reset a payout back to `pending`: that weakens invariants and makes payout history harder to reason about.

Tradeoff:

- The model is intentionally strict. If a future product needed richer intermediary states like `cancelled`, `reversed`, or `bank_acknowledged`, I would expand the machine explicitly rather than weakening these guards.

## 5. The Retry Model

The retry flow is split into three separate responsibilities:

- `process_pending_payouts`: claim newly-created `pending` payouts and start processing
- `schedule_processing_retries`: find stale `processing` payouts and schedule the next attempt
- `process_due_retries`: execute retries once `next_retry_at` is due

The Celery tasks are:

```python
@shared_task(name="payouts.process_pending_payouts")
def process_pending_payouts() -> int:
    processed = 0
    while True:
        with transaction.atomic():
            payout = (
                Payout.objects.select_for_update(skip_locked=True)
                .filter(status=Payout.Status.PENDING)
                .order_by("created_at")
                .first()
            )
            if payout is None:
                break
            PayoutProcessor.process_pending_locked(payout)
            processed += 1
    return processed

@shared_task(name="payouts.schedule_processing_retries")
def schedule_processing_retries() -> int:
    now = timezone.now()
    processed = 0
    while True:
        with transaction.atomic():
            payout = (
                Payout.objects.select_for_update(skip_locked=True)
                .filter(
                    status=Payout.Status.PROCESSING,
                    next_retry_at__isnull=True,
                    processing_started_at__isnull=False,
                    processing_started_at__lte=now - RetryService.STALE_AFTER,
                )
                .order_by("processing_started_at", "id")
                .first()
            )
            if payout is None:
                break
            RetryService.schedule_retry_locked(payout, now=now)
            processed += 1
    return processed
```

The retry service enforces both backoff and due-ness:

```python
class RetryService:
    STALE_AFTER = timedelta(seconds=30)
    BASE_DELAY_SECONDS = 30
    MAX_RETRIES = 3

    @staticmethod
    def next_retry_delay(retry_count: int) -> timedelta:
        return timedelta(seconds=(2 ** retry_count) * RetryService.BASE_DELAY_SECONDS)

    @staticmethod
    def schedule_retry_locked(payout: Payout, now=None) -> Payout | None:
        now = now or timezone.now()
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidRetryStateError(f"Retries can only be scheduled from processing state, not {payout.status}.")
        if payout.next_retry_at:
            return payout
        if payout.processing_started_at is None:
            raise InvalidRetryStateError("Processing payouts must have processing_started_at before retry scheduling.")
        if payout.processing_started_at > now - RetryService.STALE_AFTER:
            return None

        if payout.retry_count >= RetryService.MAX_RETRIES:
            LedgerService.release_hold(payout=payout)
            payout.transition_to(Payout.Status.FAILED)
            payout.processing_started_at = None
            payout.next_retry_at = None
            payout.save(update_fields=["status", "processing_started_at", "next_retry_at", "updated_at"])
            return payout

        payout.next_retry_at = now + RetryService.next_retry_delay(payout.retry_count)
        payout.retry_count += 1
        payout.save(update_fields=["retry_count", "next_retry_at", "updated_at"])
        return payout

    @staticmethod
    def process_due_retry_locked(payout: Payout, outcome: str | None = None, now=None) -> str:
        now = now or timezone.now()
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidRetryStateError(f"Due retries can only run from processing state, not {payout.status}.")
        if payout.next_retry_at is None:
            raise RetryNotDueError("Retry has not been scheduled for this payout.")
        if payout.next_retry_at > now:
            raise RetryNotDueError("Retry is not due yet.")
```

Two details here were important:

1. Retries stay in `processing`. I did not bounce the row back to `pending`.
2. Staleness is measured from `processing_started_at`, not `updated_at`.

Why this design:

- `processing` means "an attempt is in flight or recovering from one," which remains true during retries.
- `skip_locked` lets concurrent workers claim rows safely without double-processing.
- enforcing due-ness in the service layer means the task filter is not the only thing protecting retry timing.

Alternatives I rejected:

- Using `updated_at` as the stale signal: too easy for unrelated saves to distort retry timing.
- Resetting to `pending` for retries: creates illegal-looking workflow regressions and makes dashboards noisier.
- Having beat do the full retry work itself: less separation between scheduling and execution.

Tradeoff:

- This model depends on running both a worker and a beat scheduler. That is operationally heavier than inline processing, but it matches the assignment's asynchronous payout requirement.

## 6. Thin Views / Service Boundaries

The API layer is intentionally narrow. Views resolve merchant context, validate request shape, and delegate business rules to services. They do not contain balance logic, idempotency rules, ledger sequencing, or retry decisions.

The payout create view is:

```python
class PayoutListCreateView(MerchantScopedAPIView):
    def post(self, request):
        merchant = self.get_merchant()
        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = PayoutService.create_payout(
                merchant_id=merchant.id,
                amount_paise=serializer.validated_data["amount_paise"],
                bank_account_id=serializer.validated_data["bank_account_id"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except MissingIdempotencyKeyError as exc:
            raise ValidationError({"idempotency_key": exc.messages})

        return Response(result.payload, status=result.status_code)
```

Merchant scoping is also explicit:

```python
class MerchantScopedAPIView(APIView):
    merchant_header = "X-Merchant-Id"

    def get_merchant(self) -> Merchant:
        merchant_id = self.request.headers.get(self.merchant_header)
        if not merchant_id:
            raise ValidationError({"merchant": [f"{self.merchant_header} header is required."]})
        return get_object_or_404(Merchant, pk=merchant_id)
```

Service responsibilities are split this way:

- `balance_service.py`: ledger aggregation only
- `ledger_service.py`: allowed ledger writes and payout-backed sequencing
- `idempotency_service.py`: hashing, lookup, snapshot persistence
- `payout_service.py`: payout creation transaction and hold placement
- `payout_processor.py`: simulated bank outcome handling
- `retry_service.py`: retry scheduling and due-ness rules

Why this design:

- It keeps the request/response layer easy to review.
- It makes the financially-sensitive paths testable without HTTP.
- It avoids serializer or view classes becoming the accidental home for core business invariants.

Alternative I rejected:

- Putting orchestration in DRF serializers or fat views. That works for smaller CRUD APIs, but this assignment has enough concurrency and workflow logic that I wanted explicit service boundaries.

Tradeoff:

- There are more files than in a minimal Django app. I think that is justified here because payout correctness logic is easier to locate and reason about when it is not buried in view methods.

## 7. AI Audit

One subtle bad suggestion from AI during implementation was to use `updated_at` to decide when a `processing` payout had become stale enough for retry scheduling. On paper that sounds reasonable because `updated_at` already exists, so it looks like the cheap way to avoid adding another timestamp.

The problem is that `updated_at` answers the wrong question. It means "this row changed," not "the current processing attempt began." In this system, `updated_at` can move for reasons that should not reset retry staleness, such as retry bookkeeping itself or any future metadata write. That creates a quiet liveness bug: a genuinely stuck payout can appear fresh and get deferred indefinitely, or at least much later than intended.

The safer correction was:

- add a dedicated `processing_started_at`
- make it required for `processing` payouts
- base stale detection on that field in both the scheduler and the service

The final code that fixed this is:

```python
if self.status == self.Status.PROCESSING and self.processing_started_at is None:
    raise ValidationError({"processing_started_at": "Processing payouts must track when the current attempt started."})
```

```python
if payout.processing_started_at > now - RetryService.STALE_AFTER:
    return None
```

```python
.filter(
    status=Payout.Status.PROCESSING,
    next_retry_at__isnull=True,
    processing_started_at__isnull=False,
    processing_started_at__lte=now - RetryService.STALE_AFTER,
)
```

Why the final approach is safer:

- it ties retry timing to attempt start, not generic row churn
- it keeps scheduler behavior stable even if more fields are added later
- it makes the state machine and retry model line up cleanly

This was exactly the kind of suggestion that can look correct in a code review if you only read the happy path. It is also why I added both model-level timing constraints and service-level due-ness checks instead of trusting a single filter.

## 8. Production Tradeoffs Beyond Scope

A few things are intentionally pragmatic here rather than production-complete:

- Authentication is simulated with `X-Merchant-Id` instead of a real authn/authz model.
- Ledger immutability is protected in normal Django write paths plus DB constraints, but not with database triggers.
- Celery worker / beat deployment is required for the async path to progress beyond `pending`; the code assumes that background infrastructure exists.
- Idempotency snapshots are stored in the primary relational database; for very high scale, I would revisit partitioning / retention / observability around that table.
- There is no reconciliation loop against an external bank or PSP because the assignment uses a simulated processor.
- I did not add cancellation / reversal / refund states because they would expand the state machine and ledger rules materially beyond the assignment.

If I were hardening this past take-home scope, the first additions would be:

- real tenant authentication and authorization
- stronger operational visibility around stuck payouts and retries
- explicit reconciliation tooling
- deeper DB-level protections for append-only financial tables
- clearer deployment automation for web, worker, beat, Redis, and Postgres as one backend stack
