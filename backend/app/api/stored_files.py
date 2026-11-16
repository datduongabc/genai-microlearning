"""How the browser gets a stored file: a short-lived S3 link it follows itself, or, without S3, an
API path it fetches with its token, where the API sends the file itself. Shared by a study's
reference files and a rendered video."""
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.storage import LocalUploadStore, S3UploadStore, UploadStore


def download_link(store: UploadStore, key: str, filename: str, api_path: str) -> dict:
    if isinstance(store, S3UploadStore):
        return {"url": store.presigned_get(key, filename), "via_api": False, "filename": filename}
    return {"url": api_path, "via_api": True, "filename": filename}


def send_stored(store: UploadStore, key: str, filename: str) -> FileResponse:
    """The file itself, from the local store only: on S3 the link above is the way, and this path
    answers as if there were nothing there."""
    if not isinstance(store, LocalUploadStore):
        raise HTTPException(status_code=404, detail="Not found")
    path = store.path(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=filename)
