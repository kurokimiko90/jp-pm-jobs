"""面接 QA — 正典題庫・JD 特化生成・機械閘門・最終描画。

prep.py の interview qa stage はこの `build()` だけを呼ぶ。
移行スクリプトが bank だけを使う場合に build 側の依存を巻き込まないよう遅延解決する。
"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "build":
        from .build import build
        return build
    raise AttributeError(name)
