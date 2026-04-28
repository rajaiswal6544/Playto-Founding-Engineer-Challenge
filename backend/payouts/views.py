from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from payouts.models import LedgerEntry, Merchant, Payout
from payouts.serializers import LedgerEntrySerializer, PayoutCreateSerializer, PayoutSerializer
from payouts.services.balance_service import BalanceService
from payouts.services.payout_service import MissingIdempotencyKeyError, PayoutService


class MerchantScopedAPIView(APIView):
    merchant_header = "X-Merchant-Id"

    def get_merchant(self) -> Merchant:
        merchant_id = self.request.headers.get(self.merchant_header)
        if not merchant_id:
            # The assignment omits full auth, so this required header simulates
            # authenticated tenant context instead of falling back to an
            # arbitrary merchant record.
            raise ValidationError({"merchant": [f"{self.merchant_header} header is required."]})
        return get_object_or_404(Merchant, pk=merchant_id)


class DashboardView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        balances = BalanceService.get_balances(merchant.id)
        ledger_entries = LedgerEntry.objects.filter(merchant=merchant).order_by("-created_at", "-id")[:10]
        payouts = Payout.objects.filter(merchant=merchant).order_by("-created_at", "-id")[:10]
        return Response(
            {
                "merchant": {
                    "id": merchant.id,
                    "name": merchant.name,
                },
                **balances,
                "recent_ledger_entries": LedgerEntrySerializer(ledger_entries, many=True).data,
                "payout_history": PayoutSerializer(payouts, many=True).data,
            }
        )


class PayoutListCreateView(MerchantScopedAPIView):
    def get(self, request):
        merchant = self.get_merchant()
        payouts = Payout.objects.filter(merchant=merchant).order_by("-created_at", "-id")
        return Response(PayoutSerializer(payouts, many=True).data)

    def post(self, request):
        merchant = self.get_merchant()
        # Idempotency in this assignment intentionally begins after schema
        # validation. Malformed requests are rejected directly instead of being
        # snapshotted/replayed, which keeps the API surface simpler without
        # introducing broader request-envelope persistence.
        serializer = PayoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = PayoutService.create_payout(
                merchant_id=merchant.id,
                amount_paise=serializer.validated_data["amount_paise"],
                bank_account_id=serializer.validated_data["bank_account_id"],
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except MissingIdempotencyKeyError as exc:
            raise ValidationError({"idempotency_key": exc.messages})

        return Response(result.payload, status=result.status_code)
