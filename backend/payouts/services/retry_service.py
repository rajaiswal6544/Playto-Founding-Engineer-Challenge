from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payouts.models import Payout
from payouts.services.ledger_service import LedgerService


class RetryNotDueError(ValidationError):
    pass


class InvalidRetryStateError(ValidationError):
    pass


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
    @transaction.atomic
    def schedule_retry(payout_id: int, now=None) -> Payout | None:
        payout = Payout.objects.select_for_update().get(pk=payout_id)
        return RetryService.schedule_retry_locked(payout, now=now)

    @staticmethod
    def process_due_retry_locked(payout: Payout, outcome: str | None = None, now=None) -> str:
        now = now or timezone.now()
        if payout.status != Payout.Status.PROCESSING:
            raise InvalidRetryStateError(f"Due retries can only run from processing state, not {payout.status}.")
        if payout.next_retry_at is None:
            raise RetryNotDueError("Retry has not been scheduled for this payout.")
        if payout.next_retry_at > now:
            raise RetryNotDueError("Retry is not due yet.")

        from payouts.services.payout_processor import PayoutProcessor

        return PayoutProcessor.process_due_retry_locked(payout, outcome=outcome, now=now)

    @staticmethod
    @transaction.atomic
    def process_due_retry(payout_id: int, outcome: str | None = None, now=None) -> str:
        payout = Payout.objects.select_for_update().get(pk=payout_id)
        return RetryService.process_due_retry_locked(payout, outcome=outcome, now=now)
