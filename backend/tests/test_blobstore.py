from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from blobstore import AzureBlobStore, BlobInfo, dump_name


def test_dump_name_format():
    assert dump_name(datetime(2027, 1, 4, 2, 30, tzinfo=UTC), "abc123") == (
        "20270104T023000Z-abc123.dump"
    )


def test_fake_and_azure_upload_refuse_overwrite(monkeypatch, tmp_path, fake_blob_store):
    path = tmp_path / "backup.dump"
    path.write_bytes(b"dump")
    fake = fake_blob_store()
    fake.upload("backup", path)
    with pytest.raises(FileExistsError):
        fake.upload("backup", path)
    client = Mock()
    monkeypatch.setattr("blobstore.DefaultAzureCredential", Mock())
    monkeypatch.setattr("blobstore.BlobServiceClient", Mock(return_value=client))
    store = AzureBlobStore("https://example.blob.core.windows.net")
    store.upload("backup", path)
    _args, kwargs = client.get_container_client.return_value.upload_blob.call_args
    assert kwargs["name"] == "backup"
    assert kwargs["overwrite"] is False


def test_azure_list_includes_deleted(monkeypatch):
    client = Mock()
    created = datetime(2027, 1, 4, tzinfo=UTC)
    container = client.get_container_client.return_value
    container.list_blobs.return_value = [
        SimpleNamespace(name="old.dump", creation_time=created, deleted=True)
    ]
    monkeypatch.setattr("blobstore.DefaultAzureCredential", Mock())
    monkeypatch.setattr("blobstore.BlobServiceClient", Mock(return_value=client))
    store = AzureBlobStore("https://example.blob.core.windows.net")
    assert store.list_all(True) == [BlobInfo("old.dump", created, True)]
    container.list_blobs.assert_called_once_with(include=["deleted"])
