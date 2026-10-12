#!/usr/bin/env python3
"""launchd → bash script 墊片。

macOS TCC 不允許 launchd 背景的 /bin/bash 讀 ~/Documents（錯誤：Operation not
permitted），但 /opt/homebrew/bin/python3 已有 Documents 授權（dashboard 的
uvicorn 即以此運作）。launchd 以 python3 為進入點，bash 作為子行程繼承授權。

用法（plist ProgramArguments）:
    /opt/homebrew/bin/python3 <本檔絕對路徑> <script.sh> [args...]
"""
import subprocess
import sys

if len(sys.argv) < 2:
    print("用法: launchd_shim.py <script.sh> [args...]", file=sys.stderr)
    sys.exit(2)

sys.exit(subprocess.call(["/bin/bash", *sys.argv[1:]]))
