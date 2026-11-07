"""Validate actual image bytes and keep arbitrary upload names off disk."""

from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, UnidentifiedImageError

from .database import new_id
from .domain import DomainError

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageStore:
    """Content-address isolation using random keys and constrained formats."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def validate(self, data: bytes) -> tuple[str, str]:
        """Inspect magic bytes and decode bounds, not the claimed MIME type."""
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise DomainError("图片大小须为1字节至5MB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as image:
                    kind = image.format
                    if kind not in {"PNG", "JPEG"}:
                        raise DomainError("只支持真实JPEG或PNG图片")
                    image.verify()
        except (UnidentifiedImageError, OSError, ValueError,
                Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise DomainError("图片损坏或尺寸异常") from exc
        return ("image/png", ".png") if kind == "PNG" else ("image/jpeg", ".jpg")

    def save(self, data: bytes) -> dict:
        """Create a unique file; the caller rolls it back if its DB write fails."""
        content_type, extension = self.validate(data)
        key = new_id() + extension
        path = self.directory / key
        with path.open("xb") as output:
            output.write(data)
        return {"storage_key": key, "content_type": content_type, "byte_size": len(data)}

    def remove(self, key: str):
        """Delete only previously generated immediate-child files."""
        path = (self.directory / key).resolve()
        if path.parent != self.directory:
            raise ValueError("Invalid storage key")
        path.unlink(missing_ok=True)

    def path(self, key: str) -> Path:
        """Resolve a key after the service has checked visibility."""
        path = (self.directory / key).resolve()
        if path.parent != self.directory or not path.is_file():
            raise DomainError("图片不存在", 404, "NOT_FOUND")
        return path
