from django.conf import settings
from django.db import migrations


def stamp_existing_calls(apps, schema_editor):
    """Calls opened before calls remembered their contract all ran on the one configured at upgrade time."""
    CallSession = apps.get_model("calls", "CallSession")
    contract = settings.STELLAR.get("escrow_contract_id", "")
    CallSession.objects.filter(onchain_id__isnull=False, escrow_contract="").update(escrow_contract=contract)


class Migration(migrations.Migration):
    dependencies = [("calls", "0005_escrow_contract")]
    operations = [migrations.RunPython(stamp_existing_calls, migrations.RunPython.noop)]
