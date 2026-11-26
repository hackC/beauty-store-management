"""Pillow image processor and safe local product-image store adapters."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .domain import CatalogError


class PillowImageProcessor:
    ACCEPTED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
    MAX_BYTES = 6 * 1024 * 1024

    def process(self, data: bytes, content_type: str) -> bytes:
        if content_type not in self.ACCEPTED_TYPES:
            raise CatalogError(415, "只接受 JPEG、PNG 或 WebP 图片")
        if len(data) > self.MAX_BYTES:
            raise CatalogError(413, "图片不能超过 6MB")
        try:
            with Image.open(io.BytesIO(data)) as source:
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                if source.width > 6000 or source.height > 6000 or source.width * source.height > 20_000_000:
                    raise CatalogError(413, "图片分辨率过高")
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                side = min(oriented.size)
                x = (oriented.width - side) // 2
                y = (oriented.height - side) // 2
                cropped = oriented.crop((x, y, x + side, y + side)).resize((640, 640), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                cropped.save(output, format="JPEG", quality=82, optimize=True)
                return output.getvalue()
        except CatalogError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise CatalogError(400, "图片内容不正确") from exc


class FileProductImageStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate(filename: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{32}\.jpg", str(filename or "")):
            raise CatalogError(404, "图片引用无效")
        return filename

    def save(self, filename: str, data: bytes) -> None:
        filename = self._validate(filename)
        destination = self.directory / filename
        temporary = self.directory / (filename + ".tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise

    def delete(self, filename: str) -> None:
        filename = self._validate(filename)
        (self.directory / filename).unlink(missing_ok=True)
        (self.directory / (filename + ".tmp")).unlink(missing_ok=True)

    def path(self, filename: str) -> Path:
        return self.directory / self._validate(filename)
