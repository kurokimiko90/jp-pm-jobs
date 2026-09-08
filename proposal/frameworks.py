"""方法論庫 — 本人が実際に使っている思考の型。

`data/frameworks.yaml` は **User Layer**（`DATA_CONTRACT.md` 準拠）:
手で直してよい・自動では上書きしない。生成は `proposal.frameworks_build` を
明示的に叩いたときだけ（3 モデル草案 → 機械採点 → 合併裁決）。

このファイルは読み書きとレンダリングだけを持つ。LLM は呼ばない。
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PATH = ROOT / "data" / "frameworks.yaml"

# 提案 1 本に載せる方法論の上限（多いと「フレームワーク自慢」になる）
RENDER_LIMIT = 6


def path() -> Path:
    return PATH


def exists() -> bool:
    return PATH.exists()


def load() -> list[dict]:
    """方法論のリスト。未構築なら空リスト（呼び出し側が案内を出す）。"""
    if not PATH.exists():
        return []
    try:
        data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    items = data.get("frameworks") or []
    return [i for i in items if isinstance(i, dict) and i.get("name")]


def meta() -> dict:
    if not PATH.exists():
        return {}
    try:
        data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return {k: v for k, v in data.items() if k != "frameworks"}


def save(items: list[dict], extra: dict | None = None) -> Path:
    body = dict(extra or {})
    body["frameworks"] = items
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(
        yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8")
    return PATH


def select(tags: list[str] | None = None, limit: int = RENDER_LIMIT) -> list[dict]:
    """JD タグに合う方法論を選ぶ。タグ無指定なら votes / anchor_score 順。

    純規則・零 LLM。マッチしないときは上位を返す（無理に絞らない）。
    """
    items = load()
    if not items:
        return []
    if tags:
        low = [t.lower() for t in tags]

        def hit(item: dict) -> int:
            blob = " ".join(str(item.get(k, "")) for k in ("name", "when", "one_liner")).lower()
            return sum(1 for t in low if t and t in blob)

        items = sorted(items, key=lambda i: (-hit(i), -i.get("votes", 0),
                                             -i.get("anchor_score", 0)))
    else:
        items = sorted(items, key=lambda i: (-i.get("votes", 0),
                                             -i.get("anchor_score", 0)))
    return items[:limit]


def render(items: list[dict] | None = None) -> str:
    """prompt へ載せる形。実例（本人が本当にやったこと）を必ず併記する。"""
    items = items if items is not None else select()
    if not items:
        return ("（方法論庫が未構築。一般的なフレームワーク名を持ち出さず、"
                "プロフィールに書かれた本人の実際のやり方だけを根拠にすること）")
    lines = []
    for i, f in enumerate(items, 1):
        ev = f.get("evidence") or {}
        steps = f.get("steps") or []
        lines.append(f"## {i}. {f.get('name')}")
        if f.get("when"):
            lines.append(f"- 使いどころ: {f['when']}")
        if steps:
            lines.append("- 手順: " + " → ".join(str(s) for s in steps))
        if ev:
            lines.append(
                f"- 本人の実例: {ev.get('project', '?')} — {ev.get('what_happened', '')}")
        if f.get("one_liner"):
            lines.append(f"- 面接での言い方: 「{f['one_liner']}」")
        lines.append("")
    return "\n".join(lines)


def names() -> set[str]:
    return {str(f.get("name")) for f in load() if f.get("name")}
