"""Move the existing per-user node startup to Agent Hub without touching secrets."""
import ctypes
import os
import sys
from pathlib import Path

import psutil


def single_instance():
    if os.name != "nt":
        return None
    handle = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                  "Local\\AgentHub-" + os.environ.get("USERNAME", "user"))
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise SystemExit("Agent Hub 已在运行")
    return handle


def migrate():
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return
    current = Path(sys.executable).resolve()
    for process in psutil.process_iter(["exe", "username"]):
        try:
            path = Path(process.info["exe"] or "").resolve()
            if path != current and path.name.lower() == "workbuddygatewaynode.exe" and \
                    process.info["username"] == psutil.Process().username():
                process.terminate()
                process.wait(timeout=10)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired, OSError):
            pass
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                          r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        winreg.SetValueEx(key, "AgentHub", 0, winreg.REG_SZ, '"' + sys.executable + '"')
        try:
            winreg.DeleteValue(key, "AgentGatewayNode")
        except FileNotFoundError:
            pass
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    (startup / "AgentGatewayNode.cmd").unlink(missing_ok=True)
