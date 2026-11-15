"""Public production probes. Never log request headers or token values."""

import sys
from datetime import UTC, datetime

import httpx
import jwt


def run_smoke(base_url: str, client: httpx.Client) -> int:
    base = base_url.rstrip('/')
    try:
        response = client.get(f'{base}/health?deep=1')
        if response.status_code != 200:
            print('Smoke failed: deep health')
            return 1
        response = client.get(f'{base}/')
        if (response.status_code != 200
                or response.headers.get('content-type', '').split(';')[0].lower() != 'text/html'
                or 'id="root"' not in response.text):
            print('Smoke failed: web app HTML')
            return 1
        # Synthetic, deliberately expired JWT; no production credentials are used.
        expired = jwt.encode({'sub': 'smoke', 'exp': 1}, 'smoke-test-key-not-a-secret-32-bytes',
                             algorithm='HS256')
        url = f'{base}/api/days/{datetime.now(UTC).date().isoformat()}'
        for token in (None, 'abc', expired):
            headers = {} if token is None else {'Authorization': f'Bearer {token}'}
            if client.get(url, headers=headers).status_code != 401:
                print('Smoke failed: API rejected-credential check')
                return 1
    except httpx.HTTPError:
        print('Smoke failed: HTTP request')
        return 1
    print('Smoke passed')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('Usage: smoke.py BASE_URL')
        return 1
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        return run_smoke(args[0], client)


if __name__ == '__main__':
    raise SystemExit(main())
