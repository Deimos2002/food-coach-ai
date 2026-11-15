"""Portable backup names and Blob storage boundary."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


@dataclass(frozen=True)
class BlobInfo:
    name: str
    created: datetime
    deleted: bool


class BlobStore(Protocol):
    def upload(self, name: str, path: Path) -> None: ...
    def download(self, name: str, path: Path) -> None: ...
    def list_all(self, include_deleted: bool) -> list[BlobInfo]: ...


class AzureBlobStore:
    def __init__(self, account_url: str, container: str = "backups"):
        self.container = BlobServiceClient(
            account_url, credential=DefaultAzureCredential()
        ).get_container_client(container)

    def upload(self, name: str, path: Path) -> None:
        with path.open("rb") as stream:
            self.container.upload_blob(name=name, data=stream, overwrite=False)

    def download(self, name: str, path: Path) -> None:
        with path.open("wb") as stream:
            self.container.download_blob(name).readinto(stream)

    def list_all(self, include_deleted: bool) -> list[BlobInfo]:
        blobs = self.container.list_blobs(include=["deleted"] if include_deleted else [])
        return [BlobInfo(b.name, b.creation_time, bool(b.deleted)) for b in blobs]


def dump_name(now: datetime, sha: str) -> str:
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{sha}.dump"
