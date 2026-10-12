"""JD 本文が「分析に使える状態か」の判定（純規則・零依存）。

郵件版の摘要（60 字前後）やログイン壁の文言を JD として gap 分析に回すと、
半端な情報で推薦度を出してしまう。補抓（refetch_jd）と gap 選件の両方がここを使う。
"""
from __future__ import annotations

# 郵件摘要は 40〜70 字、CDP で取った本文は 800 字以上が実測の分布
MIN_JD_CHARS = 500

# 「ページは開けたが中身はログイン壁」の判別語。抜き出した本文にこれが混じって
# いたら JD ではない（そのまま保存すると gap 分析が半端な情報で走る）。
LOGIN_WALL_MARKERS = (
    "詳細をご覧になる場合は会員登録",
    "会員登録（無料）が必要です",
    "ログインしてください",
    "Sign in to view",
    "Join LinkedIn to view",
    "Authenticating...",
)


def is_login_wall(text: str) -> bool:
    return any(m in text for m in LOGIN_WALL_MARKERS)


def jd_ready(text: str | None) -> bool:
    """gap 分析に回せる JD 本文か（長さ十分・ログイン壁でない）。"""
    body = (text or "").strip()
    return len(body) >= MIN_JD_CHARS and not is_login_wall(body)
