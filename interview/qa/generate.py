"""LLM 呼び出し層 — 求める人物像・登場製品の特定・正典の JD 向け調整・JD 特化問答・
深掘り 2 層・一括修復。

呼び出し回数は問数に依存させない（問ごとに呼ばない）。1 パックあたり
  persona_traits 1 + product_relationships 1 + tune_core 1 + jd_specific 1
  + drilldown 2 + repair ≤2 = 最大 8 回。すべて miko-ws 指揮中心（tools.miko_llm）経由。
"""

from __future__ import annotations

import re

from tools import miko_llm

from . import keywords
from .bank import POINT_RE, CoreAnswer
from .render import QA

_BLOCK_RE = re.compile(r"^###\s+(?:Q\.|(C\d+)\.)\s*(.+?)\s*$", re.MULTILINE)

# 新しく作る問だけに付ける。正典（`### C番号.`）のキーワードは taxonomy 側に固定で
# 持っているので、LLM には作らせない。
KEYWORD_RULE = f"""\
見出しのキーワード（厳守）:
- 見出しは「### Q. [キーワード] 質問文」の形にする。
- キーワードは質問の話題を表す {keywords.MAX_LEN} 文字以内の名詞。文・助詞・句読点は入れない。
  例: 「[権限設計] 〜」「[コスト] 〜」「[多言語] 〜」。
- 質問文そのものは変えない。キーワードを付けるだけ。
"""

# 正典生成（tools/oneoff/build_qa_core.py）とも共有する。ここを緩めると、
# 3 点それぞれに別プロジェクトを 1 行ずつ並べた「職務経歴書の朗読」に戻る。
POINT_STRUCTURE_RULES = """\
要点の組み立て（厳守。ここが一番大事）:
- 要点 1 は実際にあった一場面を書く。「どんな状況で / 自分が何を判断し / どうなったか」を
  2 文までで繋ぐ。担当範囲や肩書きの列挙にしない。
- 要点 2 はその判断の理由、または次も同じ結果を出せる根拠を書く。
- 要点 3 を書くのは、この会社の事業・組織に固有の接続が言える問だけ。「御社でも
  活かせます」「御社でも貢献できます」のような、どの会社にも言える締めは書かない。
  言うことが無ければ 2 点で終える。全問の 3 割以上が同じ型の締めになってはいけない。
- 別々のプロジェクト名を 1 行ずつ 3 つ並べる書き方は禁止。それは職務経歴書であって
  面接の答えではない。1 問では原則 1 つの場面を深く話す。
"""

CONTENT_RULES = """\
内容（厳守）:
- 素材にない数字・実績・企業名を作らない。素材の数字はそのまま使う。
- 同じ実績・同じ数字を問ごとに繰り返さない。素材の中から、まだ使っていない場面を選ぶ。
- 面接でそのまま口に出せる、です・ます調の話し言葉。一文 90 字以内、回答全体は 30〜60 秒。
- 「当該」「上述」「寄与」「勘案」「帰属」等の書面語、「面接では〜と答えます」の
  ような解説目線は禁止。「貴社」ではなく「御社」。
"""

STYLE_RULES = f"""\
形式（厳守）:
- 各問は「### Q. 質問文」の見出し 1 行 → 答えの芯を 1 文 → 「1. 」「2. 」「3. 」の要点。
- 見出しと回答本文以外は書かない。狙い・解説・NG 例・前置き・後書きは一切禁止。
- 1 文目は「結論として」「結論から言うと」「まず」などの前置きを置かず、答えから入る。

{POINT_STRUCTURE_RULES}
{CONTENT_RULES}"""

# 指揮中心の accept は「形式」だけを見る。用語の禁止をここへ入れてはいけない —
# 十数問の一括出力では 1 語の違反で全体が差し戻され、brain 総当たりの末に
# gateway が 500 を返す。用語は _clean() で機械修正し、残りは gates が拾う。
_ACCEPT_BASE = {
    "regex": r"^1\. ",
    "regexFlags": "m",
}

# 機械的に直せる言い換え — 直せるものは LLM を呼び直さず手元で直す
_REWRITES = (
    ("貴社", "御社"),
)
# 答えの頭に付く前置き。付いていたら剥がす（禁止しても LLM は必ず付ける）
_PREFACE_RE = re.compile(
    r"^(?:結論(?:として|から言うと|から申し上げると|としては)|まず|端的に言うと)"
    r"[、,：:]\s*")


def _clean(line: str) -> str:
    """機械的に直せる違反だけをその場で直す（呼び直さない）。"""

    for old, new in _REWRITES:
        line = line.replace(old, new)
    return _PREFACE_RE.sub("", line).strip()


def _parse(text: str) -> list[tuple[str | None, str, str, str, list[str]]]:
    """`### Q.` / `### C01.` ブロック → (qid, キーワード, 質問, 結論, 要点)。

    見出しの `[キーワード]` はここで剥がす。以降の処理（重複判定・修復の
    引き当て）はすべて素の質問文で行う。
    """

    marks = list(_BLOCK_RE.finditer(text))
    out: list[tuple[str | None, str, str, str, list[str]]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        conclusion_lines: list[str] = []
        points: list[str] = []
        for raw in text[mark.end():end].splitlines():
            line = raw.strip()
            if not line or line == "---" or line.startswith("<!--") or line.startswith("#"):
                continue
            point = POINT_RE.match(line)
            if point:
                points.append(point.group(2).strip())
            elif not points:
                conclusion_lines.append(line)
        keyword, question = keywords.split(mark.group(2).strip())
        if question and points:
            conclusion = _clean("".join(conclusion_lines).strip())
            out.append((mark.group(1), keyword, question, conclusion,
                        [_clean(point) for point in points]))
    return out


def _listing(items) -> str:
    return "\n\n".join(
        f"### Q. {item.question}\n{item.conclusion}\n"
        + "\n".join(f"{i}. {p}" for i, p in enumerate(item.points, start=1))
        for item in items
    )


def _qa(keyword: str, question: str, conclusion: str, points: list[str],
        section: str) -> QA:
    """LLM がキーワードを付け忘れた問は規則で埋める（見出しを裸で出さない）。"""

    return QA(question, conclusion, points[:3], section,
              keywords.trim(keyword) or keywords.fallback(question))


def _avoid_block(questions: list[str], evidence: list[str]) -> str:
    parts = []
    if questions:
        listed = "\n".join(f"- {q}" for q in questions)
        parts.append(f"# 既に別途用意済みの質問（作らない・言い換えも作らない）\n{listed}")
    if evidence:
        listed = "\n".join(f"- {e}" for e in evidence)
        parts.append(
            "# 既に他の問で使った実績（別の場面を優先して選ぶ。"
            "どうしても必要なときだけ再使用可）\n" + listed)
    return "\n\n".join(parts)


def persona_traits(review_context: str) -> list[str]:
    """求人側の文脈だけから「求める人物像」を言語化する（1 回の呼び出し）。

    JD に「求める人物像」という見出しがあるとは限らない（実測: 明記された求人票は
    全体の一部）。無くても、募集背景・業務内容・企業文化の書きぶりから「どういう
    タイプの人を採りたいか」は読み取れることが多い — ここを正規表現ではなく LLM に
    読ませる理由そのもの。応募者プロフィールは渡さない（`_review_context` と同じ
    理由 — 求人側だけで判断する客観的な人物像であるべきで、応募者に寄せて都合よく
    書かせない）。失敗したら空リストを返す（呼び出し側が regex フォールバックへ回す）。
    """

    prompt = f"""\
以下の求人情報を読み、この会社がこのポジションで採りたい「人物像」を
3〜6 個、簡潔な一文ずつで言語化してください。

指示:
- JD に「求める人物像」という見出しがあれば、そこを最優先で使う（言い換えず、
  意味を保ったまま簡潔にする）。
- 見出しが無い、または内容が薄い場合は、募集背景・業務内容・企業文化・バリューの
  書きぶりから、この会社が実際に採りたいタイプを推測して補う。「推測です」とは
  書かず、素直に人物像として書く。
- 抽象的な美辞麗句（「成長意欲がある方」のような空文句）だけで終わらせず、
  何をする人物か・どう仕事に向き合う人物かが分かる具体性を持たせる。
- 求人票に無い情報を作らない。会社の実際の事業内容・業務内容からの妥当な推測に
  留め、存在しない実績や制度を書かない。

{review_context}

# 出力
1 行 1 特性で、箇条書き（各行の先頭に「- 」）のみを出力する。前置き・見出し・
番号付けは不要。
"""
    accept = {"regex": r"^- ", "regexFlags": "m", "minChars": 30}
    try:
        result = miko_llm.text(prompt, timeout=300, opts={"accept": accept})
    except Exception:
        return []
    traits = [line.strip()[2:].strip() for line in result.splitlines()
              if line.strip().startswith("- ")]
    return [t for t in traits if t][:6]


def product_relationships(review_context: str) -> list[str]:
    """JD・会社事実に登場する製品・プロダクト名を拾う（1 回の呼び出し）。

    製品の**存在**を特定するだけの層。求人側の文脈だけで判定する（`persona_traits`
    と同じ理由 — 応募者プロフィールは渡さない）。関係・貢献の中身を書くのは
    `jd_specific`（応募者プロフィールを持つ側）の役目 — ここで名前だけ拾って
    そちらのプロンプトへ「この製品ごとに 1 問答えよ」という材料として渡す。
    実在しない製品名を作らせないよう、JD・会社事実に**書かれている名称のみ**を
    使わせる。失敗・該当なしは空リストを返す（呼び出し側は製品関連の追加指示を
    スキップするだけで、パック生成自体は止めない）。
    """

    prompt = f"""\
以下の求人情報（JD 本文・会社事実）を読み、そこに登場する製品名・サービス名・
プロダクト名をすべて列挙してください。

指示:
- JD 本文または会社事実に**実際に書かれている名称のみ**を使う。存在しない製品名を
  作らない。表記はそのまま（略称・英字表記もそのまま）にする。
- 会社名そのもの、汎用的な事業カテゴリ名（「AI ソリューション」等）は含めない。
  固有の製品・サービスの名称だけを挙げる。
- 該当する製品名が無ければ、他には何も書かず「なし」とだけ出力する。

{review_context}

# 出力
1 行 1 製品名で、箇条書き（各行の先頭に「- 」）のみを出力する。説明文は不要。
"""
    accept = {"regex": r"^- ", "regexFlags": "m", "minChars": 3}
    try:
        result = miko_llm.text(prompt, timeout=300, opts={"accept": accept})
    except Exception:
        return []
    names = [line.strip()[2:].strip() for line in result.splitlines()
             if line.strip().startswith("- ")]
    return [n for n in names if n and n != "なし"][:8]


def tune_core(context: str, targets: list[CoreAnswer]) -> dict[str, CoreAnswer]:
    """jd_dependent な正典回答を、この求人向けに 1 回の呼び出しで調整する。

    骨格と事実は変えず、要点の 1 つをこの会社に接続する形へ差し替えるだけ。
    """

    if not targets:
        return {}
    prompt = f"""\
以下は応募者の汎用の想定回答です。この求人向けに調整してください。

調整の方針:
- 事実・数字・骨格は変えない。新しい実績を足さない。
- 要点のうち 1 つを、この会社・この JD に接続する内容へ書き換える（どの要点を
  書き換えるかは質問ごとに最も自然な位置を選ぶ）。文脈の「求める人物像」に合う
  実績が既にこの問にあるなら、そこへ繋げる書き換えを優先する。
- 全問を同じ型で締めない。「御社でも〜したいです」で終わる問は全体の半分以下にする。
- 結論文は、この会社に対する答えとして自然になるよう必要なら整える。
- 質問文は一字一句変えない。見出しは `### C01.` の形式のまま返す。

{STYLE_RULES}

{context}

# 汎用の想定回答（これを調整する）
{_listing(targets)}

# 出力
調整後の全 {len(targets)} 問を `### C番号. 質問文` の見出しで、結論と要点だけ出力する。
"""
    accept = dict(_ACCEPT_BASE, minChars=max(600, 200 * len(targets)))
    result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})

    by_qid = {a.qid: a for a in targets}
    tuned: dict[str, CoreAnswer] = {}
    for qid, _keyword, question, conclusion, points in _parse(result):
        base = by_qid.get(qid or "")
        if base is None or not conclusion:
            continue
        tuned[base.qid] = CoreAnswer(
            qid=base.qid, category=base.category, question=base.question,
            conclusion=conclusion, points=points[:3], evidence=base.evidence,
            jd_dependent=True,
        )
    return tuned


def jd_specific(context: str, avoid_questions: list[str] | None = None,
                used_evidence: list[str] | None = None, count: int = 12) -> list[QA]:
    """この JD でしか出ない問を生成する。定番と重ならない範囲に限定する。"""

    prompt = f"""\
日本企業の採用面接官として、この求人でこそ突かれる想定問答を {count} 問作ってください。

問の選び方:
- JD の仕事内容・必須要件・優遇要件から、面接官が必ず確認する点を選ぶ。
- 応募者が未経験・部分一致の領域を必ず 2 問以上入れ、ごまかさず正直に答える。
- この会社の事業構造・利用者・組織体制に固有の問を 2 問以上入れる。どの会社でも
  成立する一般論の問（優先順位の付け方、合意形成の仕方など）は作らない。
- 経歴・強み弱み・転職理由・年収・キャリアビジョンなどの定番質問は別途用意済みなので作らない。
- **文脈の「求める人物像」に挙げられている特性は、条件を満たすかとは別に
  「この人物像に見えるか」を確かめる軸。特性ごとに少なくとも 1 問で、その特性を
  裏付ける具体的な実績（プロジェクト名・数字）を回答の要点に明示すること。
  抽象的な自己PR（「主体性があります」等）で済ませない。**
- **文脈の「JD・会社事実に登場する製品」に挙げられている製品ごとに、最低 1 問を
  「この製品にどう貢献できますか／入社後まず何をしますか」という趣旨で作る。
  回答は必ず次の 3 点を明示する: ①製品の説明（JD・会社事実に書かれている範囲の
  みで語る。それ以外の知識で機能・仕様を断定しない）②応募者とこの製品の関係
  （プロフィールの実績のうち隣接する経験。無理に関連付けず、薄ければ「直接の
  経験はないが〜」と正直に書く）③貢献できること・次にすること（入社後にこの
  製品へどう関わり、何を担当し、何から着手するか。意気込みではなく実績から
  導ける具体的な行動）。**

{STYLE_RULES}
{KEYWORD_RULE}
{context}

{_avoid_block(avoid_questions or [], used_evidence or [])}

# 出力
{count} 問を `### Q. [キーワード] 質問文` の見出しで、結論と要点だけ出力する。
"""
    accept = dict(_ACCEPT_BASE, minChars=1800)
    result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    return [_qa(k, q, c, p, "jd") for _, k, q, c, p in _parse(result)]


def drilldown(context: str, targets: list[QA | CoreAnswer], per: int = 2) -> list[QA]:
    """突かれやすい問への追問と、その答えを 1 回の呼び出しで作る（1 層目）。"""

    if not targets:
        return []
    prompt = f"""\
以下の回答に対し、面接官が実際に返してくる追問と、その答えを作ってください。

追問の作り方:
- 1 つの回答につき {per} 問。回答の中で最も検証されやすい部分（数字の根拠、
  再現性、自分の担当範囲、うまくいかなかった場合）を突く。
- 「なぜそう判断したのですか」だけで終わらせず、答えに書かれた具体的な語を
  引用して突く。元の回答を読まなければ作れない問にする。
- 追問の答えも同じ形式で書く。素材にない事実は足さない。

{STYLE_RULES}
{KEYWORD_RULE}
{context}

# 元の回答
{_listing(targets)}

# 出力
追問を `### Q. [キーワード] 質問文` の見出しで、結論と要点だけ出力する。元の回答は再掲しない。
"""
    accept = dict(_ACCEPT_BASE, minChars=1200)
    result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    return [_qa(k, q, c, p, "drilldown") for _, k, q, c, p in _parse(result)]


def drilldown_second(context: str, first: list[QA], picks: int = 4) -> list[QA]:
    """1 層目の答えを、さらにもう 1 段突く（2 層目）。

    面接が崩れるのは 1 段目ではなく 2 段目 — 「その数字は自分の担当範囲ですか」
    「同じ状況で失敗したことは」まで用意しておく。
    """

    if not first:
        return []
    targets = first[:picks]
    prompt = f"""\
以下は「追問とその答え」です。面接官はここでもう一段踏み込みます。
その 2 段目の追問と答えを、対象 {len(targets)} 問それぞれに 1 問ずつ作ってください。

2 段目の作り方:
- 1 段目の答えの中で、まだ検証されていない前提を突く。例えば「その数字のうち
  自分が直接動かした範囲はどこか」「同じやり方が通用しなかった場面はあるか」
  「相手が納得しなかったらどうしたか」。
- 答えは逃げずに具体的に書く。分からないことは分からないと答えたうえで、
  どう埋めるかを述べる。素材にない事実は足さない。

{STYLE_RULES}
{KEYWORD_RULE}
{context}

# 1 段目の追問と答え
{_listing(targets)}

# 出力
2 段目の追問を `### Q. [キーワード] 質問文` の見出しで、結論と要点だけ出力する。1 段目は再掲しない。
"""
    accept = dict(_ACCEPT_BASE, minChars=600)
    result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    return [_qa(k, q, c, p, "drilldown") for _, k, q, c, p in _parse(result)]


def repair_batch(context: str, problems: list[tuple[str, str, list[str], str]],
                 used_evidence: list[str] | None = None) -> dict[str, QA]:
    """閘門に落ちた問をまとめて 1 回の呼び出しで作り直す。

    problems: [(ラベル, 質問文, 現在の要点, 問題の説明), ...]
    戻り値: {質問文: 作り直した QA} — 返ってこなかった問は呼び出し側が元のまま残す。
    """

    if not problems:
        return {}
    listing = "\n\n".join(
        f"### Q. {question}\n"
        + "\n".join(f"{i}. {p}" for i, p in enumerate(points, start=1))
        + f"\n<!-- 検査結果: {problem} -->"
        for _, question, points, problem in problems
    )
    prompt = f"""\
以下の想定回答が検査に落ちました。各問の検査結果を直して書き直してください。

直し方:
- 質問文は一字一句変えない。見出しはそのまま `### Q. 質問文` で返す。
- 素材に無い数字を指摘された問は、その数字を落とすか、素材にある数字へ置き換える。
- 実績の使い回しを指摘された問は、素材の中からまだ使っていない場面へ差し替える。
- 年数の食い違いを指摘された問は、素材にある値へそろえる。
- 抽象論だけと指摘された問は、実際にあった一場面へ書き直す。

{STYLE_RULES}

{context}

{_avoid_block([], used_evidence or [])}

# 作り直す対象（全 {len(problems)} 問）
{listing}

# 出力
全 {len(problems)} 問を `### Q. 質問文` の見出しで、結論と要点だけ出力する。
"""
    accept = dict(_ACCEPT_BASE, minChars=max(200, 120 * len(problems)))
    try:
        result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    except Exception:
        return {}
    fixed: dict[str, QA] = {}
    for _, keyword, question, conclusion, points in _parse(result):
        # キーワードは呼び出し側が元の問のものを引き継ぐ（修復対象は中身だけ）
        fixed[question] = QA(question, conclusion, points[:3], "repair", keyword)
    return fixed


def refine_batch(context: str, targets: list[tuple[str, str, str, list[str], str, str]],
                 focus: list[str], used_evidence: list[str] | None = None,
                 ) -> dict[str, QA]:
    """批評（critic）で弱いと指摘された問を、1 回の呼び出しでまとめて書き直す。

    `repair_batch` との違いは指示の出所。あちらは機械閘門（事実・形式）の違反を
    直す。こちらは面接官の目での物足りなさを直す — 直し方は批評が問ごとに
    言語化済みなので、そのまま渡して従わせる。**素材に無い実績は足させない。**

    targets: [(ラベル, 質問文, 現在の結論, 現在の要点, 指摘, 直し方), ...]
    focus:   最も低かった評価軸（2 つまで）。全軸を一度に直させない。
    戻り値: {質問文: 書き直した QA} — 返ってこなかった問は呼び出し側が元のまま残す。
    """

    if not targets:
        return {}
    listing = "\n\n".join(
        f"### Q. {question}\n{conclusion}\n"
        + "\n".join(f"{i}. {p}" for i, p in enumerate(points, start=1))
        + f"\n<!-- 面接官の指摘: {problem} / 直し方: {fix} -->"
        for _, question, conclusion, points, problem, fix in targets
    )
    focus_line = "・".join(focus) if focus else "指摘に書かれた点"
    prompt = f"""\
以下の想定回答を、面接官から受けた指摘のとおりに書き直してください。

書き直し方:
- 各問の `面接官の指摘` と `直し方` に従う。**直し方に書かれていないことはしない。**
- 今回とくに弱いと判定された観点は「{focus_line}」。ここが上がる方向へ直す。
- 質問文は一字一句変えない。見出しはそのまま `### Q. 質問文` で返す。
- **素材に無い実績・数字を作らない。** 足りないと感じても作らず、素材にある
  別の場面へ差し替えるか、既にある場面の判断の理由を掘り下げる。
- 「もっと具体的に」を字面で受けて形容詞を増やさない。場面・判断・結果を書く。

{STYLE_RULES}

{context}

{_avoid_block([], used_evidence or [])}

# 書き直す対象（全 {len(targets)} 問）
{listing}

# 出力
全 {len(targets)} 問を `### Q. 質問文` の見出しで、結論と要点だけ出力する。
"""
    accept = dict(_ACCEPT_BASE, minChars=max(200, 120 * len(targets)))
    try:
        result = miko_llm.text(prompt, timeout=600, opts={"accept": accept})
    except Exception:
        return {}
    refined: dict[str, QA] = {}
    for _, keyword, question, conclusion, points in _parse(result):
        refined[question] = QA(question, conclusion, points[:3], "refine", keyword)
    return refined
