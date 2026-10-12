"""正典題庫（question-bank/core/）の読み書きと、移行元 4 ファイルの解析。

正典の 1 問は「### C01. 質問文」＋メタ行 1 本＋結論 1 文＋番号付き要点。
downstream（pack 描画・道場・シアター）はすべてここを唯一の出所にする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .taxonomy import CATEGORIES, CATEGORY_ORDER, CORE, CoreQuestion

ROOT = Path(__file__).resolve().parents[2]
QBANK = ROOT / "interview" / "question-bank"
CORE_DIR = QBANK / "core"

# 移行元 — 参照子の接頭辞 → ファイル名
SOURCE_FILES: dict[str, str] = {
    "common": "common_qa.md",
    "ext": "common_qa_extended.md",
    "ans": "asked-frequently-answers.md",
    "pm": "asked-frequently-answers-pm.md",
}

_SRC_HEADING_RE = re.compile(r"^##\s+([\d\-]+)\.\s*(.+?)\s*$", re.MULTILINE)
_CORE_Q_RE = re.compile(r"^###\s+(C\d+)\.\s*(.+?)\s*$", re.MULTILINE)
_META_RE = re.compile(r"^<!--\s*(.*?)\s*-->\s*$", re.MULTILINE)
POINT_RE = re.compile(r"^(\d+)[\.、]\s*(.+)$")


@dataclass(frozen=True)
class CoreAnswer:
    qid: str
    category: str
    question: str
    conclusion: str
    points: list[str]
    evidence: list[str]
    jd_dependent: bool

    @property
    def body(self) -> str:
        lines = [self.conclusion]
        lines += [f"{i}. {p}" for i, p in enumerate(self.points, start=1)]
        return "\n".join(lines).strip()


# ------------------------------------------------------------ 移行元の解析

def load_sources() -> dict[str, str]:
    """`common#3` → その節の本文（見出し行を除く）の辞書。"""

    blocks: dict[str, str] = {}
    for prefix, filename in SOURCE_FILES.items():
        path = QBANK / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        marks = list(_SRC_HEADING_RE.finditer(text))
        for idx, mark in enumerate(marks):
            end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
            body = text[mark.end():end].strip()
            if body:
                blocks[f"{prefix}#{mark.group(1)}"] = body
    return blocks


def material_for(question: CoreQuestion, blocks: dict[str, str]) -> str:
    """1 問ぶんの移行元素材を連結する。欠損参照は静かに飛ばさず印を残す。"""

    parts: list[str] = []
    for ref in question.sources:
        body = blocks.get(ref)
        if body is None:
            parts.append(f"## [{ref}] — 素材なし")
            continue
        parts.append(f"## [{ref}]\n{body}")
    return "\n\n".join(parts)


# ---------------------------------------------------------- 正典の読み書き

def core_path(category: str) -> Path:
    index = CATEGORY_ORDER.index(category) + 1
    return CORE_DIR / f"{index:02d}-{category}.md"


def render_core_file(category: str, answers: list[CoreAnswer]) -> str:
    parts = [f"# {CATEGORIES[category]}", ""]
    for answer in answers:
        meta = [
            f"jd_dependent: {'true' if answer.jd_dependent else 'false'}",
            f"evidence: {', '.join(answer.evidence) or '-'}",
        ]
        parts += [
            f"### {answer.qid}. {answer.question}",
            f"<!-- {' | '.join(meta)} -->",
            "",
            answer.conclusion,
        ]
        parts += [f"{i}. {p}" for i, p in enumerate(answer.points, start=1)]
        parts += [""]
    return "\n".join(parts).rstrip() + "\n"


def parse_core_file(text: str, category: str) -> list[CoreAnswer]:
    marks = list(_CORE_Q_RE.finditer(text))
    answers: list[CoreAnswer] = []
    jd_flags = {item.qid: item.jd_dependent for item in CORE}
    for idx, mark in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        block = text[mark.end():end]
        evidence: list[str] = []
        for meta in _META_RE.findall(block):
            for chunk in meta.split("|"):
                key, _, value = chunk.partition(":")
                if key.strip() == "evidence" and value.strip() != "-":
                    evidence = [v.strip() for v in value.split(",") if v.strip()]
        conclusion_lines: list[str] = []
        points: list[str] = []
        for raw in _META_RE.sub("", block).splitlines():
            line = raw.strip()
            if not line or line == "---":
                continue
            point = POINT_RE.match(line)
            if point:
                points.append(point.group(2).strip())
            elif not points:
                conclusion_lines.append(line)
        answers.append(CoreAnswer(
            qid=mark.group(1),
            category=category,
            question=mark.group(2).strip(),
            conclusion="".join(conclusion_lines).strip(),
            points=points,
            evidence=evidence,
            jd_dependent=jd_flags.get(mark.group(1), False),
        ))
    return answers


def load_core() -> list[CoreAnswer]:
    """正典を qid 順（taxonomy の定義順）で返す。未生成なら空リスト。"""

    found: dict[str, CoreAnswer] = {}
    for category in CATEGORY_ORDER:
        path = core_path(category)
        if path.exists():
            for answer in parse_core_file(path.read_text(encoding="utf-8"), category):
                found[answer.qid] = answer
    return [found[item.qid] for item in CORE if item.qid in found]
