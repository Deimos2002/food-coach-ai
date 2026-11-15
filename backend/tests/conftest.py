import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from blobstore import BlobInfo

_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect


def _guarded_connect(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if host not in _ALLOWED_HOSTS:
        raise RuntimeError(
            f"Tests may not open real network connections to {host!r}. "
            "Use FakeLlmClient or respx to mock any HTTP call."
        )
    return _real_connect(self, address, *args, **kwargs)


@pytest.fixture(autouse=True)
def _block_non_local_sockets():
    """Defense in depth: no test may reach the real network except localhost (Postgres)."""
    socket.socket.connect = _guarded_connect
    try:
        yield
    finally:
        socket.socket.connect = _real_connect


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip_integration = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


@pytest.fixture
def db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


class FakeBlobStore:
    def __init__(self, blobs=()):
        self.blobs = list(blobs)
        self.contents = {}
        self.uploaded_paths = []

    def upload(self, name, path):
        if name in self.contents:
            raise FileExistsError(name)
        self.contents[name] = path.read_bytes()
        self.uploaded_paths.append(path)
        self.blobs.append(BlobInfo(name, datetime.now(UTC), False))

    def download(self, name, path):
        path.write_bytes(self.contents[name])

    def list_all(self, include_deleted):
        return [blob for blob in self.blobs if include_deleted or not blob.deleted]


@pytest.fixture
def fake_blob_store():
    return FakeBlobStore
