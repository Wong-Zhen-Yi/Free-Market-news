#!/usr/bin/env python3
"""One-click launcher for the local Free Market News web app."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def is_server_running() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    app_dir = root_dir / "app"
    data_dir = root_dir / "data"
    data_dir.mkdir(exist_ok=True)

    if not is_server_running():
        log_path = data_dir / "server.log"
        log_file = log_path.open("a", encoding="utf-8")
        command = [
            sys.executable,
            str(app_dir / "webapp.py"),
            "--portfolio",
            str(app_dir / "portfolio.json"),
            "--db",
            str(data_dir / "news.db"),
            "--port",
            str(PORT),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            command,
            cwd=str(app_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

        for _ in range(30):
            if is_server_running():
                break
            time.sleep(0.25)

    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
