import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
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

    def test_running_revision_uses_listener_process_not_active_manifest(self):
        base = "http://127.0.0.1:8790"
        revision = "a" * 40
        connection = SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(ip="127.0.0.1", port=8790), pid=42)
        process = SimpleNamespace(cmdline=lambda: ["node.exe", str(bm.STORE / revision / "bridge/workbuddy-bridge.mjs")])
        with patch.object(bm.psutil, "net_connections", return_value=[connection]), \
             patch.object(bm.psutil, "Process", return_value=process), \
             patch.object(bm, "active", return_value=(bm.STORE / ("b" * 40), "b" * 40)):
            self.assertEqual(bm.running_revision(base), revision)
        foreign = SimpleNamespace(cmdline=lambda: ["node.exe", "C:/other/bridge/workbuddy-bridge.mjs"])
        with patch.object(bm.psutil, "net_connections", return_value=[connection]), \
             patch.object(bm.psutil, "Process", return_value=foreign):
            self.assertIsNone(bm.running_revision(base))

    def test_recovers_bridge_through_existing_dashboard(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"started": True}
        class Client:
            def __init__(self, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def post(self, url, headers):
                self.url = url
                assert headers == {"x-workbuddy-panel": "1"}
                return Response()
        with patch.object(bm, "ensure_initial", return_value=(bm.LEGACY, bm.BASE_REVISION)), \
             patch.object(bm, "port_open", return_value=False), \
             patch.object(bm, "listener_source", return_value=bm.LEGACY.resolve()), \
             patch.object(bm.httpx, "Client", Client), \
             patch.object(bm.subprocess, "Popen") as spawn:
            self.assertEqual(bm.start_bridge("http://127.0.0.1:8790"), (None, bm.BASE_REVISION))
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
