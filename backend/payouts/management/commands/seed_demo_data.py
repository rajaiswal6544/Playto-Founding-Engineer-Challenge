from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from payouts.models import Merchant, Payout
from payouts.services.balance_service import BalanceService
from payouts.services.ledger_service import LedgerService
from payouts.services.payout_processor import PayoutProcessor


class Command(BaseCommand):
    help = "Seed a demo merchant with ledger and payout history."

    def handle(self, *args, **options):
        merchant, _ = Merchant.objects.get_or_create(name="Playto Demo Merchant")

        if not merchant.ledger_entries.filter(entry_type="credit").exists():
            LedgerService.credit_customer_payment(
                merchant_id=merchant.id,
                amount_paise=200_000,
                reference_id="seed-initial-credit",
            )

        if not Payout.objects.filter(merchant=merchant, bank_account_id="bank_demo_completed").exists():
            with transaction.atomic():
                payout = Payout.objects.create(
                    merchant=merchant,
                    bank_account_id="bank_demo_completed",
                    amount_paise=30_000,
                )
                LedgerService.place_hold(payout=payout)
                PayoutProcessor.process_pending_locked(
                    payout,
                    outcome=PayoutProcessor.SUCCESS,
                    now=timezone.now(),
                )

        if not Payout.objects.filter(merchant=merchant, bank_account_id="bank_demo_failed").exists():
            with transaction.atomic():
                payout = Payout.objects.create(
                    merchant=merchant,
                    bank_account_id="bank_demo_failed",
                    amount_paise=15_000,
                )
                LedgerService.place_hold(payout=payout)
                PayoutProcessor.process_pending_locked(
                    payout,
                    outcome=PayoutProcessor.FAIL,
                    now=timezone.now(),
                )

        if not Payout.objects.filter(merchant=merchant, bank_account_id="bank_demo_pending").exists():
            with transaction.atomic():
                payout = Payout.objects.create(
                    merchant=merchant,
                    bank_account_id="bank_demo_pending",
                    amount_paise=10_000,
                    status=Payout.Status.PENDING,
                )
                LedgerService.place_hold(payout=payout)

        balances = BalanceService.get_balances(merchant.id)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded merchant {merchant.id} with available={balances['available_balance']} held={balances['held_balance']}"
            )
        )
