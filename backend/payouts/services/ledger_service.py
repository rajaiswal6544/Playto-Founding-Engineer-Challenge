from django.core.exceptions import ValidationError

from payouts.models import LedgerEntry, Payout


class LedgerService:
    @staticmethod
    def credit_customer_payment(*, merchant_id: int, amount_paise: int, reference_id: str) -> LedgerEntry:
        if not reference_id:
            raise ValidationError({"reference_id": "Customer payment credits require a reference_id."})
        return LedgerEntry.objects.create(
            merchant_id=merchant_id,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount_paise=amount_paise,
            reference_type="customer_payment",
            reference_id=reference_id,
        )

    @staticmethod
    def place_hold(*, payout: Payout) -> LedgerEntry:
        if payout.status != Payout.Status.PENDING:
            raise ValidationError({"status": "Payout holds may only be created while the payout is pending."})
        return LedgerEntry.objects.create(
            merchant_id=payout.merchant_id,
            payout=payout,
            entry_type=LedgerEntry.EntryType.HOLD,
            amount_paise=payout.amount_paise,
        )

    @staticmethod
    def release_hold(*, payout: Payout) -> LedgerEntry:
        if payout.status != Payout.Status.PROCESSING:
            raise ValidationError({"status": "Hold releases may only be created while the payout is processing."})
        if not LedgerEntry.objects.filter(payout=payout, entry_type=LedgerEntry.EntryType.HOLD).exists():
            raise ValidationError({"payout": "Cannot release funds for a payout that has no hold entry."})
        return LedgerEntry.objects.create(
            merchant_id=payout.merchant_id,
            payout=payout,
            entry_type=LedgerEntry.EntryType.RELEASE,
            amount_paise=payout.amount_paise,
        )

    @staticmethod
    def capture_debit(*, payout: Payout) -> LedgerEntry:
        if payout.status != Payout.Status.PROCESSING:
            raise ValidationError({"status": "Payout debits may only be created while the payout is processing."})
        if not LedgerEntry.objects.filter(payout=payout, entry_type=LedgerEntry.EntryType.HOLD).exists():
            raise ValidationError({"payout": "Cannot capture a payout debit without an existing hold."})
        if not LedgerEntry.objects.filter(payout=payout, entry_type=LedgerEntry.EntryType.RELEASE).exists():
            raise ValidationError({"payout": "Cannot capture a payout debit before releasing the hold."})
        return LedgerEntry.objects.create(
            merchant_id=payout.merchant_id,
            payout=payout,
            entry_type=LedgerEntry.EntryType.DEBIT,
            amount_paise=payout.amount_paise,
        )
