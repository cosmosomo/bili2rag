from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bilibili_library.exporter import _write_manifest


class TestLibraryManifest(unittest.TestCase):
    def test_write_manifest_describes_exported_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "asr").mkdir()
            (root / "asr" / "transcript.txt").write_text("hello", encoding="utf-8")
            (root / "audio.m4a").write_bytes(b"abc")

            _write_manifest(root)

            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            paths = {item["path"] for item in manifest["files"]}
            self.assertIn("asr\\transcript.txt", paths)
            self.assertIn("audio.m4a", paths)


if __name__ == "__main__":
    unittest.main()
