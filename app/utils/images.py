"""Food photo preparation.

Photos are validated, rotated according to their EXIF orientation and resized
or re-compressed before being sent to the API. Sending a 12 megapixel phone
photo is slow, expensive and unnecessary: the model downscales large images
anyway, so we do it locally and keep the request small.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.i18n import tr

__all__ = [
    "FILE_DIALOG_FILTER",
    "MAX_UPLOAD_BYTES",
    "SUPPORTED_EXTENSIONS",
    "ImageError",
    "ImageTooLargeError",
    "PreparedImage",
    "UnsupportedImageError",
    "human_size",
    "prepare_image_for_api",
]

# Formats accepted by the DeepSeek vision API (and by the file dialog).
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
FILE_DIALOG_FILTER = "Food photos (*.jpg *.jpeg *.png *.webp *.gif)"

# DeepSeek limits: max 32 MiB per inline image, 48 MiB per request body.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

# Our own, much stricter targets: keeps requests fast and cheap.
MAX_DIMENSION = 1280
TARGET_MAX_BYTES = 3 * 1024 * 1024


class ImageError(Exception):
    """Base class for image problems. ``user_message`` is UI ready."""

    message_key = "error.image_generic"

    def __init__(self, message: str | None = None) -> None:
        self.user_message = message or tr(self.message_key)
        super().__init__(self.user_message)


class UnsupportedImageError(ImageError):
    message_key = "error.image_unsupported"


class ImageTooLargeError(ImageError):
    message_key = "error.image_too_large"


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Result of preparing a photo for the vision API."""

    original_path: Path
    data_url: str
    width: int
    height: int
    original_bytes: int
    prepared_bytes: int
    resized: bool

    @property
    def resize_note(self) -> str:
        return "Image resized for analysis." if self.resized else ""


def human_size(num_bytes: int) -> str:
    """Format a byte count for humans, e.g. ``2.4 MB``."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def prepare_image_for_api(
    path: str | Path,
    *,
    max_dimension: int = MAX_DIMENSION,
    target_bytes: int = TARGET_MAX_BYTES,
) -> PreparedImage:
    """Validate, resize and encode a photo as a base64 data URL."""
    image_path = Path(path)
    if not image_path.is_file():
        raise ImageError(tr("error.file_missing"))
    if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedImageError()

    original_bytes = image_path.stat().st_size
    if original_bytes > MAX_UPLOAD_BYTES:
        raise ImageTooLargeError()

    try:
        with Image.open(image_path) as raw:
            raw.load()
            image = ImageOps.exif_transpose(raw)
            if image is None:  # pragma: no cover - Pillow always returns an image
                image = raw
            image = image.convert("RGBA") if _has_alpha(image) else image.convert("RGB")
            prepared_bytes, width, height = _encode(
                image,
                max_dimension=max_dimension,
                target_bytes=target_bytes,
            )
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError(tr("error.image_unreadable")) from exc
    except OSError as exc:
        raise ImageError(tr("error.image_read_failed", detail=str(exc))) from exc

    if len(prepared_bytes) > MAX_UPLOAD_BYTES:  # pragma: no cover - very unlikely
        raise ImageTooLargeError()

    encoded = base64.b64encode(prepared_bytes).decode("ascii")
    return PreparedImage(
        original_path=image_path,
        data_url=f"data:image/jpeg;base64,{encoded}",
        width=width,
        height=height,
        original_bytes=original_bytes,
        prepared_bytes=len(prepared_bytes),
        resized=len(prepared_bytes) != original_bytes or _needs_resize_size(image_path, max_dimension),
    )


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _needs_resize_size(path: Path, max_dimension: int) -> bool:
    try:
        with Image.open(path) as image:
            return max(image.size) > max_dimension
    except (UnidentifiedImageError, OSError):  # pragma: no cover - defensive
        return False


def _encode(
    image: Image.Image,
    *,
    max_dimension: int,
    target_bytes: int,
) -> tuple[bytes, int, int]:
    """Encode ``image`` as JPEG, shrinking until it fits ``target_bytes``."""
    scale = 1.0
    quality = 88
    best: tuple[bytes, int, int] | None = None

    for _ in range(6):
        working = image
        if scale < 1.0:
            new_size = (
                max(1, int(image.width * scale)),
                max(1, int(image.height * scale)),
            )
            working = image.resize(new_size, Image.Resampling.LANCZOS)
        # JPEG has no transparency: flatten onto white.
        if working.mode in {"RGBA", "LA", "P"}:
            background = Image.new("RGB", working.size, (255, 255, 255))
            background.paste(working, mask=working.convert("RGBA").split()[-1])
            working = background
        buffer = io.BytesIO()
        working.save(buffer, format="JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()
        best = (data, working.width, working.height)
        if len(data) <= target_bytes and max(working.width, working.height) <= max_dimension:
            break
        if max(working.width, working.height) > max_dimension:
            scale = min(scale, max_dimension / max(image.width, image.height))
        else:
            scale = scale * 0.85
        quality = max(60, quality - 8)

    assert best is not None  # loop always runs at least once
    return best[0], best[1], best[2]
