from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0003_gate2_hardening"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(payout__isnull=False) | models.Q(entry_type="credit"),
                name="ledger_generic_entries_must_be_credit",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(payout__isnull=False) | ~models.Q(reference_type=""),
                name="ledger_reference_type_not_empty",
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(payout__isnull=False) | ~models.Q(reference_id=""),
                name="ledger_reference_id_not_empty",
            ),
        ),
    ]
