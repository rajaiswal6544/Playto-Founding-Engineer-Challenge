from rest_framework import status
from rest_framework.test import APITestCase

from payouts.models import LedgerEntry, Merchant, Payout


class PayoutApiTests(APITestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(name="API Merchant")
        LedgerEntry.objects.create(
            merchant=self.merchant,
            entry_type=LedgerEntry.EntryType.CREDIT,
            amount_paise=20_000,
            reference_type="test",
            reference_id="credit",
        )

    def test_create_payout_requires_idempotency_key(self):
        response = self.client.post(
            "/api/v1/payouts",
            {"amount_paise": 5_000, "bank_account_id": "bank_001"},
            format="json",
            headers={"X-Merchant-Id": str(self.merchant.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("idempotency_key", response.data)

    def test_create_payout_requires_explicit_merchant_header(self):
        response = self.client.post(
            "/api/v1/payouts",
            {"amount_paise": 5_000, "bank_account_id": "bank_001"},
            format="json",
            headers={"Idempotency-Key": "missing-merchant"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("merchant", response.data)

    def test_dashboard_requires_explicit_merchant_header(self):
        response = self.client.get("/api/v1/dashboard")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("merchant", response.data)

    def test_dashboard_rejects_unknown_merchant_header(self):
        response = self.client.get("/api/v1/dashboard", headers={"X-Merchant-Id": "999999"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_dashboard_returns_balances_and_history(self):
        Payout.objects.create(
            merchant=self.merchant,
            bank_account_id="bank_existing",
            amount_paise=2_500,
            status=Payout.Status.PENDING,
        )
        response = self.client.get("/api/v1/dashboard", headers={"X-Merchant-Id": str(self.merchant.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["merchant"]["id"], self.merchant.id)
        self.assertIn("available_balance", response.data)
        self.assertIn("recent_ledger_entries", response.data)
        self.assertIn("payout_history", response.data)
