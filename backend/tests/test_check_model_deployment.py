import copy
import io
import json

import pytest

from scripts.check_model_deployment import main

GOOD = {
    'sku': {'name': 'DataZoneStandard'},
    'properties': {
        'model': {'name': 'gpt-4.1-mini', 'version': '2025-04-14'},
        'versionUpgradeOption': 'NoAutoUpgrade',
    },
}


def test_gate_passes():
    assert main(io.StringIO(json.dumps(GOOD))) == 0


@pytest.mark.parametrize(('path', 'value'), [
    (('sku', 'name'), 'GlobalStandard'),
    (('properties', 'model', 'version'), '2025-01-01'),
    (('properties', 'versionUpgradeOption'), 'OnceNewDefaultVersionAvailable'),
    (('properties', 'model', 'name'), 'gpt-4.1'),
])
def test_gate_rejects_mismatch(path, value, capsys):
    payload = copy.deepcopy(GOOD)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert main(io.StringIO(json.dumps(payload))) == 1
    assert '.'.join(path) in capsys.readouterr().out


@pytest.mark.parametrize('payload', ['{}', 'null', '[]', 'invalid'])
def test_gate_rejects_invalid_payload(payload):
    assert main(io.StringIO(payload)) == 1
