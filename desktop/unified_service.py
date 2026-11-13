"""Loopback data plane for WorkBuddy and Raccoon."""
from pathlib import Path

import uvicorn

from bridge_manager import bundle_dir
from raccoon_runner import RaccoonRunner
from unified_proxy import app


def main():
    source = bundle_dir() / "vendor" / "raccoon2api"
    if not (source / "main.py").is_file():
        raise FileNotFoundError(f"Missing Raccoon source: {source}")
    raccoon = RaccoonRunner(source, 8791)
    raccoon.start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8788, log_level="warning", access_log=False,
                    log_config=None)
    finally:
        raccoon.stop()


if __name__ == "__main__":
    main()
