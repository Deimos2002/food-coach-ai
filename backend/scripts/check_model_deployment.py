"""Validate Azure's deployment metadata, supplied as JSON on stdin."""

import json
import sys
from typing import TextIO

EXPECTED = {
    'sku.name': 'DataZoneStandard',
    'properties.model.name': 'gpt-4.1-mini',
    'properties.model.version': '2025-04-14',
    'properties.versionUpgradeOption': 'NoAutoUpgrade',
}


def main(stream: TextIO | None = None) -> int:
    try:
        payload = json.load(stream if stream is not None else sys.stdin)
    except (ValueError, OSError):
        print('Model gate failed: invalid deployment JSON')
        return 1
    failed = False
    for path, expected in EXPECTED.items():
        actual = payload
        for key in path.split('.'):
            actual = actual.get(key) if isinstance(actual, dict) else None
        if actual != expected:
            print(f'Model gate failed: {path} must equal {expected}')
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
