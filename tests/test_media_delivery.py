from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from astrbot_plugin_kirby_catalog.media_delivery import prepare_image_for_delivery


class DeliveryCacheTests(unittest.TestCase):
    def test_cache_is_published_only_after_jpeg_encoding_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            output = root / "delivery"
            Image.new("RGB", (128, 64), "pink").save(source)
            writing = threading.Event()
            release = threading.Event()
            original_save = Image.Image.save

            def save_image(image, path, *args, **kwargs):
                # A real interrupted encoder may leave a JPEG prefix on disk.
                Path(path).write_bytes(b"\xff\xd8\xff")
                writing.set()
                if not release.wait(2):
                    raise TimeoutError("encoder was not released")
                return original_save(image, path, *args, **kwargs)

            with (
                patch.object(Image.Image, "save", save_image),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                pending = executor.submit(
                    prepare_image_for_delivery, source, output, max_width=64
                )
                try:
                    self.assertTrue(writing.wait(2))
                    published = [
                        path for path in output.glob("kirby-delivery-*.jpg")
                        if "-tmp-" not in path.name
                    ]
                    self.assertEqual(published, [])
                finally:
                    release.set()
                result = pending.result(timeout=3)
            with Image.open(result) as image:
                image.load()
                self.assertEqual(image.size, (64, 32))
            self.assertEqual(list(output.glob("*-tmp-*.jpg")), [])

    def test_failed_encoder_leaves_no_delivery_cache_or_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            output = root / "delivery"
            Image.new("RGB", (128, 64), "pink").save(source)

            def fail_after_open(_image, path, *args, **kwargs):
                Path(path).write_bytes(b"\xff\xd8\xff")
                raise OSError("encoder interrupted")

            with patch.object(Image.Image, "save", fail_after_open):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    prepare_image_for_delivery(source, output, max_width=64)
            self.assertEqual(list(output.iterdir()), [])
