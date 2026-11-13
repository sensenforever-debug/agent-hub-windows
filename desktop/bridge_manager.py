"""Keep upstream source separate from the gateway desktop application."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse
import httpx
import psutil
import re

UPSTREAM = "Ianzhyh/workbuddy-to-dsh"
BASE_REVISION = "2f3c556d38896164bf138627b484662aa6f4d345"
APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "AgentGatewayNode"
STORE = APP_DATA / "upstream"
MANIFEST = STORE / "active.json"
LEGACY = APP_DATA / "workbuddy-to-dsh"
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def bundle_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def node_exe():
    bundled = bundle_dir() / "runtime" / "node.exe"
    return str(bundled if bundled.is_file() else shutil.which("node") or "")


def bridge_url(base):
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.port:
        raise ValueError("本地桥地址须为 http://127.0.0.1:端口")
    return base.rstrip("/")


def active():
    if MANIFEST.is_file():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        path = STORE / data["revision"]
        if path.is_dir():
            return path, data["revision"]
    if LEGACY.is_dir():
        return LEGACY, BASE_REVISION
    return None, None


def _write_manifest(revision, previous=None):
    STORE.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps({"revision": revision, "previous": previous}), encoding="utf-8")
    tmp.replace(MANIFEST)


def _install_archive(archive, revision):
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("无效的上游提交")
    destination = STORE / revision
    if destination.is_dir():
        return destination
    STORE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=STORE) as temp:
        staging = Path(temp)
        with zipfile.ZipFile(archive) as z:
            members = z.infolist()
            for member in members:
                parts = Path(member.filename).parts
                if len(parts) < 2 or ".." in parts or member.filename.startswith("/"):
                    raise ValueError("上游 ZIP 路径不安全")
                target = staging.joinpath(*parts[1:]).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise ValueError("上游 ZIP 路径不安全")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        for name in ("LICENSE", "package.json", "dashboard/server.mjs", "bridge/workbuddy-bridge.mjs"):
            if not (staging / name).is_file():
                raise ValueError(f"上游包缺少 {name}")
        package = json.loads((staging / "package.json").read_text(encoding="utf-8"))
        if package.get("name") != "workbuddy-to-dsh":
            raise ValueError("上游包名称不符")
        shutil.move(str(staging), str(destination))
    return destination


def ensure_initial():
    current, revision = active()
    if current:
        return current, revision
    archive = bundle_dir() / "vendor" / "upstream.zip"
    if not archive.is_file():
        raise FileNotFoundError("安装包缺少上游源码")
    _install_archive(archive, BASE_REVISION)
    _write_manifest(BASE_REVISION)
    return active()


def latest():
    req = urllib.request.Request(f"https://api.github.com/repos/{UPSTREAM}/commits/main",
                                 headers={"User-Agent": "AgentGatewayNode/1"})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = json.load(response)
    return data["sha"], data["commit"]["message"].splitlines()[0]


def download(revision):
    archive_url = f"https://github.com/{UPSTREAM}/archive/{revision}.zip"
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / "source.zip"
        urllib.request.urlretrieve(archive_url, archive)
        return _install_archive(archive, revision)


def activate(revision, bridge_base, token, dashboard_port=8792):
    """Validate, switch, then health-check; restore prior source on failure."""
    candidate = STORE / revision
    if not candidate.is_dir():
        raise FileNotFoundError("候选源码不存在")
    if not node_exe():
        raise FileNotFoundError("缺少 Node.js 运行时")
    # A separate source tree must parse successfully before changing the pointer.
    for file in ("dashboard/server.mjs", "bridge/workbuddy-bridge.mjs"):
        check = subprocess.run([node_exe(), "--check", str(candidate / file)], capture_output=True, timeout=15)
        if check.returncode:
            raise RuntimeError(f"上游语法检查失败：{file}")
    old, old_revision = active()
    if old_revision == revision:
        return old_revision
    if old_revision == BASE_REVISION and old == LEGACY and not (STORE / BASE_REVISION).exists():
        shutil.copytree(LEGACY, STORE / BASE_REVISION)
    stop_bridge(bridge_base, dashboard_port, old)
    _write_manifest(revision, old_revision)
    try:
        start_bridge(bridge_base, dashboard_port)
        verify_models(bridge_base, token)
    except Exception:
        stop_bridge(bridge_base, dashboard_port, candidate)
        _write_manifest(old_revision, revision)
        start_bridge(bridge_base, dashboard_port)
        raise
    return old_revision


def rollback(bridge_base, token, dashboard_port=8792):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    previous = data.get("previous")
    if not previous or not (STORE / previous).is_dir():
        raise RuntimeError("没有可回退的版本")
    current, _ = active()
    stop_bridge(bridge_base, dashboard_port, current)
    _write_manifest(previous, data["revision"])
    try:
        start_bridge(bridge_base, dashboard_port)
        verify_models(bridge_base, token)
    except Exception:
        stop_bridge(bridge_base, dashboard_port, STORE / previous)
        _write_manifest(data["revision"], previous)
        start_bridge(bridge_base, dashboard_port)
        raise
    return previous


def verify_models(base, token, timeout=45):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5, trust_env=False) as client:
                response = client.get(bridge_url(base) + "/v1/models",
                                      headers={"Authorization": "Bearer " + token})
                response.raise_for_status()
                names = [x.get("id") for x in response.json().get("data", [])]
                if any(isinstance(name, str) for name in names):
                    return names
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("新桥未能在 45 秒内提供模型目录")


def stop_bridge(base, dashboard_port, source):
    """Stop only the known local dashboard and its own bridge."""
    if source is None:
        return
    dashboard = f"http://127.0.0.1:{int(dashboard_port)}"
    try:
        with httpx.Client(timeout=12, trust_env=False) as client:
            client.post(dashboard + "/api/bridge/stop", headers={"x-workbuddy-panel": "1"})
    except httpx.HTTPError:
        pass
    server_script = str((source / "dashboard" / "server.mjs").resolve()).lower()
    for process in psutil.process_iter(["pid", "cmdline", "username"]):
        try:
            args = process.info["cmdline"] or []
            if any(str(arg).lower() == server_script for arg in args):
                process.terminate()
                process.wait(timeout=8)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            pass


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def listener_source(port, folder, script_name):
    """Find the source tree of a known listener without trusting the active manifest."""
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return None
    for connection in connections:
        if (connection.status != psutil.CONN_LISTEN or not connection.laddr or
                connection.laddr.port != port or not connection.pid or
                connection.laddr.ip not in ("127.0.0.1", "::1")):
            continue
        try:
            arguments = psutil.Process(connection.pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
        for argument in arguments:
            script = Path(argument)
            if script.name.lower() != script_name or script.parent.name.lower() != folder:
                continue
            return script.parent.parent.resolve()
    return None


def running_revision(base):
    """Identify the source tree of the process actually listening on the bridge port."""
    source = listener_source(urlparse(bridge_url(base)).port, "bridge", "workbuddy-bridge.mjs")
    if source == LEGACY.resolve():
        return BASE_REVISION
    if source and source.parent == STORE.resolve() and REVISION_RE.fullmatch(source.name):
        return source.name
    return None


def start_bridge(base, dashboard_port=8792):
    bridge_url(base)
    path, revision = ensure_initial()
    if port_open(urlparse(base).port):
        return None, revision
    dashboard_source = listener_source(int(dashboard_port), "dashboard", "server.mjs")
    if dashboard_source:
        if dashboard_source != path.resolve():
            raise RuntimeError("运行中的控制台与所选桥版本不一致")
        with httpx.Client(timeout=20, trust_env=False) as client:
            response = client.post(f"http://127.0.0.1:{int(dashboard_port)}/api/bridge/start",
                                   headers={"x-workbuddy-panel": "1"})
            response.raise_for_status()
            if not response.json().get("started"):
                raise RuntimeError("控制台未能恢复 WorkBuddy 桥")
        return None, revision
    if port_open(int(dashboard_port)):
        raise RuntimeError("控制台端口被未知进程占用")
    if not node_exe():
        raise FileNotFoundError("缺少 Node.js 运行时")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen([node_exe(), str(path / "dashboard" / "server.mjs")], cwd=path,
                               creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process, revision
