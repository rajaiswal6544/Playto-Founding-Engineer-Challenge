from rest_framework import serializers

from payouts.models import LedgerEntry, Payout


class LedgerEntrySerializer(serializers.ModelSerializer):
    payout_id = serializers.IntegerField(read_only=True)
    reference_type = serializers.SerializerMethodField()
    reference_id = serializers.SerializerMethodField()

    def get_reference_type(self, obj):
        return "payout" if obj.payout_id else obj.reference_type

    def get_reference_id(self, obj):
        return str(obj.payout_id) if obj.payout_id else obj.reference_id

    class Meta:
        model = LedgerEntry
        fields = ("id", "entry_type", "amount_paise", "payout_id", "reference_type", "reference_id", "created_at")


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = (
            "id",
            "merchant_id",
            "bank_account_id",
            "amount_paise",
            "status",
            "retry_count",
            "next_retry_at",
            "processing_started_at",
            "created_at",
            "updated_at",
        )


class PayoutCreateSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.CharField(max_length=128, trim_whitespace=True)
