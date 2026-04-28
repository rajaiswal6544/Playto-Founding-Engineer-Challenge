import threading
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from payouts.models import IdempotencyKey, LedgerEntry, Merchant, Payout
from payouts.services.balance_service import BalanceService
from payouts.services.ledger_service import LedgerService
from payouts.services.payout_processor import InvalidProcessorStateError, PayoutProcessor, UnknownProcessorOutcomeError
from payouts.services.payout_service import PayoutService
from payouts.services.retry_service import InvalidRetryStateError, RetryNotDueError, RetryService


class PayoutServiceTests(TestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(name="Service Merchant")
        LedgerService.credit_customer_payment(
            merchant_id=self.merchant.id,
            amount_paise=10_000,
            reference_id="test-credit",
        )

    def test_idempotent_create_returns_same_snapshot(self):
        first = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=4_000,
            bank_account_id="bank_same",
            idempotency_key="same-key",
        )
        second = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=4_000,
            bank_account_id="bank_same",
            idempotency_key="same-key",
        )
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.HOLD).count(), 1)

    def test_invalid_state_transition_is_blocked(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_abc",
            amount_paise=1_000,
        )
        payout.transition_to(Payout.Status.PROCESSING)
        payout.processing_started_at = timezone.now()
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])
        payout.transition_to(Payout.Status.COMPLETED)
        payout.processing_started_at = None
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])
        with self.assertRaises(ValidationError):
            payout.transition_to(Payout.Status.PENDING)

    def test_direct_illegal_status_mutation_is_blocked_on_save(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_direct_mutation",
            amount_paise=1_000,
        )
        payout.status = Payout.Status.COMPLETED
        with self.assertRaises(ValidationError):
            payout.save()

    def test_ledger_entries_cannot_be_updated_or_deleted(self):
        entry = LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount_paise=500,
            reference_type="test",
            reference_id="append-only",
        )
        entry.amount_paise = 600
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_insufficient_funds_failure_is_replayed_for_same_key(self):
        first = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=20_000,
            bank_account_id="bank_fail_same_key",
            idempotency_key="insufficient-funds-key",
        )
        second = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=20_000,
            bank_account_id="bank_fail_same_key",
            idempotency_key="insufficient-funds-key",
        )
        self.assertEqual(first.status_code, 400)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(Payout.objects.count(), 0)

    def test_expired_idempotency_key_is_retained_and_rejected(self):
        result = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=1_000,
            bank_account_id="bank_expiry",
            idempotency_key="expired-key",
        )
        self.assertEqual(result.status_code, 201)

        record = IdempotencyKey.objects.get(merchant=self.merchant, key="expired-key")
        IdempotencyKey.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        replay = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=1_000,
            bank_account_id="bank_expiry",
            idempotency_key="expired-key",
        )
        self.assertEqual(replay.status_code, 409)
        self.assertTrue(IdempotencyKey.objects.filter(merchant=self.merchant, key="expired-key").exists())
        self.assertEqual(Payout.objects.count(), 1)

    def test_duplicate_payout_ledger_rows_are_blocked_by_db_constraint(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_unique_hold",
            amount_paise=1_000,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            payout=payout,
            entry_type=LedgerEntry.EntryType.HOLD,
            amount_paise=1_000,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.bulk_create(
                    [
                        LedgerEntry(
                            merchant=self.merchant,
                            payout=payout,
                            entry_type=LedgerEntry.EntryType.HOLD,
                            amount_paise=1_000,
                        )
                    ]
                )

    def test_same_key_different_payload_is_rejected(self):
        first = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=4_000,
            bank_account_id="bank_original",
            idempotency_key="payload-conflict-key",
        )
        second = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=5_000,
            bank_account_id="bank_original",
            idempotency_key="payload-conflict-key",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertIn("idempotency_key", second.payload)

    def test_release_requires_existing_hold(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_release_without_hold",
            amount_paise=700,
        )
        payout.transition_to(Payout.Status.PROCESSING)
        payout.processing_started_at = timezone.now()
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])
        with self.assertRaises(ValidationError):
            LedgerService.release_hold(payout=payout)

    def test_debit_requires_release_sequence(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_debit_without_release",
            amount_paise=700,
        )
        LedgerService.place_hold(payout=payout)
        payout.transition_to(Payout.Status.PROCESSING)
        payout.processing_started_at = timezone.now()
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])
        with self.assertRaises(ValidationError):
            LedgerService.capture_debit(payout=payout)

    def test_generic_non_credit_entries_are_rejected(self):
        with self.assertRaises(ValidationError):
            LedgerEntry.objects.create(
                merchant=self.merchant,
                entry_type=LedgerEntry.EntryType.DEBIT,
                amount_paise=500,
                reference_type="manual_adjustment",
                reference_id="bad-generic-debit",
            )

    def test_ledger_payout_merchant_mismatch_is_rejected(self):
        other_merchant = Merchant.objects.create(name="Other Merchant")
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_mismatch",
            amount_paise=500,
        )
        with self.assertRaises(ValidationError):
            LedgerEntry.objects.create(
                merchant=other_merchant,
                payout=payout,
                entry_type=LedgerEntry.EntryType.HOLD,
                amount_paise=500,
            )

    def test_idempotency_key_payout_merchant_mismatch_is_rejected(self):
        other_merchant = Merchant.objects.create(name="Other Idempotency Merchant")
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_idempotency_mismatch",
            amount_paise=500,
        )
        with self.assertRaises(ValidationError):
            IdempotencyKey.objects.create(
                merchant=other_merchant,
                payout=payout,
                key="merchant-mismatch-key",
                request_hash="a" * 64,
                request_snapshot={"amount_paise": 500},
                response_snapshot={"status_code": 201, "body": {"id": payout.id}},
                expires_at=timezone.now() + timedelta(hours=1),
            )

    def test_failed_payout_releases_held_balance(self):
        created = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=3_000,
            bank_account_id="bank_fail",
            idempotency_key="fail-key",
        )
        PayoutProcessor.process_pending_payout(created.payload["id"], outcome=PayoutProcessor.FAIL)
        balances = BalanceService.get_balances(self.merchant.id)
        self.assertEqual(balances["held_balance"], 0)
        self.assertEqual(balances["available_balance"], 10_000)

    def test_non_processing_pending_processor_call_raises(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_invalid_processor_state",
            amount_paise=400,
        )
        payout.transition_to(Payout.Status.PROCESSING)
        payout.processing_started_at = timezone.now()
        payout.save(update_fields=["status", "processing_started_at", "updated_at"])
        with self.assertRaises(InvalidProcessorStateError):
            PayoutProcessor.process_pending_payout(payout.id, outcome=PayoutProcessor.SUCCESS)

    def test_retry_schedules_then_completes_stuck_processing(self):
        created = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=2_000,
            bank_account_id="bank_retry",
            idempotency_key="retry-key",
        )
        payout_id = created.payload["id"]
        PayoutProcessor.process_pending_payout(payout_id, outcome=PayoutProcessor.STUCK)

        now = timezone.now()
        Payout.objects.filter(pk=payout_id).update(updated_at=now - timedelta(minutes=5), processing_started_at=now)
        scheduled = RetryService.schedule_retry(payout_id, now=now)
        self.assertIsNone(scheduled)

        stale_time = timezone.now() - timedelta(seconds=31)
        Payout.objects.filter(pk=payout_id).update(processing_started_at=stale_time)
        RetryService.schedule_retry(payout_id, now=timezone.now())

        payout = Payout.objects.get(pk=payout_id)
        self.assertEqual(payout.retry_count, 1)
        self.assertIsNotNone(payout.next_retry_at)
        self.assertEqual(payout.status, Payout.Status.PROCESSING)

        with self.assertRaises(RetryNotDueError):
            RetryService.process_due_retry(payout_id, outcome=PayoutProcessor.SUCCESS, now=timezone.now())

        Payout.objects.filter(pk=payout_id).update(next_retry_at=timezone.now() - timedelta(seconds=1))
        RetryService.process_due_retry(payout_id, outcome=PayoutProcessor.SUCCESS, now=timezone.now())
        payout.refresh_from_db()
        balances = BalanceService.get_balances(self.merchant.id)
        self.assertEqual(payout.status, Payout.Status.COMPLETED)
        self.assertEqual(balances["held_balance"], 0)
        self.assertEqual(balances["available_balance"], 8_000)
        self.assertIsNone(payout.processing_started_at)

    def test_schedule_retry_from_non_processing_state_raises(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_invalid_retry_state",
            amount_paise=400,
        )
        with self.assertRaises(InvalidRetryStateError):
            RetryService.schedule_retry(payout.id, now=timezone.now())

    def test_process_due_retry_from_non_processing_state_raises(self):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_invalid_due_retry_state",
            amount_paise=400,
        )
        with self.assertRaises(InvalidRetryStateError):
            RetryService.process_due_retry(payout.id, now=timezone.now())

    def test_unknown_processor_outcome_raises_error(self):
        created = PayoutService.create_payout(
            merchant_id=self.merchant.id,
            amount_paise=1_000,
            bank_account_id="bank_unknown_outcome",
            idempotency_key="unknown-outcome-key",
        )
        with self.assertRaises(UnknownProcessorOutcomeError):
            PayoutProcessor.process_pending_payout(created.payload["id"], outcome="mystery")

    def test_empty_reference_fields_are_blocked_by_db_constraints(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.bulk_create(
                    [
                        LedgerEntry(
                            merchant=self.merchant,
                            entry_type=LedgerEntry.EntryType.CREDIT,
                            amount_paise=100,
                            reference_type="",
                            reference_id="",
                        )
                    ]
                )


class PayoutConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.merchant = Merchant.objects.create(name="Concurrent Merchant")
        LedgerService.credit_customer_payment(
            merchant_id=self.merchant.id,
            amount_paise=10_000,
            reference_id="concurrent-credit",
        )

    def _attempt_create(self, barrier: threading.Barrier, results: dict, slot: str, key: str):
        close_old_connections()
        try:
            barrier.wait()
            result = PayoutService.create_payout(
                merchant_id=self.merchant.id,
                amount_paise=6_000,
                bank_account_id=f"bank_{slot}",
                idempotency_key=key,
            )
            if result.status_code == 201:
                results[slot] = ("success", result.payload["id"])
            else:
                results[slot] = ("failed", result.payload)
        finally:
            close_old_connections()

    def test_only_one_of_two_parallel_payouts_succeeds(self):
        barrier = threading.Barrier(2)
        results = {}
        threads = [
            threading.Thread(target=self._attempt_create, args=(barrier, results, "a", "idem-a")),
            threading.Thread(target=self._attempt_create, args=(barrier, results, "b", "idem-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(Payout.objects.count(), 1)
        self.assertEqual(LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.HOLD).count(), 1)
        self.assertCountEqual([result[0] for result in results.values()], ["success", "failed"])
