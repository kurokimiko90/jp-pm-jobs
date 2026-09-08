"""面接 QA の標準形式解析・描画・決定論的品質検査。

LLM を呼ばない層だけをここに集約する。qa_upgrade.py のほか、将来の
dojo / theater 統合でも同じ parser と lint を再利用できるようにする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .qa import keywords


_Q_RE = re.compile(r"^###\s+Q(?:[\d\-]+)?[\.、]?\s*(.+?)\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$", re.MULTILINE)
_POINT_RE = re.compile(r"^(\d+)[\.、]\s*(.+)$")
_OLD_DRILL_RE = re.compile(r"^#{3,4}\s*(?:深掘り|D\d+)")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[,.]\d+)*(?:\.\d+)?%?")
_DRILL_Q_RE = re.compile(r"^##\s+Q(\d+)\.\s*.+?\s*$", re.MULTILINE)
_DRILL_D_RE = re.compile(r"^###\s+D(\d+)\.\s*(.+?)（(HR|PdM|技術)）\s*$", re.MULTILINE)
_DRILL_ANSWER_RE = re.compile(r"^>\s*(.+?)\s*$", re.MULTILINE)
_WRITTEN_STYLE_TERMS = (
    "当該",
    "上述",
    "下記",
    "において",
    "寄与",
    "勘案",
    "帰属",
    "再構成です",
    "現時点の資料では",
    "確認済みの担当",
    "職務資料では",
    "推進してまいりました",
    "ご説明申し上げ",
    "面接では",
    "資料上では",
    "今ある資料",
    "と答えます",
    "と話します",
    "と表現します",
    "実績として申しません",
    "責任境界",
    "運用閾値",
    "判断境界",
    "直接的な接続",
    "変更条件",
    "精緻化条件",
    "責任分界",
    "解釈が戻らない",
    "近接経験",
    "再確認条件",
    "設定化",
    "現在確認中",
)


@dataclass(frozen=True)
class QAItem:
    """標準一問一答の一項目。old_drills は再生成前の参考用で、基底回答には混ぜない。"""

    qid: int
    section: str
    question: str
    conclusion: str
    points: list[str] = field(default_factory=list)
    old_drills: str = ""
    keyword: str = ""  # 見出し頭の話題ラベル。回答本文・検査対象には含めない

    @property
    def answer_text(self) -> str:
        return "\n".join([self.conclusion, *self.points]).strip()


def parse_qa(text: str) -> list[QAItem]:
    """01/03_interview_qa.md の標準形式を解析する。

    既存の ``#### 深掘り`` 以下は基底回答から切り離す。これにより、浅い旧追問が
    回答本文へ混入することなく、新しい回答を根にして追問を作り直せる。
    """

    sections = list(_SECTION_RE.finditer(text))

    def section_at(pos: int) -> str:
        current = ""
        for mark in sections:
            if mark.start() >= pos:
                break
            current = mark.group(1).strip()
        return current

    matches = list(_Q_RE.finditer(text))
    items: list[QAItem] = []
    for idx, mark in enumerate(matches, start=1):
        end = matches[idx].start() if idx < len(matches) else len(text)
        raw_body = text[mark.end():end].strip()
        base_lines: list[str] = []
        drill_lines: list[str] = []
        in_drill = False
        for raw in raw_body.splitlines():
            line = raw.strip()
            if _OLD_DRILL_RE.match(line):
                in_drill = True
            (drill_lines if in_drill else base_lines).append(raw)

        conclusion_lines: list[str] = []
        points: list[str] = []
        points_started = False
        for raw in base_lines:
            line = raw.strip()
            if not line or line == "---":
                continue
            if line.startswith("## "):
                break
            point = _POINT_RE.match(line)
            if point:
                points_started = True
                points.append(point.group(2).strip())
            elif not points_started and not line.startswith("#"):
                conclusion_lines.append(line)

        keyword, question = keywords.split(mark.group(1).strip())
        conclusion = "".join(conclusion_lines).strip()
        if question and (conclusion or points):
            items.append(QAItem(
                qid=idx,
                section=section_at(mark.start()),
                question=question,
                conclusion=conclusion,
                points=points,
                old_drills="\n".join(drill_lines).strip(),
                keyword=keyword,
            ))
    return items


def render_qa(company: str, title: str, generated_on: str, items: list[QAItem]) -> str:
    """downstream parser と互換の ``### Q.`` + 結論 + 番号付き要点に描画。"""

    parts = [
        f"# {company} {title} 想定問答 — 内容・口語アップグレード版",
        "",
        f"生成日：{generated_on}｜全{len(items)}問",
        "",
        "> 事実確認・回答改善・日本語口語化を実施。旧版は自動上書きしていません。",
        "",
        "---",
    ]
    previous_section = ""
    for item in items:
        if item.section and item.section != previous_section:
            parts += ["", f"## {item.section}", ""]
            previous_section = item.section
        heading = keywords.decorate(item.keyword, item.question)
        parts += ["", f"### Q. {heading}", "", item.conclusion, ""]
        parts += [f"{idx}. {point}" for idx, point in enumerate(item.points, start=1)]
        parts += ["", "---"]
    return "\n".join(parts).rstrip() + "\n"


def _norm_number(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def unsupported_numbers(text: str, authoritative_sources: str) -> list[str]:
    """権威素材に同じ数値がない生成数値を返す。

    回答構造の 1/2/3 は呼び出し側が本文だけを渡すため対象外。1〜3 の「点・つ」は
    単なる列挙として許可するが、年・名・件・％等は必ず素材に存在する必要がある。
    """

    known = {_norm_number(m.group()) for m in _NUMBER_RE.finditer(authoritative_sources)}
    suspects: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group()
        normalized = _norm_number(token)
        suffix = text[match.end():match.end() + 3]
        if normalized in {"1", "2", "3"} and re.match(r"(?:つ|点|つ目|点目)", suffix):
            continue
        if normalized not in known:
            suspects.add(token)
    return sorted(suspects)


def oral_lints(items: list[QAItem]) -> list[str]:
    """面接口語として機械判定できる違反だけを列挙する。"""

    findings: list[str] = []
    for item in items:
        answer = item.answer_text
        if "貴社" in answer:
            findings.append(f"Q{item.qid}: 面接口語で『貴社』を使用（『御社』にする）")
        if re.search(r"(?:である|だ。)", answer):
            findings.append(f"Q{item.qid}: 常体・書面語が残っている")
        if answer.count("ですけれども") > 2:
            findings.append(f"Q{item.qid}: 『ですけれども』が多すぎる")
        if answer.count("んです") > 2:
            findings.append(f"Q{item.qid}: 『んです』が多すぎる")
        if answer.count("と考えています") > 2:
            findings.append(f"Q{item.qid}: 『と考えています』が反復している")
        written_terms = [term for term in _WRITTEN_STYLE_TERMS if term in answer]
        if written_terms:
            findings.append(
                f"Q{item.qid}: 面接では硬い書面語が残っている（{' / '.join(written_terms)}）"
            )
        for sentence in re.split(r"[。！？\n]", answer):
            if len(sentence.strip()) > 90:
                findings.append(f"Q{item.qid}: 90字超の一文あり")
                break
        if not item.conclusion:
            findings.append(f"Q{item.qid}: 結論文がない")
        if not 2 <= len(item.points) <= 3:
            findings.append(f"Q{item.qid}: 要点が{len(item.points)}個（2〜3個にする）")
    return findings


def drill_oral_lints(drills: dict[int, list[dict[str, str]]]) -> list[str]:
    """深掘り回答の口語違反を列挙する。"""

    findings: list[str] = []
    for qid, rows in drills.items():
        for index, drill in enumerate(rows, start=1):
            answer = drill.get("answer", "")
            label = f"Q{qid} D{index}"
            if "貴社" in answer:
                findings.append(f"{label}: 面接口語で『貴社』を使用（『御社』にする）")
            if re.search(r"(?:である|だ。)", answer):
                findings.append(f"{label}: 常体・書面語が残っている")
            written_terms = [term for term in _WRITTEN_STYLE_TERMS if term in answer]
            if written_terms:
                findings.append(
                    f"{label}: 面接では硬い書面語が残っている（{' / '.join(written_terms)}）"
                )
            for sentence in re.split(r"[。！？\n]", answer):
                if len(sentence.strip()) > 90:
                    findings.append(f"{label}: 90字超の一文あり")
                    break
    return findings


def placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{.*?\}\}", text)))


def parse_drills(text: str) -> dict[int, list[dict[str, str]]]:
    """qa_upgrade の 03_drilldown_qa.md（審稿・事実監査済み）を qid 別に解析。

    _drill_markdown() の出力形式専用。theater 側はこれを事実出典として直接
    描画し、LLM に作り直させない（監査済み内容の再編造を避ける）。
    """
    q_marks = list(_DRILL_Q_RE.finditer(text))
    result: dict[int, list[dict[str, str]]] = {}
    for idx, qm in enumerate(q_marks):
        qid = int(qm.group(1))
        end = q_marks[idx + 1].start() if idx + 1 < len(q_marks) else len(text)
        block = text[qm.end():end]
        d_marks = list(_DRILL_D_RE.finditer(block))
        drills: list[dict[str, str]] = []
        for didx, dm in enumerate(d_marks):
            dend = d_marks[didx + 1].start() if didx + 1 < len(d_marks) else len(block)
            body = block[dm.end():dend]
            answer_m = _DRILL_ANSWER_RE.search(body)
            answer = answer_m.group(1).strip() if answer_m else ""
            if answer:
                drills.append({"question": dm.group(2).strip(), "tag": dm.group(3), "answer": answer})
        if drills:
            result[qid] = drills
    return result
