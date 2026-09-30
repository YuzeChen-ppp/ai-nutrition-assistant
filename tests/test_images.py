"""Photo validation, resizing and base64 encoding."""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.utils.images import (
    ImageError,
    UnsupportedImageError,
    human_size,
    prepare_image_for_api,
)


class ImagePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, size: tuple[int, int], mode: str = "RGB") -> Path:
        path = self.dir / name
        Image.new(mode, size, (200, 120, 60) if mode == "RGB" else (200, 120, 60, 128)).save(path)
        return path

    def test_small_jpeg_is_encoded(self) -> None:
        prepared = prepare_image_for_api(self._make("meal.jpg", (600, 400)))
        self.assertTrue(prepared.data_url.startswith("data:image/jpeg;base64,"))
        payload = base64.b64decode(prepared.data_url.split(",", 1)[1])
        self.assertTrue(payload.startswith(b"\xff\xd8"))  # JPEG magic bytes
        self.assertLessEqual(max(prepared.width, prepared.height), 1280)

    def test_large_photo_is_resized(self) -> None:
        prepared = prepare_image_for_api(self._make("big.jpg", (4000, 3000)))
        self.assertTrue(prepared.resized)
        self.assertLessEqual(max(prepared.width, prepared.height), 1280)
        self.assertEqual(prepared.resize_note, "Image resized for analysis.")

    def test_png_with_transparency_is_flattened(self) -> None:
        prepared = prepare_image_for_api(self._make("plate.png", (500, 500), mode="RGBA"))
        self.assertTrue(prepared.data_url.startswith("data:image/jpeg;base64,"))

    def test_webp_is_supported(self) -> None:
        path = self.dir / "meal.webp"
        Image.new("RGB", (400, 300), (10, 200, 10)).save(path)
        prepared = prepare_image_for_api(path)
        self.assertGreater(prepared.prepared_bytes, 0)

    def test_unsupported_extension_is_rejected(self) -> None:
        path = self.dir / "notes.txt"
        path.write_text("hello", encoding="utf-8")
        with self.assertRaises(UnsupportedImageError):
            prepare_image_for_api(path)

    def test_corrupt_image_is_rejected(self) -> None:
        path = self.dir / "broken.png"
        path.write_bytes(b"this is not an image")
        with self.assertRaises(UnsupportedImageError):
            prepare_image_for_api(path)

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(ImageError):
            prepare_image_for_api(self.dir / "does-not-exist.jpg")

    def test_human_size(self) -> None:
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(2048), "2.0 KB")
        self.assertEqual(human_size(3 * 1024 * 1024), "3.0 MB")


if __name__ == "__main__":
    unittest.main()
