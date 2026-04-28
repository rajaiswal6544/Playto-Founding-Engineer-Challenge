from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from payouts.models import Merchant, Payout
from payouts.services.balance_service import BalanceService
from payouts.services.idempotency_service import IdempotencyService
from payouts.services.ledger_service import LedgerService


class MissingIdempotencyKeyError(ValidationError):
    pass


@dataclass(frozen=True)
class PayoutCreationResult:
    payload: dict
    status_code: int
    created: bool


class PayoutService:
    @staticmethod
    def serialize_payout(payout: Payout) -> dict:
        return {
            "id": payout.id,
            "merchant_id": payout.merchant_id,
            "bank_account_id": payout.bank_account_id,
            "amount_paise": payout.amount_paise,
            "status": payout.status,
            "retry_count": payout.retry_count,
            "next_retry_at": payout.next_retry_at.isoformat() if payout.next_retry_at else None,
            "processing_started_at": payout.processing_started_at.isoformat() if payout.processing_started_at else None,
            "created_at": payout.created_at.isoformat(),
            "updated_at": payout.updated_at.isoformat(),
        }

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

        balances = BalanceService.get_balances(merchant.id)
        if balances["available_balance"] < amount_paise:
            payload = {"amount_paise": ["Insufficient available balance for payout."]}
            IdempotencyService.create_snapshot(
                merchant_id=merchant.id,
                key=idempotency_key,
                request_payload=request_payload,
                payload=payload,
                status_code=400,
                payout=None,
            )
            return PayoutCreationResult(payload=payload, status_code=400, created=False)

        payout = Payout.objects.create(
            merchant=merchant,
            bank_account_id=bank_account_id,
            amount_paise=amount_paise,
            status=Payout.Status.PENDING,
        )
        LedgerService.place_hold(payout=payout)

        payload = PayoutService.serialize_payout(payout)
        IdempotencyService.create_snapshot(
            merchant_id=merchant.id,
            key=idempotency_key,
            request_payload=request_payload,
            payload=payload,
            status_code=201,
            payout=payout,
        )
        return PayoutCreationResult(payload=payload, status_code=201, created=True)
