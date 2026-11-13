"""Run the existing WorkBuddy bridge and gateway connector inside the Qt app."""
import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import keyring

import bridge_manager as bm
import node

CONFIG = bm.APP_DATA / "config.json"


class NodeController:
    def __init__(self, notify):
        self.notify = notify
        self.worker = None
        self.loop = None
        self.task = None
        self.bridge_process = None

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not keyring.get_password(node.SERVICE, "node-token"):
            self.notify("gateway", "尚未配对")
            return
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8-sig")) if CONFIG.is_file() else {}
            workbuddy_bridge = bm.bridge_url(config.get("bridge", "http://127.0.0.1:8790"))
            gateway = config.get("gateway", "wss://api.435038537.xyz/node/ws")
        except Exception as exc:
            self.notify("gateway", "配置错误：" + type(exc).__name__)
            return
        bridge_token = keyring.get_password(node.SERVICE, "bridge-token")
        if not bridge_token:
            file = Path.home() / ".workbuddy-bridge/bridge-token"
            if file.is_file():
                bridge_token = file.read_text(encoding="utf-8").strip()
                if bridge_token:
                    keyring.set_password(node.SERVICE, "bridge-token", bridge_token)
        if bridge_token:
            try:
                self.bridge_process, _ = bm.start_bridge(workbuddy_bridge, int(config.get("dashboard_port", 8792)))
            except Exception as exc:
                self.notify("gateway", "WorkBuddy 桥暂不可用：" + type(exc).__name__)
        else:
            self.notify("gateway", "WorkBuddy 桥令牌缺失；继续连接可用来源")
        args = SimpleNamespace(gateway=gateway, bridge="http://127.0.0.1:8788", protocol=3,
                               workbuddy_bridge=workbuddy_bridge, dashboard_port=int(config.get("dashboard_port", 8792)),
                               raccoon_revision=node.RACCOON_REVISION,
                               adapter_revision=node.AGENT_HUB_VERSION,
                               credential_epoch=config.get("credential_epoch", ""))

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.task = self.loop.create_task(node.main(args, self.notify))
            try:
                self.loop.run_until_complete(self.task)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.notify("gateway", "连接失败：" + type(exc).__name__)
            finally:
                self.loop.close()
                self.loop = self.task = None
        self.worker = threading.Thread(target=run, name="agent-hub-gateway", daemon=True)
        self.worker.start()
        self.notify("gateway", "连接中")

    def stop(self):
        if self.loop and self.task:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        self.notify("gateway", "已断开")
