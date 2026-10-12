"""最終描画 — 定番（正典）+ JD 特化 + 深掘り を 1 枚の純日本語問答へ組む。

出力は質問と回答だけ。見出し以外の説明文・注意書き・メタ情報は書かない
（当日はこのファイルだけを開けばよい状態にする）。

閘門用のラベル付け（`labelled()`）もここに置く。閘門が見るのは結論文も含めた
回答本文であって質問文ではない — 質問文は JD の語をそのまま含むことが多く、
質問文まで数えると「JD 要件を扱えているか」の判定が自己成就してしまう。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import keywords
from .bank import CoreAnswer
from .taxonomy import CATEGORIES, keyword_of

# 出力順 — 面接の流れ（経歴 → 志望動機/この求人 → 人物 → 実務 → 専門 → 逆質問）
SECTION_ORDER: tuple[str, ...] = (
    "career", "__jd__", "strength", "pm", "ai", "agent", "client",
    "__drilldown__", "closing",
)

JD_SECTION_TITLE = "この求人について"
DRILLDOWN_SECTION_TITLE = "深掘り"


@dataclass(frozen=True)
class QA:
    question: str
    conclusion: str
    points: list[str]
    section: str
    keyword: str = ""

    @classmethod
    def from_core(cls, answer: CoreAnswer) -> "QA":
        return cls(answer.question, answer.conclusion, answer.points, answer.category,
                   keyword_of(answer.qid))


@dataclass(frozen=True)
class Labelled:
    """閘門が 1 問を見るための単位。origin は再生成の可否判断に使う。

    `category` は `critic.py` が採点軸を選ぶための分類（`taxonomy.CATEGORIES` の
    キー、または jd/drilldown 生成物ではその origin 自身）。閘門（gates.py）は
    使わない — 事実・形式の検査に問の種類は関係しないため。
    """

    label: str
    question: str
    conclusion: str
    points: list[str]
    origin: str  # "core" | "jd" | "drilldown"
    category: str = ""  # taxonomy のカテゴリキー、または "jd" / "drilldown"

    @property
    def sentences(self) -> list[str]:
        """事実検査の対象 — 結論文も含める（結論に置いた数字が抜けるため）。"""

        return ([self.conclusion] if self.conclusion else []) + list(self.points)

    @property
    def body(self) -> str:
        return "\n".join(self.sentences)


def _block(item: QA) -> list[str]:
    heading = keywords.decorate(item.keyword, item.question)
    lines = [f"### Q. {heading}", "", item.conclusion]
    lines += [f"{i}. {point}" for i, point in enumerate(item.points, start=1)]
    lines.append("")
    return lines


def render(company: str, core: list[CoreAnswer], jd_items: list[QA],
           drilldown: list[QA]) -> str:
    grouped: dict[str, list[QA]] = {}
    for answer in core:
        grouped.setdefault(answer.category, []).append(QA.from_core(answer))

    parts = [f"# 想定問答 — {company}", ""]
    for key in SECTION_ORDER:
        if key == "__jd__":
            items, title = jd_items, JD_SECTION_TITLE
        elif key == "__drilldown__":
            items, title = drilldown, DRILLDOWN_SECTION_TITLE
        else:
            items, title = grouped.get(key, []), CATEGORIES[key]
        if not items:
            continue
        parts += [f"## {title}", ""]
        for item in items:
            parts += _block(item)
    return "\n".join(parts).rstrip() + "\n"


def label_of(item: CoreAnswer | QA, origin: str, index: int = 0) -> str:
    """人が読んで場所が分かるラベル。build 側の再生成もこの規則で引き当てる。"""

    if origin == "core":
        return f"{item.qid} {item.question[:24]}"  # type: ignore[union-attr]
    prefix = "JD" if origin == "jd" else "DD"
    return f"{prefix}{index:02d} {item.question[:24]}"


def labelled(core: list[CoreAnswer], jd_items: list[QA],
             drilldown: list[QA]) -> list[Labelled]:
    out: list[Labelled] = []
    for answer in core:
        out.append(Labelled(label_of(answer, "core"), answer.question,
                            answer.conclusion, answer.points, "core", answer.category))
    for index, item in enumerate(jd_items, start=1):
        out.append(Labelled(label_of(item, "jd", index), item.question,
                            item.conclusion, item.points, "jd", "jd"))
    for index, item in enumerate(drilldown, start=1):
        out.append(Labelled(label_of(item, "drilldown", index), item.question,
                            item.conclusion, item.points, "drilldown", "drilldown"))
    return out


def answers_text(items: list[Labelled]) -> str:
    """閘門用 — 回答本文だけを連結する（質問文は含めない）。"""

    return "\n".join(item.body for item in items)
