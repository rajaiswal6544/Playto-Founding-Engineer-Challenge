from django.db.models import BigIntegerField, Case, F, Sum, Value, When
from django.db.models.functions import Coalesce

from payouts.models import LedgerEntry


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
