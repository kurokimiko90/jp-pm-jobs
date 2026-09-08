"""機械閘門 — LLM を呼ばずに「信じられる問答か」を判定する。

7 つを見る。抽象的な良し悪しは判定しない（できないものは主張しない）:

  A 事実錨定 — 回答中の数字が素材に存在するか / 要点に具体物があるか
  B 要件覆蓋 — JD の要件が最低 1 問で扱われているか
  C 一貫性   — 経験年数など単一値であるべき事実が複数の値で語られていないか
  D 素材偏り — 同じ実績が全問で使い回されていないか
  E 口語     — そのまま口に出せない書面語が残っていないか
  F 人物像覆蓋 — JD の「求める人物像」の各特性が最低 1 問で具体的実績と結び
    付いているか（B と同じキーワード近似・報告のみで再生成しない）
  G 製品覆蓋 — JD・会社事実に登場する製品が最低 1 問で「関係・貢献・次にする
    こと」と結び付いているか（B・F と同じキーワード近似・報告のみで再生成しない）

F は「条件を満たすか」ではなく「面接官が求めている人物のタイプに見えるか」を
見る軸で、B（MUST/WANT の逐条チェック）とは別の観点。求める人物像は条件を
満たしていても「この人はうちが欲しい人物か」で落ちる面接があるため、先に
LLM に人物像を言語化させ、生成時にそこへ実績を対応させることを促す
（`generate.persona_traits` → `generate.jd_specific` のプロンプトに反映）。

G は「この求人の背景にある具体的なプロダクトへの理解と関わり方」を見る軸。
製品名を挙げるだけの一般論（「AI 技術に興味があります」）で終わる面接を防ぐため、
JD・会社事実に出てくる製品ごとに、応募者との関係・入社後の貢献・最初に着手
することまで踏み込ませる（`generate.product_relationships` →
`generate.jd_specific` のプロンプトに反映）。

E をここで見るのは、指揮中心の accept へ禁止語を入れられないため。十数問の一括
出力では 1 語の違反で全体が差し戻され、brain 総当たりの末に 500 になる
（`generate._ACCEPT_BASE` の注記）。機械で直せる語は generate 側で置換済み。

C は「同じ事実が食い違う」全般ではなく、実際に事故になった型（年数の不一致）に
絞る。D は「面接官が同じ話を何度も聞かされる」型の事故を数で捉えるだけで、
話の面白さは判定しない。網羅を装わないため、限界を report に明記する。

検査対象は結論文と要点の両方。質問文は含めない — JD の語をそのまま含む質問文まで
数えると、B の覆蓋判定が「JD の語を質問に書いたから覆蓋できている」と自己成就する。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from interview.qa_quality import unsupported_numbers

from .render import Labelled, answers_text

# 要点が具体物を含むかの判定 — 数字 / 英数字の固有名 / カタカナ語 3 字以上
_CONCRETE_RE = re.compile(r"\d|[A-Za-z]{2,}|[ァ-ヶー]{3,}")
_YEARS_RE = re.compile(r"(\d+)\s*年(?:間|以上|弱|強)?")
_YEARS_CONTEXT = ("経験", "PM", "プロダクトマネ", "PdM", "従事", "携わ")
# 要件からキーワードを拾う — 漢字/カタカナの塊
_KEYWORD_RE = re.compile(r"[一-鿿]{2,}|[ァ-ヶー]{3,}|[A-Za-z]{3,}")
_STOPWORDS = {
    "経験", "能力", "以上", "以下", "程度", "年以上", "年程度", "年数",
    "業務", "対応", "推進", "実施", "必要", "歓迎", "必須", "尚可",
    "こと", "もの", "など", "ある", "する", "できる", "方", "者", "スキル",
}

# D 用 — 「実績の指紋」は数量表現で捉える。抽象語は指紋にしない
_UNITS = ("年", "件", "万", "億", "％", "%", "店舗", "ブランド", "人", "回",
          "言語", "項目", "社", "か月", "ヶ月", "ユーザー", "種類", "画面", "キー")
_FINGERPRINT_RE = re.compile(
    r"(\d[\d,]*)\s*(" + "|".join(re.escape(unit) for unit in _UNITS) + r")")
# 使い回しとみなす下限 — 全問数に対する割合と絶対数の大きい方。
# 面接官は同じ数字を 4 回聞けば「その話しか無いのか」と受け取る。実測（77 問）で
# 12 % 相当（9 問）だと 8 問使い回しの実績が素通りしたため 6 % まで下げた。
OVERUSE_RATIO = 0.06
OVERUSE_MIN = 4
# 同じ型の締め（「御社でも〜」）が全問のこの割合を超えたら監査に出す
TEMPLATE_ENDING_RATIO = 0.25
# 1 巡で作り直す上限（LLM 1 回にまとめて送るが、長さが暴れないよう蓋をする）
MAX_REPAIR_TARGETS = 8

# E 用 — 面接で口に出すと浮く書面語・解説目線。機械置換できないものだけ挙げる
WRITTEN_TERMS = ("当該", "上述", "下記", "寄与", "勘案", "帰属", "所存",
                 "面接では", "資料上では", "と答えます", "NG回答", "回答例")


@dataclass
class GateReport:
    unsupported: dict[str, list[str]] = field(default_factory=dict)
    thin_points: list[str] = field(default_factory=list)
    uncovered_requirements: list[str] = field(default_factory=list)
    covered_requirements: int = 0
    total_requirements: int = 0
    year_conflicts: list[str] = field(default_factory=list)
    year_offenders: list[str] = field(default_factory=list)
    overused: dict[str, int] = field(default_factory=dict)
    overused_offenders: list[str] = field(default_factory=list)
    written_style: dict[str, list[str]] = field(default_factory=dict)
    template_endings: list[str] = field(default_factory=list)
    uncovered_persona: list[str] = field(default_factory=list)
    covered_persona: int = 0
    total_persona: int = 0
    uncovered_products: list[str] = field(default_factory=list)
    covered_products: int = 0
    total_products: int = 0

    @property
    def blocking(self) -> list[str]:
        """作り直すべき質問ラベル。要件覆蓋は報告のみで再生成対象にしない。"""

        return sorted(
            set(self.unsupported) | set(self.thin_points)
            | set(self.year_offenders) | set(self.overused_offenders)
            | set(self.written_style)
        )

    @property
    def ok(self) -> bool:
        return not self.blocking


# 要件の語と、回答側で実際に使われる略語。同義を見ないと「プロダクトマネジメント
# 経験」が未覆蓋へ倒れる — 答えは PdM / PM と書くのが自然なため
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "プロダクトマネジメント": ("PdM", "PM", "プロダクトマネージャー"),
    "プロダクトマネージャー": ("PdM", "PM", "プロダクトマネジメント"),
    "要件定義": ("要件", "仕様"),
    "折衝": ("調整", "交渉"),
    "多言語": ("日本語", "英語", "中国語"),
}


def _keywords(text: str) -> set[str]:
    return {
        word for word in _KEYWORD_RE.findall(text)
        if word not in _STOPWORDS and len(word) >= 2
    }


def _matches(word: str, body: str) -> bool:
    return word in body or any(alias in body for alias in _SYNONYMS.get(word, ()))


def _fingerprints(text: str) -> set[str]:
    return {f"{num.replace(',', '')}{unit}" for num, unit in _FINGERPRINT_RE.findall(text)}


def fact_anchor(items: list[Labelled], material: str) -> tuple[dict, list]:
    """A 事実錨定 — (素材にない数字, 具体物のない要点) を返す。

    数字は結論文も対象にする。具体物の有無は要点だけを見る（結論は 1 文で
    抽象的になるのが自然なので、そこを咎めると全問が落ちる）。
    """

    unsupported: dict[str, list[str]] = {}
    thin: list[str] = []
    for item in items:
        suspects = unsupported_numbers(item.body, material)
        if suspects:
            unsupported[item.label] = suspects
        if item.points and not any(_CONCRETE_RE.search(point) for point in item.points):
            thin.append(item.label)
    return unsupported, thin


def requirement_coverage(qa_text: str, requirements: list[str],
                         min_hits: int = 2) -> tuple[list[str], int]:
    """B 要件覆蓋 — JD 要件ごとにキーワードが回答本文へ届いているかを見る。

    完全な意味照合はできないので、キーワード一致数の閾値で「触れていない疑い」を
    挙げるに留める。挙がったものは人が読んで判断する前提。
    """

    body = qa_text
    uncovered: list[str] = []
    covered = 0
    for requirement in requirements:
        words = _keywords(requirement)
        if not words:
            continue
        # 語が少ない要件は 1 語一致で可とする（2 語必須だと固有語 1 つの要件が
        # 常に未覆蓋へ倒れ、報告が信用できなくなる）
        need = 1 if len(words) <= 2 else min_hits
        hits = sum(1 for word in words if _matches(word, body))
        if hits >= need:
            covered += 1
        else:
            uncovered.append(requirement)
    return uncovered, covered


def year_consistency(items: list[Labelled]) -> tuple[list[str], list[str]]:
    """C 一貫性（年数のみ）— 経験年数が複数の値で語られていないか。

    多数派の値を正とし、少数派を語っている問だけを再生成対象として返す。
    正典（手書き）も対象にする — 食い違いは手書き側にあることもあるため、
    どちらが少数派かだけで機械的に決める。
    """

    # context → 値 → その値を語るラベル
    speakers: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for item in items:
        body = item.body
        for match in _YEARS_RE.finditer(body):
            window = body[max(0, match.start() - 18):match.end() + 12]
            for context in _YEARS_CONTEXT:
                if context in window:
                    speakers[context][match.group(1)].add(item.label)

    findings: list[str] = []
    offenders: set[str] = set()
    for context, values in speakers.items():
        if len(values) <= 1:
            continue
        findings.append(f"「{context}」の年数が複数: {' / '.join(sorted(values))}")
        majority = max(values, key=lambda value: (len(values[value]), value))
        for value, labels in values.items():
            if value != majority:
                offenders |= labels
    return findings, sorted(offenders)


def evidence_balance(items: list[Labelled]) -> tuple[dict[str, int], list[str]]:
    """D 素材偏り — 同じ実績（数量表現）が何問で使われているかを数える。

    面接官が同じ数字を何度も聞かされる状態を防ぐ。使い回しの多い実績だけで
    構成され、固有の中身が無い問を再生成対象として返す。正典は対象外
    （手書きの答えを機械が捨てない — 報告だけして人が直す）。
    """

    if not items:
        return {}, []
    per_item = {item.label: _fingerprints(item.body) for item in items}
    counter: Counter[str] = Counter()
    for prints in per_item.values():
        counter.update(prints)

    limit = max(OVERUSE_MIN, int(len(items) * OVERUSE_RATIO))
    overused = {key: count for key, count in counter.items() if count > limit}
    if not overused:
        return {}, []

    offenders: list[tuple[int, str]] = []
    for item in items:
        if item.origin == "core":
            continue
        prints = per_item[item.label]
        if not prints:
            continue
        reused = prints & set(overused)
        if reused == prints:  # この問の実績が全部「使い回し」だけで出来ている
            offenders.append((sum(overused[key] for key in reused), item.label))
    offenders.sort(reverse=True)
    return (dict(sorted(overused.items(), key=lambda kv: -kv[1])),
            [label for _, label in offenders[:MAX_REPAIR_TARGETS]])


def template_endings(items: list[Labelled]) -> list[str]:
    """締めの型が全問で反復していないか（報告のみ・再生成はしない）。

    「御社でも活かせます」で終わる問が並ぶと、聞く側には全部同じ答えに聞こえる。
    ただし何問目を削るべきかは機械では決められないので、挙げるだけにする。
    """

    endings = [item.points[-1] for item in items if item.points]
    if not endings:
        return []
    offenders = [item.label for item in items
                 if item.points and item.points[-1].lstrip().startswith("御社")]
    if len(offenders) <= len(endings) * TEMPLATE_ENDING_RATIO:
        return []
    return offenders


def oral_style(items: list[Labelled]) -> dict[str, list[str]]:
    """E 口語 — そのまま口に出せない書面語・解説目線が残っている問を返す。

    正典も対象にする（手書き側に残っていれば pack 内で直す）。機械置換できる
    「貴社→御社」等は generate 側で処理済みなので、ここには挙がらない。
    """

    findings: dict[str, list[str]] = {}
    for item in items:
        hits = [term for term in WRITTEN_TERMS if term in item.body]
        if hits:
            findings[item.label] = hits
    return findings


def run(items: list[Labelled], material: str, requirements: list[str],
        persona_traits: list[str] | None = None,
        product_names: list[str] | None = None) -> GateReport:
    unsupported, thin = fact_anchor(items, material)
    uncovered, covered = requirement_coverage(answers_text(items), requirements)
    year_findings, year_offenders = year_consistency(items)
    overused, overused_offenders = evidence_balance(items)
    persona_traits = persona_traits or []
    uncovered_persona, covered_persona = requirement_coverage(
        answers_text(items), persona_traits)
    product_names = product_names or []
    uncovered_products, covered_products = requirement_coverage(
        answers_text(items), product_names)
    return GateReport(
        unsupported=unsupported,
        thin_points=thin,
        uncovered_requirements=uncovered,
        covered_requirements=covered,
        total_requirements=len([r for r in requirements if _keywords(r)]),
        year_conflicts=year_findings,
        year_offenders=year_offenders,
        overused=overused,
        overused_offenders=overused_offenders,
        written_style=oral_style(items),
        template_endings=template_endings(items),
        uncovered_persona=uncovered_persona,
        covered_persona=covered_persona,
        total_persona=len([t for t in persona_traits if _keywords(t)]),
        uncovered_products=uncovered_products,
        covered_products=covered_products,
        total_products=len([p for p in product_names if _keywords(p)]),
    )


def render_report(report: GateReport, total_questions: int) -> str:
    lines = [
        "# QA 機械監査",
        "",
        f"- 問数: {total_questions}",
        f"- JD 要件覆蓋: {report.covered_requirements} / {report.total_requirements}",
        f"- 素材にない数字を含む問: {len(report.unsupported)}",
        f"- 具体物のない問: {len(report.thin_points)}",
        f"- 年数の不一致: {len(report.year_conflicts)}",
        f"- 使い回しの多い実績: {len(report.overused)}",
        f"- 書面語が残っている問: {len(report.written_style)}",
        f"- 同じ型で締めている問: {len(report.template_endings)}",
        f"- 求める人物像 覆蓋: {report.covered_persona} / {report.total_persona}",
        f"- 製品 覆蓋: {report.covered_products} / {report.total_products}",
        "",
        "> 覆蓋判定はキーワード一致による近似。挙がった要件は人が読んで確認する。",
        "> 一貫性検査は経験年数のみを対象とし、事実の食い違い全般は検出しない。",
        "> 素材偏りは数量表現の出現問数のみを見る。話の重複そのものは判定しない。",
        "> 締めの型は報告のみ — どの問を削るかは機械では決められない。",
        "",
    ]
    if report.unsupported:
        lines += ["## 素材にない数字", ""]
        lines += [f"- {label}: {' / '.join(nums)}" for label, nums in report.unsupported.items()]
        lines.append("")
    if report.thin_points:
        lines += ["## 具体物のない回答", ""] + [f"- {label}" for label in report.thin_points] + [""]
    if report.year_conflicts:
        lines += ["## 年数の不一致", ""] + [f"- {item}" for item in report.year_conflicts]
        if report.year_offenders:
            lines.append(f"- 少数派として作り直した問: {', '.join(report.year_offenders)}")
        lines.append("")
    if report.overused:
        lines += ["## 使い回しの多い実績（出現問数）", ""]
        lines += [f"- {key}: {count} 問" for key, count in report.overused.items()]
        lines += ["", "> 正典（C 番号）は手書きのため機械では作り直さない。"
                      "多すぎる場合は question-bank/core/ を人が直す。", ""]
    if report.written_style:
        lines += ["## 書面語が残っている回答", ""]
        lines += [f"- {label}: {' / '.join(terms)}"
                  for label, terms in report.written_style.items()] + [""]
    if report.template_endings:
        lines += ["## 「御社でも〜」で締めている問（多すぎる）", ""]
        lines += [f"- {label}" for label in report.template_endings]
        lines += ["", "> どれを削るかは機械では決められない。読んで、"
                      "会社固有の接続が言えない問は最後の要点を落とす。", ""]
    if report.uncovered_requirements:
        lines += ["## QA が触れていない疑いのある JD 要件", ""]
        lines += [f"- {item}" for item in report.uncovered_requirements] + [""]
    if report.uncovered_persona:
        lines += ["## QA が具体的実績で応えていない疑いのある「求める人物像」", ""]
        lines += [f"- {item}" for item in report.uncovered_persona]
        lines += ["", "> 条件（B）は満たしていても、この人物像に見えるかは別軸。"
                      "人が読んで、どの問の要点で対応させるか判断する。", ""]
    if report.uncovered_products:
        lines += ["## QA が「関係・貢献・次にすること」で応えていない疑いのある製品", ""]
        lines += [f"- {item}" for item in report.uncovered_products]
        lines += ["", "> 製品名が出てくるだけでは覆蓋済みにしない（近似のため実際は"
                      "触れているのに漏れて見えることもある）。人が読んで、どの問へ"
                      "関係・貢献・次にすることを足すか判断する。", ""]
    return "\n".join(lines)
