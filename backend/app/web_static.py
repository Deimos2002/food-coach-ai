"""Serve the built web app (spec §3.1): real files, else ``index.html`` for SPA deep links."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def mount_static(app: FastAPI, static_dir: Path, api_prefix: str) -> None:
    """Register a GET catch-all. Call after every router so API routes win."""
    root = static_dir.resolve()
    index = root / "index.html"
    prefix = api_prefix.strip("/")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if prefix and (full_path == prefix or full_path.startswith(prefix + "/")):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (root / full_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)
