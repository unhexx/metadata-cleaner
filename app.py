import io
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import piexif
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from PIL import ExifTags, Image
from PIL.PngImagePlugin import PngInfo
from pydantic import BaseModel

PROJECT_URL = "https://exception.expert"
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "storage"))
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TTL_HOURS = int(os.getenv("TTL_HOURS", "24"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "300"))

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}

app = FastAPI(title="Metadata Cleaner", version="1.0.0")


class ProcessResponse(BaseModel):
    original_filename: str
    extracted_exif: dict[str, Any]
    download_url: str
    expires_at: str


class StoredFile(BaseModel):
    filename: str
    path: Path
    expires_at: datetime


file_registry: dict[str, StoredFile] = {}
registry_lock = threading.Lock()


def ensure_storage() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def is_supported(file: UploadFile) -> bool:
    suffix = Path(file.filename or "").suffix.lower()
    return suffix in ALLOWED_EXTENSIONS and (file.content_type or "").lower() in ALLOWED_CONTENT_TYPES


def decode_exif_values(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return value.hex()
    if isinstance(value, tuple):
        return [decode_exif_values(v) for v in value]
    if isinstance(value, dict):
        return {str(k): decode_exif_values(v) for k, v in value.items()}
    return value


def extract_exif_data(image_bytes: bytes) -> dict[str, Any]:
    data: dict[str, Any] = {}
    with Image.open(io.BytesIO(image_bytes)) as img:
        data["format"] = img.format
        data["mode"] = img.mode
        data["size"] = {"width": img.width, "height": img.height}

        pil_exif = img.getexif()
        if pil_exif:
            named_exif = {}
            for tag_id, value in pil_exif.items():
                tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                named_exif[str(tag)] = decode_exif_values(value)
            data["pil_exif"] = named_exif

    try:
        piexif_data = piexif.load(image_bytes)
        structured = {k: decode_exif_values(v) for k, v in piexif_data.items() if k != "thumbnail"}
        if piexif_data.get("thumbnail"):
            structured["thumbnail"] = "present"
        data["piexif"] = structured
    except Exception:
        data["piexif"] = {}

    return data


def clean_metadata(file_name: str, image_bytes: bytes) -> tuple[bytes, str]:
    suffix = Path(file_name).suffix.lower()
    with Image.open(io.BytesIO(image_bytes)) as img:
        output = io.BytesIO()

        if suffix in {".jpg", ".jpeg"}:
            rgb = img.convert("RGB")
            exif_dict = {
                "0th": {
                    piexif.ImageIFD.ImageDescription: PROJECT_URL.encode("utf-8"),
                    piexif.ImageIFD.Software: b"metadata-cleaner",
                },
                "Exif": {},
                "GPS": {},
                "1st": {},
                "thumbnail": None,
            }
            exif_bytes = piexif.dump(exif_dict)
            rgb.save(output, format="JPEG", quality=95, exif=exif_bytes)
            return output.getvalue(), ".jpg"

        if suffix == ".png":
            png_info = PngInfo()
            png_info.add_text("ProjectURL", PROJECT_URL)
            sanitized = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img.copy()
            sanitized.save(output, format="PNG", pnginfo=png_info)
            return output.getvalue(), ".png"

    raise HTTPException(status_code=400, detail="Unsupported image format")


def store_file(content: bytes, ext: str) -> tuple[str, datetime]:
    ensure_storage()
    file_id = secrets.token_urlsafe(12)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=TTL_HOURS)
    filename = f"{file_id}{ext}"
    path = STORAGE_DIR / filename
    path.write_bytes(content)

    with registry_lock:
        file_registry[file_id] = StoredFile(filename=filename, path=path, expires_at=expires_at)

    return file_id, expires_at


def cleanup_expired() -> None:
    now = datetime.now(timezone.utc)
    expired_ids: list[str] = []

    with registry_lock:
        for file_id, item in file_registry.items():
            if item.expires_at <= now:
                expired_ids.append(file_id)

        for file_id in expired_ids:
            item = file_registry.pop(file_id)
            if item.path.exists():
                item.path.unlink(missing_ok=True)


def cleanup_worker() -> None:
    while True:
        cleanup_expired()
        threading.Event().wait(CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
def on_startup() -> None:
    ensure_storage()
    t = threading.Thread(target=cleanup_worker, daemon=True)
    t.start()


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return """
    <html>
      <head><title>Metadata Cleaner</title></head>
      <body>
        <h1>Metadata Cleaner</h1>
        <p>Загрузите PNG/JPG/JPEG, сервис извлечёт EXIF, очистит метаданные и оставит URL проекта.</p>
        <form action="/api/process" method="post" enctype="multipart/form-data">
          <input type="file" name="file" accept=".png,.jpg,.jpeg" required />
          <button type="submit">Очистить</button>
        </form>
      </body>
    </html>
    """


@app.post("/api/process", response_model=ProcessResponse)
async def process_image(file: UploadFile = File(...)) -> ProcessResponse:
    if not is_supported(file):
        raise HTTPException(status_code=400, detail="Only PNG/JPG/JPEG are supported")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="File is empty")

    exif_data = extract_exif_data(image_bytes)
    cleaned, ext = clean_metadata(file.filename or "uploaded", image_bytes)
    file_id, expires_at = store_file(cleaned, ext)

    return ProcessResponse(
        original_filename=file.filename or "uploaded",
        extracted_exif=exif_data,
        download_url=f"{BASE_URL}/download/{file_id}",
        expires_at=expires_at.isoformat(),
    )


@app.get("/download/{file_id}")
def download(file_id: str) -> FileResponse:
    with registry_lock:
        item = file_registry.get(file_id)

    if not item:
        raise HTTPException(status_code=404, detail="File not found or expired")

    if item.expires_at <= datetime.now(timezone.utc):
        with registry_lock:
            file_registry.pop(file_id, None)
        if item.path.exists():
            item.path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="File expired")

    return FileResponse(item.path, filename=item.filename, media_type="application/octet-stream")
