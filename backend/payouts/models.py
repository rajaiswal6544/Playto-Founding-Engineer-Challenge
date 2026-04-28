from django.core.exceptions import ValidationError
from django.db import models


class Merchant(models.Model):
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class LedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"
        HOLD = "hold", "Hold"
        RELEASE = "release", "Release"

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="ledger_entries")
    payout = models.ForeignKey("Payout", on_delete=models.PROTECT, related_name="ledger_entries", null=True, blank=True)
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    amount_paise = models.BigIntegerField()
    reference_type = models.CharField(max_length=50, null=True, blank=True)
    reference_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("merchant", "created_at")),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(amount_paise__gt=0), name="ledger_amount_gt_zero"),
            models.CheckConstraint(
                check=(
                    (
                        models.Q(payout__isnull=False)
                        & models.Q(reference_type__isnull=True)
                        & models.Q(reference_id__isnull=True)
                    )
                    | (
                        models.Q(payout__isnull=True)
                        & models.Q(reference_type__isnull=False)
                        & models.Q(reference_id__isnull=False)
                    )
                ),
                name="ledger_exactly_one_reference_source",
            ),
            models.CheckConstraint(
                check=models.Q(payout__isnull=True)
                | models.Q(
                    entry_type__in=[
                        EntryType.HOLD,
                        EntryType.DEBIT,
                        EntryType.RELEASE,
                    ]
                ),
                name="ledger_payout_entries_allowed_types",
            ),
            models.CheckConstraint(
                check=models.Q(payout__isnull=False) | models.Q(entry_type=EntryType.CREDIT),
                name="ledger_generic_entries_must_be_credit",
            ),
            models.CheckConstraint(
                check=models.Q(payout__isnull=False) | ~models.Q(reference_type=""),
                name="ledger_reference_type_not_empty",
            ),
            models.CheckConstraint(
                check=models.Q(payout__isnull=False) | ~models.Q(reference_id=""),
                name="ledger_reference_id_not_empty",
            ),
            models.UniqueConstraint(
                fields=("payout",),
                condition=models.Q(payout__isnull=False, entry_type=EntryType.HOLD),
                name="uniq_hold_entry_per_payout",
            ),
            models.UniqueConstraint(
                fields=("payout",),
                condition=models.Q(payout__isnull=False, entry_type=EntryType.DEBIT),
                name="uniq_debit_entry_per_payout",
            ),
            models.UniqueConstraint(
                fields=("payout",),
                condition=models.Q(payout__isnull=False, entry_type=EntryType.RELEASE),
                name="uniq_release_entry_per_payout",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.merchant_id}:{self.entry_type}:{self.amount_paise}"

    def clean(self):
        if self.payout_id is not None:
            # PostgreSQL check constraints cannot safely enforce a join back to
            # payout.merchant here, so we validate the cross-table relationship
            # in application code and keep duplicate-row protection in the DB.
            if self.reference_type or self.reference_id:
                raise ValidationError("Payout-backed ledger entries must use the explicit payout FK only.")
            if self.entry_type not in {self.EntryType.HOLD, self.EntryType.DEBIT, self.EntryType.RELEASE}:
                raise ValidationError({"entry_type": "Only hold, debit, and release entries may reference a payout."})
            if self.payout and self.merchant_id != self.payout.merchant_id:
                raise ValidationError({"merchant": "Ledger entry merchant must match the referenced payout merchant."})
            if self.payout and self.amount_paise != self.payout.amount_paise:
                raise ValidationError({"amount_paise": "Payout-backed ledger amounts must exactly match payout amount."})

            # Ordering rules like "release requires hold" still live in
            # application validation because the assignment deliberately avoids
            # DB triggers/stored procedures for cross-row enforcement.
            hold_exists = type(self).objects.filter(payout_id=self.payout_id, entry_type=self.EntryType.HOLD).exists()
            release_exists = type(self).objects.filter(payout_id=self.payout_id, entry_type=self.EntryType.RELEASE).exists()

            if self.entry_type == self.EntryType.HOLD:
                if self.payout and self.payout.status != self.payout.Status.PENDING:
                    raise ValidationError({"entry_type": "Hold entries may only be created while the payout is pending."})
            elif self.entry_type == self.EntryType.RELEASE:
                if not hold_exists:
                    raise ValidationError({"entry_type": "Release entries require an existing hold for the payout."})
                if self.payout and self.payout.status != self.payout.Status.PROCESSING:
                    raise ValidationError({"entry_type": "Release entries may only be created while the payout is processing."})
            elif self.entry_type == self.EntryType.DEBIT:
                if not hold_exists:
                    raise ValidationError({"entry_type": "Debit entries require an existing hold for the payout."})
                if not release_exists:
                    raise ValidationError({"entry_type": "Debit entries require a release entry to be written first."})
                if self.payout and self.payout.status != self.payout.Status.PROCESSING:
                    raise ValidationError({"entry_type": "Debit entries may only be created while the payout is processing."})
        else:
            if self.entry_type != self.EntryType.CREDIT:
                raise ValidationError({"entry_type": "Only customer payment credits may be written without a payout reference."})
            if not self.reference_type or not self.reference_id:
                raise ValidationError("Non-payout ledger entries must include reference_type and reference_id.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            # This assignment deliberately avoids DB triggers/stored procedures.
            # Model hooks protect normal Django write paths, while DB constraints
            # protect duplicate payout-backed rows and basic shape invariants.
            raise ValidationError("Ledger entries are append-only and cannot be updated.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries are append-only and cannot be deleted.")


class Payout(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.PROCESSING},
        Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
        Status.COMPLETED: set(),
        Status.FAILED: set(),
    }

    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="payouts")
    bank_account_id = models.CharField(max_length=128)
    amount_paise = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("status",)),
            models.Index(fields=("next_retry_at",)),
            models.Index(fields=("status", "next_retry_at")),
            models.Index(fields=("status", "processing_started_at")),
            models.Index(fields=("status", "created_at")),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(amount_paise__gt=0), name="payout_amount_gt_zero"),
            models.CheckConstraint(
                check=(
                    (
                        models.Q(status=Status.PENDING)
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                    | (
                        models.Q(status=Status.PROCESSING)
                        & models.Q(processing_started_at__isnull=False)
                    )
                    | (
                        models.Q(status=Status.COMPLETED)
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                    | (
                        models.Q(status=Status.FAILED)
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                ),
                name="payout_status_timing_consistency",
            ),
        ]

    @classmethod
    def validate_transition(cls, current_status: str, next_status: str) -> None:
        if next_status not in cls.ALLOWED_TRANSITIONS.get(current_status, set()):
            raise ValidationError(f"Illegal payout state transition: {current_status} -> {next_status}")

    def transition_to(self, next_status: str) -> None:
        self.validate_transition(self.status, next_status)
        self.status = next_status

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.status
        return instance

    def clean(self):
        if self.status == self.Status.PENDING and (self.next_retry_at is not None or self.processing_started_at is not None):
            raise ValidationError("Pending payouts cannot have retry scheduling or processing timestamps.")
        if self.status == self.Status.PROCESSING and self.processing_started_at is None:
            raise ValidationError({"processing_started_at": "Processing payouts must track when the current attempt started."})
        if self.status in {self.Status.COMPLETED, self.Status.FAILED} and (
            self.next_retry_at is not None or self.processing_started_at is not None
        ):
            raise ValidationError("Terminal payouts cannot have retry scheduling or processing timestamps.")

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.status != self.Status.PENDING:
                raise ValidationError("New payouts must start in pending state.")
        else:
            previous_status = getattr(self, "_loaded_status", None)
            if previous_status is None:
                previous_status = type(self).objects.only("status").get(pk=self.pk).status
            if self.status != previous_status:
                self.validate_transition(previous_status, self.status)

        self.full_clean()
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    def __str__(self) -> str:
        return f"Payout<{self.id}>:{self.status}"


class IdempotencyKey(models.Model):
    merchant = models.ForeignKey(Merchant, on_delete=models.PROTECT, related_name="idempotency_keys")
    key = models.CharField(max_length=255)
    payout = models.ForeignKey(Payout, on_delete=models.PROTECT, related_name="idempotency_records", null=True, blank=True)
    request_hash = models.CharField(max_length=64)
    request_snapshot = models.JSONField(default=dict, blank=True)
    response_snapshot = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(fields=("merchant", "key"), name="uniq_merchant_idempotency_key"),
            models.CheckConstraint(check=~models.Q(request_hash=""), name="idempotency_request_hash_nonempty"),
        ]

    def clean(self):
        if self.payout and self.merchant_id != self.payout.merchant_id:
            # This cross-table merchant consistency check is enforced in
            # application validation rather than DB triggers to keep the
            # assignment pragmatic and portable.
            raise ValidationError({"merchant": "Idempotency key merchant must match the referenced payout merchant."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.merchant_id}:{self.key}"
