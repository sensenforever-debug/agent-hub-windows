"""WorkBuddy gateway node desktop application (one instance per Windows user)."""
import asyncio
import ctypes
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import tkinter as tk
from tkinter import messagebox, ttk

import httpx
import keyring
import psutil

import bridge_manager as bm
import node

CONFIG = bm.APP_DATA / "config.json"
VALIDATIONS = bm.APP_DATA / "validations.json"
SERVICE = node.SERVICE
DEFAULT_GATEWAY = "wss://api.435038537.xyz/node/ws"


def redact(value):
    text = str(value)
    for marker in ("agk_", "agn_", "agp_", "Bearer "):
        start = text.find(marker)
        if start >= 0:
            end = text.find(" ", start + len(marker))
            text = text[:start] + "[已隐藏]" + (text[end:] if end >= 0 else "")
    return text[:500]


def single_instance():
    if os.name != "nt":
        return None
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\AgentGatewayNode-" + os.environ.get("USERNAME", "user"))
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise SystemExit("节点程序已运行")
    return handle


def migrate_startup():
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    target = startup / "AgentGatewayNode.cmd"
    if getattr(sys, "frozen", False):
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                args = process.info["cmdline"] or []
                if any(str(arg).replace("/", "\\").lower().endswith("\\workbuddy-node\\node_gui.py") for arg in args):
                    process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            winreg.SetValueEx(key, "AgentGatewayNode", 0, winreg.REG_SZ, '"' + sys.executable + '"')
        target.unlink(missing_ok=True)
    return target


class Application:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = self.loop = self.task = None
        self.bridge_process = None
        self.tray = None
        self.exiting = False
        self.last_probe = "尚无验收记录"
        cfg = self.load_config()
        self.gateway = tk.StringVar(value=cfg.get("gateway", DEFAULT_GATEWAY))
        self.bridge = tk.StringVar(value=cfg.get("bridge", "http://127.0.0.1:8790"))
        self.dashboard_port = tk.StringVar(value=str(cfg.get("dashboard_port", 8792)))
        self.pairing = tk.StringVar()
        self.node_secret = tk.StringVar()
        self.bridge_secret_field = tk.StringVar()
        self.status = tk.StringVar(value="未连接")
        self.bridge_status = tk.StringVar(value="检查中")
        self.identity = tk.StringVar(value="检查中")
        self.models = tk.StringVar(value="模型：检查中")
        self.revision = tk.StringVar(value="桥版本：检查中")
        try:
            self.validation_results = json.loads(VALIDATIONS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.validation_results = {}
        self.probes = tk.StringVar(value=self._validation_summary())
        self._build()
        self.root.after(100, self._drain)
        self.root.after(1000, self.refresh)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        if cfg.get("autoconnect", True):
            self.root.after(1500, self.start)

    def load_config(self):
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def save_config(self):
        bm.APP_DATA.mkdir(parents=True, exist_ok=True)
        data = {"gateway": self.gateway.get().strip(), "bridge": self.bridge.get().strip(),
                "dashboard_port": int(self.dashboard_port.get()), "autoconnect": True}
        CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build(self):
        self.root.title("WorkBuddy 团队网关节点")
        self.root.geometry("790x680")
        self.root.minsize(690, 580)
        f = ttk.Frame(self.root, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="WorkBuddy 团队网关节点", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        for value in (self.identity, self.bridge_status, self.status, self.models, self.revision, self.probes):
            ttk.Label(f, textvariable=value, wraplength=730).pack(anchor="w", pady=2)
        grid = ttk.Frame(f)
        grid.pack(fill="x", pady=12)
        for row, (label, variable, secret) in enumerate((
            ("网关地址", self.gateway, False), ("本地桥地址", self.bridge, False),
            ("控制台端口", self.dashboard_port, False), ("一次性配对码", self.pairing, True),
            ("节点令牌（已有可留空）", self.node_secret, True),
            ("桥令牌（自动读取）", self.bridge_secret_field, True))):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(grid, textvariable=variable, show="*" if secret else "", width=66).grid(row=row, column=1, sticky="ew", padx=8)
        grid.columnconfigure(1, weight=1)
        row = ttk.Frame(f)
        row.pack(fill="x")
        for label, command in (("启动桥与节点", self.start), ("停止节点", self.disconnect),
                               ("打开桥控制台", self.open_dashboard), ("检查上游升级", self.check_update),
                               ("回退桥版本", self.rollback), ("退出", self.quit)):
            ttk.Button(row, text=label, command=command).pack(side="left", padx=3)
        self.logbox = tk.Text(f, height=13, state="disabled", wrap="word")
        self.logbox.pack(fill="both", expand=True, pady=(14, 0))
        self.log("就绪。令牌继续使用 Windows 凭据管理器中的现有值。")

    def log(self, message):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", redact(message) + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def emit(self, kind, detail=""):
        self.events.put((kind, detail))

    def _drain(self):
        while not self.events.empty():
            kind, detail = self.events.get_nowait()
            if kind == "connected":
                self.status.set("网关：已连接")
            elif kind == "disconnected":
                self.status.set("网关：断线重连中")
            elif kind in ("models", "local_models"):
                self.models.set("模型：" + (", ".join(detail) if detail else "空"))
            elif kind == "probe":
                self.last_probe = redact(detail)
                self.log(detail)
            elif kind == "validation":
                model = detail["model"]
                self.validation_results[model] = {"passed": detail["passed"], "results": detail["results"],
                                                  "bridge_revision": (bm.active()[1] or "unknown")}
                VALIDATIONS.write_text(json.dumps(self.validation_results, ensure_ascii=False, indent=2), encoding="utf-8")
                self.probes.set(self._validation_summary())
            elif kind == "paired":
                self.node_secret.set(detail)
                self.pairing.set("")
                self.log("配对成功")
                self._connect()
            else:
                self.log(detail)
        if not self.exiting:
            self.root.after(100, self._drain)

    def _secret(self, supplied=""):
        value = supplied or keyring.get_password(SERVICE, "bridge-token")
        path = Path.home() / ".workbuddy-bridge" / "bridge-token"
        return value or (path.read_text(encoding="utf-8").strip() if path.is_file() else "")

    def _validation_summary(self):
        if not self.validation_results:
            return "验收：本机无记录（历史结果请查看管理台）"
        items = []
        for model, data in sorted(self.validation_results.items()):
            total = len(data.get("results", {}))
            passed = sum(value is True for value in data.get("results", {}).values())
            items.append(f"{model} {passed}/{total}" + (" 通过" if data.get("passed") else " 未通过"))
        return "验收：" + "；".join(items)

    def refresh(self):
        if self.exiting:
            return
        auth = Path(os.environ.get("LOCALAPPDATA", "")) / "CodeBuddyExtension/Data/Public/auth/workbuddy-desktop-ai.info"
        self.identity.set("WorkBuddy：" + ("已发现当前用户登录文件" if auth.is_file() else "未发现登录文件；请在 WorkBuddy 中登录"))
        path, revision = bm.active()
        self.revision.set("桥版本：" + (revision[:12] if revision else "未安装"))
        try:
            base = bm.bridge_url(self.bridge.get().strip())
            with httpx.Client(timeout=3, trust_env=False) as client:
                r = client.get(base + "/v1/models", headers={"Authorization": "Bearer " + self._secret()})
                r.raise_for_status()
                names = [x["id"] for x in r.json().get("data", []) if isinstance(x.get("id"), str)]
            self.bridge_status.set("本地桥：正常")
            self.models.set("模型：" + (", ".join(names) if names else "空"))
        except Exception as exc:
            self.bridge_status.set("本地桥：不可用（" + type(exc).__name__ + "）")
        self.root.after(30000, self.refresh)

    def start(self):
        try:
            self.save_config()
            process, revision = bm.start_bridge(self.bridge.get().strip())
            self.bridge_process = process or self.bridge_process
            self.revision.set("桥版本：" + revision[:12])
            self.root.after(1500, self._connect)
        except Exception as exc:
            self.log("启动失败：" + str(exc))

    def _connect(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            gateway = self.gateway.get().strip()
            bridge = bm.bridge_url(self.bridge.get().strip())
            if not gateway.startswith("wss://"):
                raise ValueError("网关地址必须使用 wss://")
            code = self.pairing.get().strip()
            if code:
                self.status.set("网关：配对中")
                def pair():
                    try:
                        endpoint = gateway.replace("wss://", "https://", 1).removesuffix("/node/ws") + "/node/enroll"
                        label = "Windows-" + socket.gethostname() + "-" + os.environ.get("USERNAME", "user")
                        with httpx.Client(timeout=10) as client:
                            response = client.post(endpoint, json={"code": code, "label": label})
                            response.raise_for_status()
                        self.emit("paired", response.json()["token"])
                    except Exception as exc:
                        self.emit("error", "配对失败：" + type(exc).__name__)
                threading.Thread(target=pair, daemon=True).start()
                return
            token = self.node_secret.get().strip() or keyring.get_password(SERVICE, "node-token")
            bridge_token = self._secret(self.bridge_secret_field.get().strip())
            if not token or not bridge_token:
                raise ValueError("缺少节点令牌或桥令牌")
            keyring.set_password(SERVICE, "node-token", token)
            keyring.set_password(SERVICE, "bridge-token", bridge_token)
            self.node_secret.set("")
            self.bridge_secret_field.set("")
            _, revision = bm.active()
            args = SimpleNamespace(gateway=gateway, bridge=bridge, bridge_revision=revision or "unknown")
            def run():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.task = self.loop.create_task(node.main(args, self.emit))
                try:
                    self.loop.run_until_complete(self.task)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.emit("error", type(exc).__name__)
                finally:
                    self.loop.close()
                    self.loop = self.task = None
            self.worker = threading.Thread(target=run, daemon=True)
            self.worker.start()
            self.status.set("网关：连接中")
        except Exception as exc:
            self.log("连接失败：" + str(exc))

    def disconnect(self):
        if self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        self.status.set("网关：已断开")

    def open_dashboard(self):
        webbrowser.open(f"http://127.0.0.1:{int(self.dashboard_port.get())}/")

    def check_update(self):
        def work():
            try:
                revision, title = bm.latest()
                _, current = bm.active()
                if revision == current:
                    self.emit("log", "上游已是最新提交")
                    return
                self.emit("log", "发现上游版本 " + revision[:12] + "：" + title)
                self.root.after(0, lambda: self._offer_update(revision, title))
            except Exception as exc:
                self.emit("error", "检查升级失败：" + type(exc).__name__)
        threading.Thread(target=work, daemon=True).start()

    def _offer_update(self, revision, title):
        if messagebox.askyesno("上游桥升级", f"下载并验证 {revision[:12]}？\n{title}\n验收通过后将重新连接网关。"):
            self.disconnect()
            base, port = self.bridge.get().strip(), int(self.dashboard_port.get())
            threading.Thread(target=lambda: self._upgrade(revision, base, port), daemon=True).start()

    def _upgrade(self, revision, base, port):
        try:
            bm.download(revision)
            bm.activate(revision, base, self._secret(), port)
            self.emit("log", "已升级并验证桥版本 " + revision[:12])
            self.root.after(0, self._connect)
        except Exception as exc:
            self.emit("error", "升级未生效：" + type(exc).__name__ + "：" + str(exc))
            self.root.after(0, self._connect)

    def rollback(self):
        self.disconnect()
        base, port = self.bridge.get().strip(), int(self.dashboard_port.get())
        def work():
            try:
                revision = bm.rollback(base, self._secret(), port)
                self.emit("log", "已回退并验证桥版本 " + revision[:12])
            except Exception as exc:
                self.emit("error", "回退失败：" + str(exc))
            self.root.after(0, self._connect)
        threading.Thread(target=work, daemon=True).start()

    def hide(self):
        self.root.withdraw()
        if self.tray:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
            icon = Image.new("RGB", (64, 64), "#1559b7")
            ImageDraw.Draw(icon).text((15, 18), "W", fill="white")
            self.tray = pystray.Icon("WorkBuddyNode", icon, "WorkBuddy 网关节点",
                                     menu=pystray.Menu(pystray.MenuItem("打开", lambda: self.root.after(0, self.show)),
                                                       pystray.MenuItem("退出", lambda: self.root.after(0, self.quit))))
            threading.Thread(target=self.tray.run, daemon=True).start()
        except ImportError:
            self.root.deiconify()
            messagebox.showwarning("托盘不可用", "缺少 pystray/Pillow；请使用退出按钮。")

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        self.exiting = True
        self.disconnect()
        if self.tray:
            self.tray.stop()
        self.root.destroy()


if __name__ == "__main__":
    mutex = single_instance()
    migrate_startup()
    root = tk.Tk()
    ttk.Style().theme_use("clam")
    Application(root)
    root.mainloop()
