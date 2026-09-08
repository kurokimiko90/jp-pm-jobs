"""config/*.yaml 載入器 — 使用者設定覆蓋程式碼預設值。

缺檔 = 全用程式碼預設（行為不變）；有檔 = 以 key 為單位整個覆蓋。
APP_CONFIG_DIR 環境變數可改設定目錄（測試用）。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_DIR = Path(os.environ.get("APP_CONFIG_DIR", str(ROOT / "config")))


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """讀 config/{name}.yaml。缺檔/空檔回 {}。"""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 頂層必須是 mapping（YAML dict）")
    return data


def get(name: str, key: str, default):
    """單 key 取值：config 有此 key 則整個覆蓋，否則用 default。"""
    return load(name).get(key, default)
