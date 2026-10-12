"""CDP (Chrome DevTools Protocol) 連線共用工具。

從 scrape.py 的 `_ensure_cdp` 抽出，供非爬蟲模組（例如 linkedin_inbox/）複用同一套
「port 已在監聽則重用、否則啟動一個帶獨立 profile 的 Chrome」邏輯，避免重複實作。
scrape.py 本身暫不遷移使用本模組（風險考量，行為不變）。
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time

from tools.app_config import load as _load_app_config

_SCRAPING_CFG = _load_app_config("scraping")
CHROME_BIN = _SCRAPING_CFG.get(
    "chrome_bin", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def ensure_cdp(port: int, user_data_dir: str, start_url: str) -> subprocess.Popen | None:
    """重用已在監聽的 Chrome，否則啟動一個。回傳 Popen（自己啟動時，呼叫端可選擇 terminate）。"""
    if port_open(port):
        print(f"  既存 Chrome 再利用 (port={port})")
        return None
    chrome = shutil.which("google-chrome") or CHROME_BIN
    print(f"  Chrome 起動中 (port={port}, profile={user_data_dir})...")
    proc = subprocess.Popen([
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=IsolateOrigins,site-per-process",
        start_url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        if port_open(port):
            return proc
    proc.terminate()
    raise RuntimeError(f"Chrome CDP port={port} が起動しませんでした")
