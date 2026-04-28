from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="idempotencykey",
            name="request_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
