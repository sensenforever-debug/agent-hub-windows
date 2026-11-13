import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import bridge_manager as bm


class BridgeManagerTests(unittest.TestCase):
    def test_installs_pinned_archive_without_writing_outside_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.zip"
            prefix = "workbuddy-to-dsh-" + bm.BASE_REVISION + "/"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(prefix + "LICENSE", "MIT")
                z.writestr(prefix + "package.json", json.dumps({"name": "workbuddy-to-dsh"}))
                z.writestr(prefix + "dashboard/server.mjs", "console.log('ok')")
                z.writestr(prefix + "bridge/workbuddy-bridge.mjs", "console.log('ok')")
            with patch.object(bm, "STORE", root / "versions"):
                installed = bm._install_archive(archive, bm.BASE_REVISION)
                self.assertTrue((installed / "dashboard/server.mjs").is_file())

    def test_rejects_traversal_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("repo/../../escape", "bad")
            with patch.object(bm, "STORE", root / "versions"):
                with self.assertRaises(ValueError):
                    bm._install_archive(archive, bm.BASE_REVISION)
                self.assertFalse((root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
