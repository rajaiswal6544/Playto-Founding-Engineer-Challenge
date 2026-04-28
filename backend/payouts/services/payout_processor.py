import random

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payouts.models import Payout
from payouts.services.ledger_service import LedgerService


class UnknownProcessorOutcomeError(ValidationError):
    pass


class InvalidProcessorStateError(ValidationError):
    pass


class PayoutProcessor:
    SUCCESS = "success"
    FAIL = "fail"
    STUCK = "stuck"

    @staticmethod
    def simulate_bank_result() -> str:
        roll = random.random()
        if roll < 0.7:
            return PayoutProcessor.SUCCESS
        if roll < 0.9:
            return PayoutProcessor.FAIL
        return PayoutProcessor.STUCK

    @staticmethod
    def _finalize_success(payout: Payout) -> None:
        LedgerService.release_hold(payout=payout)
        LedgerService.capture_debit(payout=payout)
        payout.transition_to(Payout.Status.COMPLETED)
        payout.processing_started_at = None
        payout.next_retry_at = None
        payout.save(update_fields=["status", "processing_started_at", "next_retry_at", "updated_at"])

    @staticmethod
    def _finalize_failure(payout: Payout) -> None:
        LedgerService.release_hold(payout=payout)
        payout.transition_to(Payout.Status.FAILED)
        payout.processing_started_at = None
        payout.next_retry_at = None
        payout.save(update_fields=["status", "processing_started_at", "next_retry_at", "updated_at"])

    @staticmethod
    def _apply_outcome(payout: Payout, outcome: str) -> str:
        if outcome not in {PayoutProcessor.SUCCESS, PayoutProcessor.FAIL, PayoutProcessor.STUCK}:
            raise UnknownProcessorOutcomeError(f"Unknown payout processor outcome: {outcome}")
        if outcome == PayoutProcessor.SUCCESS:
            PayoutProcessor._finalize_success(payout)
            return outcome
        if outcome == PayoutProcessor.FAIL:
            PayoutProcessor._finalize_failure(payout)
            return outcome

        return PayoutProcessor.STUCK

    @staticmethod
    def process_pending_locked(payout: Payout, outcome: str | None = None, now=None) -> str:
        if payout.status != Payout.Status.PENDING:
            raise InvalidProcessorStateError(f"Pending payout processor cannot run from state {payout.status}.")
        now = now or timezone.now()
        payout.transition_to(Payout.Status.PROCESSING)
        payout.processing_started_at = now
        payout.next_retry_at = None
        payout.save(update_fields=["status", "processing_started_at", "next_retry_at", "updated_at"])
        return PayoutProcessor._apply_outcome(payout, outcome or PayoutProcessor.simulate_bank_result())

    @staticmethod
    def process_due_retry_locked(payout: Payout, outcome: str | None = None, now=None) -> str:
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidProcessorStateError(f"Retry payout processor cannot run from state {payout.status}.")
        now = now or timezone.now()
        payout.processing_started_at = now
        payout.next_retry_at = None
        payout.save(update_fields=["processing_started_at", "next_retry_at", "updated_at"])
        return PayoutProcessor._apply_outcome(payout, outcome or PayoutProcessor.simulate_bank_result())

    @staticmethod
    @transaction.atomic
    def process_pending_payout(payout_id: int, outcome: str | None = None, now=None) -> str:
        payout = Payout.objects.select_for_update().get(pk=payout_id)
        return PayoutProcessor.process_pending_locked(payout, outcome=outcome, now=now)

    @staticmethod
    @transaction.atomic
    def retry_processing_payout(payout_id: int, outcome: str | None = None, now=None) -> str:
        from payouts.services.retry_service import RetryService

        return RetryService.process_due_retry(payout_id, outcome=outcome, now=now)
