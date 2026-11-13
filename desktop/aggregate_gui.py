"""Multi-page desktop console for the local unified agent proxy."""
import json
import os
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import keyring
import psutil
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget,
                               QSystemTrayIcon,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
                               QWidget)

from node_controller import NodeController
import bridge_manager as bm
from migration import migrate, single_instance
from raccoon_adapter import INTERNAL_KEY, save_token
from unified_proxy import local_key
from unified_service import main as run_service


class Bus(QObject):
    snapshot = Signal(dict)
    event = Signal(str, object)


def card(title, detail=""):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    head = QLabel(title)
    head.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
    layout.addWidget(head)
    value = QLabel(detail)
    value.setWordWrap(True)
    value.setObjectName("muted")
    layout.addWidget(value)
    return frame, value


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agent Hub · 统一代理")
        self.resize(1180, 760)
        self.bus = Bus()
        self.bus.snapshot.connect(self.apply_snapshot)
        self.bus.event.connect(self.on_event)
        self.node = NodeController(lambda kind, detail="": self.bus.event.emit(kind, detail))
        self.gateway_status = "连接中"
        self.exiting = False
        self._polling = False
        self.pages = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setFixedWidth(205)
        self.nav.addItems(["总览", "WorkBuddy", "商汤小浣熊", "模型与接口", "本机 API", "团队网关", "请求与日志", "更新与设置"])
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        shell = QWidget()
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.nav)
        outer.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.build_pages()
        self.nav.setCurrentRow(0)
        self.setStyleSheet("""
          QMainWindow, QWidget { background:#101827; color:#e8eef7; font:10pt 'Segoe UI'; }
          QListWidget { background:#0b1220; border:0; padding:18px 8px; font-size:11pt; }
          QListWidget::item { padding:13px 16px; border-radius:8px; margin:2px 5px; }
          QListWidget::item:selected { background:#26426b; color:white; }
          QFrame#card { background:#1a2639; border:1px solid #30425e; border-radius:12px; padding:12px; }
          QLabel#muted { color:#a9b8ce; }
          QPushButton { background:#2563a7; border:0; border-radius:7px; padding:9px 16px; color:white; }
          QPushButton:hover { background:#3378c8; }
          QTableWidget, QTextEdit { background:#162235; border:1px solid #30425e; border-radius:8px; }
          QHeaderView::section { background:#22344f; color:#e8eef7; padding:8px; border:0; }
        """)
        threading.Thread(target=self._run_service, daemon=True, name="unified-proxy").start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(10000)
        QTimer.singleShot(1200, self.refresh)
        QTimer.singleShot(1800, lambda: threading.Thread(target=self.node.start, daemon=True).start())
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        menu = QMenu()
        open_action = QAction("打开 Agent Hub", self)
        open_action.triggered.connect(self.showNormal)
        menu.addAction(open_action)
        quit_action = QAction("退出并断开节点", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def page(self, title, note):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(title)
        heading.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        layout.addWidget(heading)
        subtitle = QLabel(note)
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.pages.addWidget(page)
        return layout

    def build_pages(self):
        overview = self.page("统一代理总览", "一个本机接口管理多个 Agent；每个来源保留独立模型身份。")
        row = QHBoxLayout()
        self.overview_values = []
        for title in ("本机 API", "WorkBuddy", "商汤小浣熊", "团队网关"):
            widget, value = card(title, "检查中")
            row.addWidget(widget)
            self.overview_values.append(value)
        overview.addLayout(row)
        self.overview_models = QTableWidget(0, 3)
        self.overview_models.setHorizontalHeaderLabels(["来源模型", "Agent", "状态"])
        self.overview_models.horizontalHeader().setStretchLastSection(True)
        overview.addWidget(self.overview_models, 1)

        workbuddy = self.page("WorkBuddy", "由 workbuddy-to-dsh 提供模型接口；登录、额度和桥诊断保持在当前 Windows 用户中。")
        wcard, self.workbuddy_info = card("桥与账户", "检查中")
        workbuddy.addWidget(wcard)
        button = QPushButton("打开 WorkBuddy 高级控制台")
        button.clicked.connect(lambda: webbrowser.open("http://127.0.0.1:8792"))
        workbuddy.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        controls = QHBoxLayout()
        for caption, callback in (("连接团队网关", self.node.start), ("断开团队网关", self.node.stop),
                                  ("检查桥升级", self.check_workbuddy_update), ("回退桥版本", self.rollback_workbuddy)):
            control = QPushButton(caption)
            control.clicked.connect(lambda checked=False, action=callback: threading.Thread(target=action, daemon=True).start())
            controls.addWidget(control)
        workbuddy.addLayout(controls)
        pair = QPushButton("输入一次性配对码")
        pair.clicked.connect(self.pair_workbuddy)
        workbuddy.addWidget(pair, alignment=Qt.AlignmentFlag.AlignLeft)
        workbuddy.addStretch()

        raccoon = self.page("商汤小浣熊", "使用官方桌面客户端登录；raccoon2api 源码在独立适配层中运行，仅监听本机回环地址。")
        rcard, self.raccoon_info = card("登录与适配器", "检查中")
        raccoon.addWidget(rcard)
        buttons = QHBoxLayout()
        for caption, callback in (("打开商汤小浣熊", self.open_raccoon),
                                  ("导入会话令牌", self.import_raccoon_token), ("刷新模型目录", self.refresh)):
            control = QPushButton(caption)
            control.clicked.connect(callback)
            buttons.addWidget(control)
        raccoon.addLayout(buttons)
        self.raccoon_models = QTableWidget(0, 2)
        self.raccoon_models.setHorizontalHeaderLabels(["Raccoon 模型", "本机来源 ID"])
        self.raccoon_models.horizontalHeader().setStretchLastSection(True)
        raccoon.addWidget(self.raccoon_models, 1)

        models = self.page("模型与接口", "本机来源 ID 采用“原模型@Agent”格式；团队网关另行维护对外模型映射。")
        self.model_text = QTextEdit()
        self.model_text.setReadOnly(True)
        models.addWidget(self.model_text, 1)

        api = self.page("本机 API", "统一入口只监听 127.0.0.1:8788；所有调用需要独立本机 Key。")
        keycard, self.api_info = card("接入参数", "Base URL  http://127.0.0.1:8788/v1")
        api.addWidget(keycard)
        show = QPushButton("复制本机 API Key")
        show.clicked.connect(self.copy_key)
        api.addWidget(show, alignment=Qt.AlignmentFlag.AlignLeft)
        api.addStretch()

        gateway = self.page("团队网关", "节点上报来源模型与能力；服务器映射公开模型并选择合格来源。")
        gcard, self.gateway_info = card("连接", "检查中")
        gateway.addWidget(gcard)
        gateway.addStretch()

        logs = self.page("请求与日志", "仅显示本机状态与错误，不展示登录令牌或提示词原文。")
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        logs.addWidget(self.logs, 1)

        updates = self.page("更新与设置", "上游源码独立存放，按提交升级；新版本通过模型验收后启用。")
        ucard, self.update_info = card("版本", "WorkBuddy 桥固定版本；Raccoon 适配源码已固定提交。")
        updates.addWidget(ucard)
        updates.addStretch()

    def copy_key(self):
        QApplication.clipboard().setText(local_key())
        QMessageBox.information(self, "已复制", "本机 API Key 已复制到剪贴板。请只在本机客户端中使用。")

    def _run_service(self):
        try:
            run_service()
        except Exception as exc:
            self.bus.event.emit("service_error", type(exc).__name__ + ": " + str(exc)[:180])

    def open_raccoon(self):
        try:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\com.sensetime.desktop.raccoon.v6"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            QMessageBox.warning(self, "未找到客户端", "请先安装官方商汤小浣熊桌面客户端。")

    def import_raccoon_token(self):
        value, ok = QInputDialog.getText(self, "小浣熊会话令牌", "粘贴本人账号的 xhx_token：",
                                         QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            save_token(value)
            self.refresh()
        except ValueError as exc:
            QMessageBox.warning(self, "令牌无效", str(exc))

    def pair_workbuddy(self):
        code, ok = QInputDialog.getText(self, "配对 WorkBuddy 节点", "一次性配对码：",
                                        QLineEdit.EchoMode.Password)
        if not ok or not code.strip():
            return
        def work():
            try:
                path = bm.APP_DATA / "config.json"
                cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
                gateway = cfg.get("gateway", "wss://api.435038537.xyz/node/ws")
                endpoint = gateway.replace("wss://", "https://", 1).removesuffix("/node/ws") + "/node/enroll"
                label = "Windows-" + socket.gethostname() + "-" + os.environ.get("USERNAME", "user")
                with httpx.Client(timeout=10, trust_env=False) as client:
                    response = client.post(endpoint, json={"code": code.strip(), "label": label})
                    response.raise_for_status()
                keyring.set_password("agent-gateway-workbuddy-node", "node-token", response.json()["token"])
                self.bus.event.emit("gateway", "配对成功，正在连接")
                self.node.start()
            except Exception as exc:
                self.bus.event.emit("gateway", "配对失败：" + type(exc).__name__)
        threading.Thread(target=work, daemon=True).start()

    def check_workbuddy_update(self):
        try:
            revision, title = bm.latest()
            _, current = bm.active()
            if revision == current:
                self.bus.event.emit("update_info", "WorkBuddy 桥已是最新提交：" + revision[:12])
            else:
                self.bus.event.emit("update_offer", (revision, title))
        except Exception as exc:
            self.bus.event.emit("update_info", "检查升级失败：" + type(exc).__name__)

    def _bridge_config(self):
        path = bm.APP_DATA / "config.json"
        cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        return bm.bridge_url(cfg.get("bridge", "http://127.0.0.1:8790")), int(cfg.get("dashboard_port", 8792))

    def _switch_workbuddy(self, revision=None):
        try:
            base, port = self._bridge_config()
            token = keyring.get_password("agent-gateway-workbuddy-node", "bridge-token")
            self.node.stop()
            if self.node.worker:
                self.node.worker.join(timeout=6)
            if revision:
                bm.download(revision)
                bm.activate(revision, base, token, port)
                note = "桥升级完成：" + revision[:12]
            else:
                current = bm.rollback(base, token, port)
                note = "桥已回退：" + current[:12]
            self.bus.event.emit("update_info", note)
        except Exception as exc:
            self.bus.event.emit("update_info", "桥版本切换失败：" + type(exc).__name__)
        finally:
            self.node.start()

    def rollback_workbuddy(self):
        self._switch_workbuddy()

    def on_event(self, kind, detail):
        if kind == "connected":
            self.gateway_status = "已连接"
        elif kind == "disconnected":
            self.gateway_status = "已断开"
        elif kind == "gateway":
            self.gateway_status = str(detail)
        elif kind == "probe":
            self.logs.append(str(detail)[:300])
        elif kind == "service_error":
            self.logs.append("本机 API 启动失败：" + str(detail))
        elif kind == "update_info":
            self.update_info.setText(str(detail))
            self.logs.append(str(detail))
        elif kind == "update_offer":
            revision, title = detail
            if QMessageBox.question(self, "WorkBuddy 桥升级", f"下载并验证 {revision[:12]}？\n{title}") == QMessageBox.StandardButton.Yes:
                threading.Thread(target=self._switch_workbuddy, args=(revision,), daemon=True).start()
        self.gateway_info.setText(self.gateway_status)

    def closeEvent(self, event):
        if self.exiting or not self.tray.isVisible():
            super().closeEvent(event)
        else:
            self.hide()
            event.ignore()

    def quit_app(self):
        self.exiting = True
        self.node.stop()
        self.tray.hide()
        QApplication.quit()

    def refresh(self):
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._collect, daemon=True).start()

    def _collect(self):
        result = {"models": [], "workbuddy": "不可用", "raccoon": "不可用", "api": "不可用"}
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                result["api"] = "运行中" if client.get("http://127.0.0.1:8788/health").status_code == 200 else "不可用"
                result["models"] = client.get("http://127.0.0.1:8788/v1/models", headers={"Authorization": "Bearer " + local_key()}).json().get("data", [])
                w = client.get("http://127.0.0.1:8792/api/identity")
                if w.status_code == 200:
                    result["workbuddy"] = "桥运行中 · 当前用户登录" if w.json().get("auth") else "桥运行中 · 请登录"
                r = client.get("http://127.0.0.1:8791/health", headers={"X-Agent-Hub-Internal": INTERNAL_KEY})
                if r.status_code == 200:
                    info = r.json()
                    result["raccoon"] = "代理已授权" if info.get("authenticated") else "代理待授权"
                    result["raccoon_expiry"] = info.get("jwt_expires_at")
        except Exception as exc:
            result["error"] = type(exc).__name__
        result["raccoon_client"] = any(p.info.get("name", "").lower() == "商汤小浣熊.exe"
                                       for p in psutil.process_iter(["name"]))
        self.bus.snapshot.emit(result)

    def apply_snapshot(self, data):
        self._polling = False
        self.overview_values[0].setText(data["api"])
        self.overview_values[1].setText(data["workbuddy"])
        self.overview_values[2].setText(data["raccoon"])
        self.overview_values[3].setText(self.gateway_status)
        self.workbuddy_info.setText(data["workbuddy"])
        self.raccoon_info.setText(("官方客户端运行中 · " if data.get("raccoon_client") else "官方客户端未运行 · ") +
                                  data["raccoon"] + (" · 到期 " + str(data["raccoon_expiry"]) if data.get("raccoon_expiry") else ""))
        models = data["models"]
        self.overview_models.setRowCount(len(models))
        raccoon = [m for m in models if m["id"].endswith("@raccoon")]
        self.raccoon_models.setRowCount(len(raccoon))
        for i, model in enumerate(models):
            for col, value in enumerate((model["id"], model.get("owned_by", ""), "可用")):
                self.overview_models.setItem(i, col, QTableWidgetItem(value))
        for i, model in enumerate(raccoon):
            self.raccoon_models.setItem(i, 0, QTableWidgetItem(model["id"].removesuffix("@raccoon")))
            self.raccoon_models.setItem(i, 1, QTableWidgetItem(model["id"]))
        self.model_text.setPlainText("\n".join(m["id"] for m in models) or "尚无可用模型。")
        if data.get("error"):
            self.logs.append("状态检查：" + data["error"])


def main():
    mutex = single_instance()
    migrate()
    app = QApplication([])
    window = Window()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
