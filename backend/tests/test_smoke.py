from datetime import UTC, date, datetime

import httpx
import jwt
import pytest

from scripts.smoke import run_smoke


def client_for(*, health=200, root='<html><div id="root"></div></html>',
               content_type='text/html', auth_status=401, seen=None):
    def handle(request):
        if request.url.path == '/health':
            assert request.url.query == b'deep=1'
            return httpx.Response(health)
        if request.url.path == '/':
            return httpx.Response(200, text=root, headers={'content-type': content_type})
        assert request.url.path == f'/api/days/{datetime.now(UTC).date()}'
        if seen is not None:
            seen.append(request.headers.get('authorization'))
        return httpx.Response(auth_status)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_smoke_all_ok():
    seen = []
    with client_for(seen=seen) as client:
        assert run_smoke('https://example.test', client) == 0
    assert seen[:2] == [None, 'Bearer abc']
    expired = jwt.decode(seen[2].split()[1], options={'verify_signature': False})
    assert date.fromtimestamp(expired['exp']) < datetime.now(UTC).date()


@pytest.mark.parametrize('kwargs', [
    {'auth_status': 200}, {'health': 503}, {'content_type': 'application/json'},
    {'root': '<html>missing app</html>'},
])
def test_smoke_rejects_bad_response(kwargs):
    with client_for(**kwargs) as client:
        assert run_smoke('https://example.test', client) == 1


def test_smoke_output_has_no_authorization(capsys):
    with client_for(auth_status=200) as client:
        run_smoke('https://example.test', client)
    output = capsys.readouterr()
    assert 'Bearer' not in output.out + output.err
    assert 'authorization' not in (output.out + output.err).lower()


def test_smoke_network_failure_has_no_sensitive_details(capsys):
    def handle(request):
        raise httpx.ConnectError('Bearer secret', request=request)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert run_smoke('https://example.test', client) == 1
    assert 'secret' not in str(capsys.readouterr())
