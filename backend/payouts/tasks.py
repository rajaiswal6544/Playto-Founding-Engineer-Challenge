from celery import shared_task
from django.db import transaction
from django.utils import timezone

from payouts.models import Payout
from payouts.services.payout_processor import PayoutProcessor
from payouts.services.retry_service import RetryService


@shared_task(name="payouts.process_pending_payouts")
def process_pending_payouts() -> int:
    processed = 0
    while True:
        with transaction.atomic():
            # Pragmatic assignment-grade claiming: lock one row at a time and
            # skip already-claimed rows so concurrent workers do not fan out
            # the same payout batch before processing begins.
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


@shared_task(name="payouts.process_due_retries")
def process_due_retries() -> int:
    now = timezone.now()
    processed = 0
    while True:
        with transaction.atomic():
            payout = (
                Payout.objects.select_for_update(skip_locked=True)
                .filter(
                    status=Payout.Status.PROCESSING,
                    next_retry_at__isnull=False,
                    next_retry_at__lte=now,
                )
                .order_by("next_retry_at", "id")
                .first()
            )
            if payout is None:
                break
            RetryService.process_due_retry_locked(payout, now=now)
            processed += 1
    return processed
