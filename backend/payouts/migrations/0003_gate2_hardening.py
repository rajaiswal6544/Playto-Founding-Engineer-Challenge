from django.db import migrations, models
import django.db.models.deletion
import json
import hashlib


def _hash_payload(payload):
    normalized = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def backfill_ledger_and_idempotency(apps, schema_editor):
    LedgerEntry = apps.get_model("payouts", "LedgerEntry")
    Payout = apps.get_model("payouts", "Payout")
    IdempotencyKey = apps.get_model("payouts", "IdempotencyKey")

    for entry in LedgerEntry.objects.filter(reference_type="payout").exclude(reference_id__isnull=True):
        try:
            payout_id = int(entry.reference_id)
        except (TypeError, ValueError):
            continue
        payout = Payout.objects.filter(pk=payout_id).first()
        if payout is None:
            continue
        entry.payout_id = payout.id
        entry.reference_type = None
        entry.reference_id = None
        entry.save(update_fields=["payout", "reference_type", "reference_id"])

    for record in IdempotencyKey.objects.all():
        record.request_hash = _hash_payload(record.request_snapshot)
        record.save(update_fields=["request_hash"])


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0002_idempotencykey_request_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="ledgerentry",
            name="payout",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="payouts.payout"),
        ),
        migrations.AddField(
            model_name="payout",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="idempotencykey",
            name="request_hash",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="ledgerentry",
            name="reference_id",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="ledgerentry",
            name="reference_type",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.RunPython(backfill_ledger_and_idempotency, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="payout",
            index=models.Index(fields=["status", "next_retry_at"], name="payouts_pay_status_1d77a3_idx"),
        ),
        migrations.AddIndex(
            model_name="payout",
            index=models.Index(fields=["status", "processing_started_at"], name="payouts_pay_status_0fbf5b_idx"),
        ),
        migrations.AddIndex(
            model_name="payout",
            index=models.Index(fields=["status", "created_at"], name="payouts_pay_status_4a126f_idx"),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=(
                    (
                        models.Q(payout__isnull=False)
                        & models.Q(reference_type__isnull=True)
                        & models.Q(reference_id__isnull=True)
                    )
                    | (
                        models.Q(payout__isnull=True)
                        & models.Q(reference_type__isnull=False)
                        & models.Q(reference_id__isnull=False)
                    )
                ),
                name="ledger_exactly_one_reference_source",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(payout__isnull=True)
                | models.Q(entry_type__in=["hold", "debit", "release"]),
                name="ledger_payout_entries_allowed_types",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(entry_type="hold", payout__isnull=False),
                fields=("payout",),
                name="uniq_hold_entry_per_payout",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(entry_type="debit", payout__isnull=False),
                fields=("payout",),
                name="uniq_debit_entry_per_payout",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(entry_type="release", payout__isnull=False),
                fields=("payout",),
                name="uniq_release_entry_per_payout",
            ),
        ),
        migrations.AddConstraint(
            model_name="payout",
            constraint=models.CheckConstraint(
                check=(
                    (
                        models.Q(status="pending")
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                    | (
                        models.Q(status="processing")
                        & models.Q(processing_started_at__isnull=False)
                    )
                    | (
                        models.Q(status="completed")
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                    | (
                        models.Q(status="failed")
                        & models.Q(next_retry_at__isnull=True)
                        & models.Q(processing_started_at__isnull=True)
                    )
                ),
                name="payout_status_timing_consistency",
            ),
        ),
        migrations.AddConstraint(
            model_name="idempotencykey",
            constraint=models.CheckConstraint(check=~models.Q(request_hash=""), name="idempotency_request_hash_nonempty"),
        ),
    ]
