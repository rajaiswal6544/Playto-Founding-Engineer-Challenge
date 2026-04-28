from datetime import datetime, timedelta
import hashlib
import json

from django.conf import settings
from django.utils import timezone

from payouts.models import IdempotencyKey


class IdempotencyService:
    @staticmethod
    def get_key(merchant_id: int, key: str) -> IdempotencyKey | None:
        # Expired keys are retained as tombstones so late client retries cannot
        # silently create a second payout after the original request ages out.
        return IdempotencyKey.objects.select_related("payout").filter(merchant_id=merchant_id, key=key).first()

    @staticmethod
    def is_expired(record: IdempotencyKey, now=None) -> bool:
        now = now or timezone.now()
        return record.expires_at <= now

    @staticmethod
    def ttl_expiry() -> datetime:
        return timezone.now() + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS)

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
