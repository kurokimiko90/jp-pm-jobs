"""機密語の遮蔽 — 取引先・連携ブランド名を LLM prompt と生成物から消す。

PII（氏名・連絡先）は tools/deid.py と tools/pii_gate.py が扱う。本モジュールは
別種の機密：**勤務先の取引先・接続ブランド・協業キャリア名**（NDA 相当）。
候補者自身の所属企業名や自社プロダクト名は対象外（履歴書に書くべき情報）。

二段構え:
  1. redact()  — prompt に入る素材（profile / gap 素材 / 固定行）を送信前に一般化
  2. scan()    — 生成物に禁止語が残っていないか機械チェック（残れば呼び出し側が警告）

設定は config/redaction.yaml（缺檔 = 何もしない no-op。開源環境で行動変化なし）:

    drop_parenthetical: true      # 禁止語を含む括弧列挙は丸ごと削除
    terms:
      - match: ["ブランドA", "BrandA"]
        replace: "大手共通ポイント"
"""

from __future__ import annotations

import re
from functools import lru_cache

from tools import app_config

# 括弧内に禁止語がある場合、列挙ごと落とす（「（A・B・C 等）」→ 削除）
_PAREN_RE_TMPL = r"[（(][^）)]*(?:{terms})[^）)]*[）)]"


@lru_cache(maxsize=1)
def _rules() -> tuple[tuple[str, str], ...]:
    """(禁止語, 置換語) の組。長い語から先に置換して部分一致の取りこぼしを防ぐ。"""
    raw = app_config.get("redaction", "terms", []) or []
    pairs: list[tuple[str, str]] = []
    for group in raw:
        if not isinstance(group, dict):
            continue
        replacement = str(group.get("replace") or "").strip()
        for term in group.get("match") or []:
            term = str(term).strip()
            if term:
                pairs.append((term, replacement))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return tuple(pairs)


def _drop_parenthetical() -> bool:
    return bool(app_config.get("redaction", "drop_parenthetical", True))


def _pattern(term: str) -> str:
    """ASCII 語は語境界付き（"au" が "auto" に当たる事故を防ぐ）。"""
    if re.fullmatch(r"[A-Za-z0-9 .&-]+", term):
        return r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
    return re.escape(term)


def scan(text: str) -> list[str]:
    """テキストに残っている禁止語の一覧（重複なし・設定順）。"""
    if not text:
        return []
    hits = []
    for term, _ in _rules():
        if re.search(_pattern(term), text) and term not in hits:
            hits.append(term)
    return hits


def redact(text: str) -> tuple[str, list[str]]:
    """禁止語を一般語に置換。回傳 (置換後テキスト, 命中した禁止語)。"""
    if not text:
        return text, []
    rules = _rules()
    if not rules:
        return text, []

    hits = scan(text)
    if not hits:
        return text, []

    if _drop_parenthetical():
        terms_alt = "|".join(_pattern(t) for t, _ in rules)
        text = re.sub(_PAREN_RE_TMPL.format(terms=terms_alt), "", text)

    for term, replacement in rules:
        text = re.sub(_pattern(term), replacement, text)
    # 置換で生じた重複（「一般語・一般語」「一般語 と 一般語」）を畳む
    for _, replacement in rules:
        if replacement:
            esc = re.escape(replacement)
            sep = r"(?:\s*(?:[・、,／/]|と|や|および|及び)\s*)"
            text = re.sub(rf"(?:{esc})(?:{sep}{esc})+", replacement, text)
    return text, hits


if __name__ == "__main__":  # 診断: python3 -m tools.redact
    from tools.deid import build_deid_profile
    profile_text = build_deid_profile(compact=True)
    clean, hits = redact(profile_text)
    print(f"ルール数: {len(_rules())} / profile 命中: {len(hits)}")
    for h in hits:
        print(f"  - {h}")
    print(f"残存チェック: {scan(clean) or 'クリーン'}")
