"""引用が素材に実在するかの判定 — `frameworks_build` と `gates` の共用。

LLM は「原文をそのままコピーせよ」と言っても句読点や助詞を変える。完全一致を
要求すると全部落ちるので、窓幅 `QUOTE_WINDOW` の部分一致率で測る。
"""

from __future__ import annotations

import re

QUOTE_WINDOW = 12       # 錨定判定に使う部分一致の窓幅（文字）


def norm(text: str) -> str:
    """照合用の正規化 — 空白・記号・改行を落とす。"""
    return re.sub(r"[\s　、。「」『』（）()\[\]・,.:：;；\-—–ー〜~]", "", text or "")


def anchored(quote: str, corpus_norm: str) -> float:
    """quote が素材に実在するか 0.0〜1.0 で返す。corpus 側は `norm()` 済みを渡す。"""
    q = norm(quote)
    if len(q) < QUOTE_WINDOW:
        return 0.0
    windows = [q[i:i + QUOTE_WINDOW] for i in range(0, len(q) - QUOTE_WINDOW + 1)]
    hits = sum(1 for w in windows if w in corpus_norm)
    return hits / len(windows)
