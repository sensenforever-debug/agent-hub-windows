"""Run the pinned raccoon2api source on loopback without modifying upstream files."""
import importlib.util
import hmac
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from raccoon_adapter import INTERNAL_KEY, access_token


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(("127.0.0.1", address[1]), handler)


class RaccoonRunner:
    def __init__(self, source: Path, port: int = 8791):
        self.source = source
        self.port = port
        self.thread = None
        self.server = None
        self.module = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        os.environ["RACCOON_PORT"] = str(self.port)
        spec = importlib.util.spec_from_file_location("raccoon2api_upstream", self.source / "main.py")
        if spec is None or spec.loader is None:
            raise FileNotFoundError(self.source / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ThreadingHTTPServer = self._server_factory
        module.get_access_token = lambda request_header_token=None: access_token()
        module.RaccoonProxyHandler._send_cors_headers = lambda self: None
        for method_name in ("do_GET", "do_POST", "do_OPTIONS"):
            original = getattr(module.RaccoonProxyHandler, method_name)
            def guarded(handler, delegate=original):
                supplied = handler.headers.get("X-Agent-Hub-Internal", "")
                if not hmac.compare_digest(supplied, INTERNAL_KEY):
                    handler.send_error(403)
                    return
                return delegate(handler)
            setattr(module.RaccoonProxyHandler, method_name, guarded)
        module.print = lambda *args, **kwargs: None  # Upstream banner contains emoji; GBK stdout may reject it.
        module.RaccoonProxyHandler.log_message = lambda *args, **kwargs: None
        self.module = module
        self.thread = threading.Thread(target=module.run_server, name="raccoon2api", daemon=True)
        self.thread.start()

    def _server_factory(self, address, handler):
        self.server = LoopbackServer(address, handler)
        return self.server

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=5)
