from django.contrib import admin

from .models import IdempotencyKey, LedgerEntry, Merchant, Payout


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")
    search_fields = ("name",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "merchant", "payout", "entry_type", "amount_paise", "reference_type", "reference_id", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("reference_id", "payout__id")
    readonly_fields = ("merchant", "payout", "entry_type", "amount_paise", "reference_type", "reference_id", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "merchant",
        "amount_paise",
        "bank_account_id",
        "status",
        "retry_count",
        "processing_started_at",
        "next_retry_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("bank_account_id",)
    readonly_fields = (
        "merchant",
        "bank_account_id",
        "amount_paise",
        "status",
        "retry_count",
        "processing_started_at",
        "next_retry_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "merchant", "key", "payout", "expires_at", "created_at")
    search_fields = ("key",)
    readonly_fields = ("merchant", "key", "payout", "request_hash", "request_snapshot", "response_snapshot", "expires_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True
