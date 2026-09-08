"""批評層 — 機械閘門が「判定しない」と言い切った良し悪しを、面接官の目で見る。

`gates.py` は形と事実の整合だけを見る。素材の数字を正しく使い、口語で、要件も
覆蓋していても、「誰でも言える一般論」「質問に答えていない答え」「この質問が
本当は何を確かめようとしているか」は素通りする。落ちる面接はそこで落ちる。
ここはそれを拾って、書き直しの指示に変える。

**採点は問ごと・カテゴリごとに軸を変える。** 「転職理由」と「AI の PoC 展開」は
面接官が確かめたいことが違う（定着可能性 vs 専門性）。全問を同じ 5 軸に通すと、
その問が本当は何を試されているのかを無視した採点になる（実際に指摘された誤り —
`AXES` 参照）。`具体性`/`質問応答` は全問共通の答案品質、それ以外は
`CATEGORY_AXES` でカテゴリごとに 1〜2 軸だけ足す。

**LLM の批評をそのまま信じない。** 信じられるようにするための縛りが 2 つ:

1. **引用必須** — 指摘には回答本文からの逐語引用を付けさせ、その引用が実際に
   本文に在るかを機械照合する（`verify`）。在らない指摘は捨てる。LLM の判定を
   LLM で検証しない — 引用の在否は文字列比較で決まる。ラベルを取り違えた指摘は、
   引用が在る問へ引き当て直す（捨てない）。
2. **素材を増やさない** — 批評が言えるのは「素材の別の場面へ差し替えよ」まで。
   「〜という実績を書け」は書かせない（零編造。`_PROMPT` で明示）。

採点は問ごとに、その問へ適用される軸だけを 0〜10 → 0〜100 へ換算する。
**合否は問ごと** — `TARGET_SCORE` 未満の問が 1 問でも残っていれば pack は
不合格（`Critique.passed`）。平均で誤魔化さない。出てこなかった問・軸は 0 点。
黙って満点扱いにすると、LLM が問を書き落としただけで合格線を越えてしまう。

書き直して**その巡で対象にした問の合計点が下がった**版は捨てて前の版へ戻す
（`round_delta` が負）。改善が `STALL_ROUNDS` 回続けて止まったら、`MAX_ROUNDS`
に達していなくても打ち切る — 90 点という合格線は全軸 9 点以上を要求する厳しい
基準で、上限まで回しても届かない問が残ることは普通にある。それを隠さず
監査に出す。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tools import miko_llm

from .render import Labelled

# 全問共通 — 答案としての最低品質。カテゴリに関わらず必ず見る。
UNIVERSAL_AXES: tuple[str, ...] = ("具体性", "質問応答")

# カテゴリごとに追加する軸。taxonomy.CATEGORIES のキー + jd/drilldown（origin 自身）。
# 「この問は面接官が本当は何を確かめたいのか」に対応させる。増やしすぎない —
# 1 問あたり最大 2 軸（career）、それ以外は 1 軸。
CATEGORY_AXES: dict[str, tuple[str, ...]] = {
    "career":     ("定着可能性", "意欲"),      # 転職理由・キャリアビジョン・志望動機系
    "strength":   ("戦力化スピード",),         # 強み・弱み・成果＝即戦力の根拠
    "pm":         ("専門性",),                 # 実務プロセスの判断ロジック
    "ai":         ("専門性",),
    "agent":      ("専門性",),
    "client":     ("専門性",),
    "closing":    ("意欲",),                   # 逆質問＝志望度の深化
    "jd":         ("専門性",),                 # JD 特化生成物（taxonomy のカテゴリを持たない）
    "drilldown":  ("専門性",),                 # 深掘り（同上）
}
DEFAULT_CATEGORY_AXES: tuple[str, ...] = ("専門性",)

AXIS_DESC: dict[str, str] = {
    "具体性":     "場面が浮かぶか。誰が・何を判断し・どうなったかが言えているか",
    "質問応答":   "聞かれたことに答えているか。言いたいことを言っているだけになっていないか",
    "定着可能性": "この会社で長く働く動機があるか。事業内容への興味、未経験領域への学習姿勢"
                  "（「◎◎は勉強中です」型の具体例）、なりたい自分と現状のギャップを埋める発言があるか",
    "戦力化スピード": "5年後に中核人材として活躍できる根拠があるか。今すぐ貢献できることと"
                      "入社後に学ぶべきことを自分で整理し、経験の転用可能性を具体的に語れているか",
    "専門性":     "PM 経験者として見て、判断ロジックが実務で通用するか。誰でも言える一般論や、"
                  "他社でもそのまま通用する模範解答になっていないか",
    "意欲":       "志望度の深さが伝わるか。企業研究の深さや、面接を重ねるごとに志望度が"
                  "深まっている様子が答えに滲んでいるか",
}

MAX_AXIS = 10
# 問ごとの合格線。90 = 該当軸すべて平均 9 点。役員面接で問われる「長く働くか／
# 5年後中核人材か／意欲は高いか」を通せる水準を想定している（甘く付けない）。
TARGET_SCORE = 90
# 1 巡で書き直す上限。gates.MAX_REPAIR_TARGETS と同じ思想 — 長さが暴れないよう蓋をする。
# 90 点は厳しく、不合格の問が多く出うるため 5 の元の値より広めに取る。
MAX_FINDINGS = 24
# 批評 → 是正の最大巡回数。1 巡 = LLM 2 call（批評 + 是正）
MAX_ROUNDS = 5
# 対象問の合計点が「悪化も改善もしていない」巡がこの回数続いたら打ち切る
STALL_ROUNDS = 2

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)
_OBJECT_RE = re.compile(r"\{.*\}", re.S)
# 引用照合の正規化 — 空白・引用符・読点の揺れは無視する（LLM は必ず揺らす）
_NOISE_RE = re.compile(r"[\s　「」『』\"'、,。.・…]")


def axes_for(category: str) -> tuple[str, ...]:
    """このカテゴリの問に適用する採点軸（全問共通 2 軸 + カテゴリ軸 1〜2 個）。"""

    return UNIVERSAL_AXES + CATEGORY_AXES.get(category, DEFAULT_CATEGORY_AXES)


@dataclass(frozen=True)
class Finding:
    """1 問への指摘。`quote` は本文に実在する逐語引用（`verify` 済み）。"""

    label: str
    quote: str
    problem: str
    fix: str


@dataclass(frozen=True)
class ItemScore:
    """1 問の採点。軸はカテゴリから決まる（`axes_for`）— 問ごとに違ってよい。"""

    label: str
    category: str
    scores: dict[str, int]

    @property
    def axes(self) -> tuple[str, ...]:
        return axes_for(self.category)

    @property
    def total(self) -> int:
        """0〜100。この問に適用される軸だけで換算する。"""

        axes = self.axes
        if not axes:
            return 0
        got = sum(self.scores.get(axis, 0) for axis in axes)
        return round(got * 100 / (MAX_AXIS * len(axes)))

    @property
    def passed(self) -> bool:
        return self.total >= TARGET_SCORE


@dataclass(frozen=True)
class Critique:
    items: dict[str, ItemScore] = field(default_factory=dict)  # label -> ItemScore
    findings: list[Finding] = field(default_factory=list)
    dropped: int = 0  # 引用が本文に無く捨てた指摘の数（隠さず監査に出す）

    @property
    def passed(self) -> bool:
        """全問が個別に合格線へ到達しているか。平均では判定しない。"""

        return bool(self.items) and all(item.passed for item in self.items.values())

    @property
    def failing(self) -> list[ItemScore]:
        """不合格の問。点が低い順 — 是正の優先度に使う。"""

        return sorted((i for i in self.items.values() if not i.passed),
                      key=lambda i: i.total)

    @property
    def total(self) -> int:
        """監査表示用の全問平均。**合否判定には使わない**（合否は問ごと・`passed`）。"""

        if not self.items:
            return 0
        return round(sum(i.total for i in self.items.values()) / len(self.items))

    @property
    def weakest(self) -> list[str]:
        """不合格の問が抱える軸のうち、特に低い（8点未満）ものを最大4個。是正指示の焦点にする。"""

        weak: dict[str, int] = {}
        for item in self.failing:
            for axis in item.axes:
                score = item.scores.get(axis, 0)
                if score < 8:
                    weak[axis] = min(weak.get(axis, MAX_AXIS), score)
        return [axis for axis, _ in sorted(weak.items(), key=lambda kv: kv[1])][:4]


def _norm(text: str) -> str:
    return _NOISE_RE.sub("", text)


# ── 批評の解析と検証 ─────────────────────────────────


def parse(raw: str, items: list[Labelled]) -> tuple[dict[str, ItemScore], list[dict]]:
    """LLM の生出力 → (問ごとの採点, 未検証の指摘)。壊れていれば ({}, [])。

    出てこなかった問は 0 点の `ItemScore` で埋める（黙って合格扱いにしない）。
    """

    scores: dict[str, ItemScore] = {
        item.label: ItemScore(label=item.label, category=item.category, scores={})
        for item in items
    }

    fence = _FENCE_RE.search(raw)
    body = fence.group(1) if fence else raw
    match = _OBJECT_RE.search(body)
    if not match:
        return scores, []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return scores, []
    if not isinstance(data, dict):
        return scores, []

    raw_items = data.get("items")
    if isinstance(raw_items, list):
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            base = scores.get(label)
            if base is None:
                continue  # ラベルが照合できない出力は捨てる（作り話の問を採点扱いしない）
            raw_scores = entry.get("scores")
            item_scores: dict[str, int] = {}
            for axis in base.axes:
                value = raw_scores.get(axis) if isinstance(raw_scores, dict) else None
                try:
                    number = int(value)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    number = 0
                item_scores[axis] = max(0, min(MAX_AXIS, number))
            scores[label] = ItemScore(label=label, category=base.category, scores=item_scores)

    findings = data.get("findings")
    if not isinstance(findings, list):
        findings = []
    return scores, [f for f in findings if isinstance(f, dict)]


def verify(raw_findings: list[dict],
           items: list[Labelled]) -> tuple[list[Finding], int]:
    """指摘を回答本文へ機械照合する。戻り値: (生き残った指摘, 捨てた数)。

    - 引用が**どの問の本文にも**無い指摘は捨てる（LLM の作り話）
    - 引用が在る問とラベルが食い違う指摘は、引用側へ引き当て直す
    - 直し方（fix）の無い指摘は是正に使えないので捨てる
    """

    bodies = [(item.label, _norm(item.body)) for item in items]
    kept: list[Finding] = []
    dropped = 0
    for entry in raw_findings:
        quote = str(entry.get("quote") or "").strip()
        problem = str(entry.get("problem") or "").strip()
        fix = str(entry.get("fix") or "").strip()
        if not quote or not problem or not fix:
            dropped += 1
            continue
        needle = _norm(quote)
        owner = next((label for label, body in bodies if needle and needle in body), None)
        if owner is None:
            dropped += 1
            continue
        kept.append(Finding(label=owner, quote=quote, problem=problem, fix=fix))
    return kept[:MAX_FINDINGS], dropped


def round_delta(previous: "Critique", current: "Critique", targets: list[str]) -> int:
    """この巡で書き直しの対象にした問（targets）の合計点の増減。

    全問を毎巡フル再評価しているため、書き直していない問の採点ブレを拾わない
    よう対象問だけで比較する。負なら「悪化」— 呼び出し側はこの巡を捨てる。
    """

    prev_sum = sum(previous.items[label].total for label in targets if label in previous.items)
    cur_sum = sum(current.items[label].total for label in targets if label in current.items)
    return cur_sum - prev_sum


# ── LLM 呼び出し ────────────────────────────────────


_PROMPT = """\
あなたは日本企業の中途採用で最終面接を担当する役員・人事責任者です。以下は、ある
応募者が用意した想定問答です。**通すか落とすかを決める目で**読み、問ごとに採点と
指摘を返してください。

# 採点の考え方
問ごとに、その問が本当は何を確かめようとしているかで見る軸が変わります。
「転職理由」は定着可能性・意欲を、「AI の PoC 展開」は専門性を見る、というように
下の一覧で**問ごとに指定した軸だけ**を採点してください（指定されていない軸は
採点しない）。

{axis_glossary}

各軸 0〜10 点。目安: 10 = このまま通す / 8 = 通せるが弱い所がある / 5 = 読めるが
刺さらない / 3 = この答えでは判断できない / 0 = 落とす。**全軸 9 点以上を
安易に付けない。** 一つでも「誰でも言える」答えが混ざっていれば専門性・定着可能性は
6 点以下です。

# 指摘（最大 {max_findings} 件）
**9 点未満の軸を持つ問には必ず 1 件書いてください。** 良い問（全軸 9 点以上）への
賞賛は書きません。1 件につき:

- `label`  … その問の見出し（下の一覧のとおり一字一句写す）
- `quote`  … **回答本文からの逐語引用**。1 文をそのまま写す。要約・言い換えは禁止。
             引用が本文に無い指摘は機械照合で捨てられます。
- `problem`… なぜ面接官として物足りないか（1 文）
- `fix`    … どう直すか（1 文）

`fix` の制約（厳守）:
- **応募者が持っていない実績・数字を書けと指示しない。** 素材にある別の場面へ
  差し替える / 判断の理由を足す / 質問に正面から答える、までしか言えません。
- 「もっと具体的に」のような、そのままでは書き直せない指示を書かない。

# 出力
JSON オブジェクトだけを出力する。説明文・前置き・後書きは書かない。

{{"items": [{{"label": "...", "scores": {{"軸名": 0}}}}],
  "findings": [{{"label": "...", "quote": "...", "problem": "...", "fix": "..."}}]}}

{context}

# 採点する想定問答（全 {total} 問）
{listing}
"""


def _axis_glossary() -> str:
    lines = ["全軸の定義:"]
    lines += [f"- `{axis}` … {desc}" for axis, desc in AXIS_DESC.items()]
    return "\n".join(lines)


def _listing(items: list[Labelled]) -> str:
    by_category: dict[str, list[Labelled]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    blocks = []
    for category, group in by_category.items():
        axes = axes_for(category)
        blocks.append(f"## 分類「{category or '未分類'}」— この分類の採点軸: {' / '.join(axes)}")
        for item in group:
            points = "\n".join(f"{i}. {p}" for i, p in enumerate(item.points, start=1))
            blocks.append(f"### {item.label}\nQ. {item.question}\n{item.conclusion}\n{points}")
    return "\n\n".join(blocks)


def review(context: str, items: list[Labelled]) -> Critique | None:
    """想定問答を面接官の目で、問ごとにその分類の軸で採点する（LLM 1 call）。失敗したら None。

    None は「批評できなかった」であって「合格」ではない。呼び出し側は迭代を
    打ち切るだけで、通過したことにしてはいけない。
    """

    if not items:
        return None
    prompt = _PROMPT.format(
        axis_glossary=_axis_glossary(),
        max_findings=MAX_FINDINGS,
        context=context,
        total=len(items),
        listing=_listing(items),
    )
    accept = {"includesAll": ["items"], "minChars": 120}
    try:
        raw = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    except Exception:
        return None
    scores, raw_findings = parse(raw, items)
    if not scores:
        return None
    findings, dropped = verify(raw_findings, items)
    return Critique(items=scores, findings=findings, dropped=dropped)


# ── 監査レポート ────────────────────────────────────


def render_report(history: list[Critique], adopted: int | None = None) -> str:
    """`adopted` = パックに残した版（1 起点）。書き直して下がった巡があるとき、
    最終巡と採用版は一致しない。どちらも隠さずに出す。"""

    if not history:
        return ""
    index_adopted = len(history) if adopted is None else adopted
    chosen = history[index_adopted - 1]
    n_items = len(chosen.items)
    n_passed = sum(1 for i in chosen.items.values() if i.passed)

    lines = ["## 面接官の目での採点（批評 → 是正の巡回、問ごと・カテゴリ別の軸）", ""]
    lines.append("| 巡 | 平均点 | 合格した問 | 指摘 | 引用が本文に無く却下 |")
    lines.append("|---|---|---|---|---|")
    for index, critique in enumerate(history, start=1):
        n_ok = sum(1 for i in critique.items.values() if i.passed)
        mark = " ★採用" if index == index_adopted else ""
        lines.append(f"| v{index}{mark} | **{critique.total}** | {n_ok}/{len(critique.items)} | "
                     f"{len(critique.findings)} | {critique.dropped} |")
    lines += ["", f"合格線は問ごとに {TARGET_SCORE} 点（該当軸の平均 9 点以上）。"
                  f"採用した v{index_adopted} は {n_passed}/{n_items} 問が到達"
                  f"（{'全問到達' if chosen.passed else '未到達の問が残っている — 下の指摘・不合格一覧を参照'}）。", ""]
    if index_adopted != len(history):
        lines += [f"v{len(history)} は書き直しの対象問の合計点が下がったため捨て、v{index_adopted} を"
                  "パックに残した（迭代は良くなる方向にしか進めない）。", ""]

    if not chosen.passed:
        lines += ["### 合格線に届いていない問", ""]
        for item in chosen.failing:
            axes_display = ", ".join(f"{a}={item.scores.get(a, 0)}" for a in item.axes)
            lines.append(f"- **{item.label}** — {item.total} 点（{axes_display}）")
        lines.append("")

    for index, critique in enumerate(history, start=1):
        if not critique.findings:
            continue
        is_adopted = index == index_adopted
        # 前の巡の指摘は「書き直しに回した」までしか言えない。実際に直ったかは
        # 次の巡の点数が示すだけで、指摘ごとの消化は機械で追っていない。
        title = ("パックに残っている指摘" if is_adopted
                 else f"v{index} で指摘 → 書き直しに回した")
        lines += [f"### {title}", ""]
        for finding in critique.findings:
            lines += [f"- **{finding.label}** — {finding.problem}",
                      f"  - 該当: 「{finding.quote}」",
                      f"  - 直し方: {finding.fix}"]
        lines.append("")
    lines += [
        "採点は LLM が面接官役として付けたもので、実際の合否ではない。指摘は"
        "回答本文への逐語引用が在るものだけを残し、在らないものは却下している"
        "（却下数は上表）。**軸そのものの妥当性、および軸とカテゴリの対応付けは"
        "機械では検証していない。**",
        "",
    ]
    return "\n".join(lines)
