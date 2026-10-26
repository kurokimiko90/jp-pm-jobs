"""本人の実践記録（プロフィール外の一次資料）を QA 生成の文脈へ足す。

`data/candidate_profile.yaml` は経歴の要約なので、案件の判断・代償・やり方までは
持っていない。Samurai の基礎知識稿のような「本人が書いて確認した振り返り」を
`config/knowledge.yaml` に列挙すると、QA の答えがそこからも具体を引ける。

缺檔 = 空文字（行為不變）。列挙したファイルが無ければ警告して飛ばす。
遮蔽（redact）は呼び出し側の `_context` がブロック全体へかける。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from tools import app_config

log = logging.getLogger(__name__)

# 1 ファイルの上限字数 — 基礎知識稿は 5,000 字前後。長文の素材を入れて
# prompt が膨らみ指揮中心が時間切れになるのを防ぐ（超過分は切って印を残す）
MAX_CHARS_PER_SOURCE = 8000


@dataclass(frozen=True)
class KnowledgeSource:
    label: str
    path: Path


def configured_sources() -> list[KnowledgeSource]:
    items = app_config.get("knowledge", "sources", []) or []
    sources: list[KnowledgeSource] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError(f"config/knowledge.yaml の sources は label/path を持つ mapping: {item!r}")
        path = Path(str(item["path"])).expanduser()
        sources.append(KnowledgeSource(label=str(item.get("label") or path.parent.name), path=path))
    return sources


def _read(source: KnowledgeSource, limit: int = MAX_CHARS_PER_SOURCE) -> str | None:
    if not source.path.is_file():
        log.warning("knowledge source が見つからない: %s (%s)", source.label, source.path)
        return None
    text = source.path.read_text(encoding="utf-8").strip()
    # 素材内の見出しを 2 段下げ、ラベル（##）の下へ収める
    text = re.sub(r"(?m)^(#+)", r"##\1", text)
    if len(text) > limit:
        text = text[:limit] + "\n（以下省略 — 上限字数で切った）"
    return text


def knowledge_block(sources: list[KnowledgeSource] | None = None,
                    max_chars_per_source: int = MAX_CHARS_PER_SOURCE) -> str:
    """prompt に差し込む Markdown 節。素材が 1 つも無ければ空文字。

    `max_chars_per_source` は呼び出し側の prompt 予算に合わせて絞る（面接 QA は
    応募者プロフィール・JD・会社事実と同居するので短くする — `interview.qa.build`）。
    """

    sources = configured_sources() if sources is None else sources
    parts = [
        f"## {source.label}\n{text}"
        for source in sources
        if (text := _read(source, max_chars_per_source))
    ]
    if not parts:
        return ""
    return (
        "# 応募者本人の実践記録（本人が書いた振り返り。事実の出所として使ってよい。"
        "【AIの推論】【候補】等の印が付いた記述は断定せず、数字は原文どおりに使うこと）\n\n"
        + "\n\n".join(parts)
        + "\n"
    )
