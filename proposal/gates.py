"""機械閘門 — LLM を呼ばずに「面接に持ち込めるか」を判定する。

判定できるものだけを判定する。提案の筋の良さは機械では測れない — それは
`redteam` stage（LLM に採用側の目で潰させる）と人間の仕事で、ここでは扱わない。

  A 構造     必須見出し / 分量 / 表 / カード各項目の埋まり
  B 数字錨定  本文の数字が素材（プロフィール・既存回答・JD・会社事実）に実在するか
  C 事実仮説  会社の内部事情を断定していないか（仮説マークの有無）
  D 能力覆蓋  人物像で挙げた能力すべてにカードがあるか
  E 安全     PII 残存・取引先ブランド名残存
  F 事実引用  `[事実]` と書いた行の引用が、実際に取得した会社原文に存在するか
  G 実績紐付  経験マッピング表の各行に本人の実例が入っているか
  H 自己採点  紅隊レビューが 7 軸の点数表を出しているか（点は refine の判断材料）
  I 事例重複  同じ事例を複数のカードで使い回していないか（**警告のみ・落とさない**）

C は**近似**である。「社内の実態を知らないのに知った風に書く」型の事故を、
仮説マークの数と会社名を含む断定文の検出で捉えるだけ。個々の記述が事実かどうかは
判定していない（監査レポートにもそう書く。網羅を装わない）。

F は C を**実測に置き換える**部分。`research.py` が実際に取ってきた原文だけを
照合先にするので、「[事実] と書いてあるのに出所が無い」を機械で落とせる。研究層が
空振りした求人では `[事実]` タグ自体が使えなくなる（全部仮説で書くしかない）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from interview.qa_quality import unsupported_numbers
from tools.pii_gate import scrub_for_external
from tools.redact import scan as redact_scan

from .anchor import QUOTE_WINDOW, anchored, norm as anchor_norm

# 仮説であることを示すマーク（C 用）
_HYPOTHESIS_MARKS = ("仮説", "であれば", "だとすれば", "と推測", "かもしれ",
                     "要確認", "{{要確認}}", "確認したい", "前提が正しければ")
# 社内実態を断定する語尾（C 用）— 会社名と同じ行にあると危ない
_ASSERT_PAT = re.compile(
    r"(しています|されています|しており|されており|になっています|があります|"
    r"を採用しています|を使っています|が課題です)")
# 数字錨定の対象外にする定型（章番号・順位・ステップ番号）
_STRUCTURAL_NUM = re.compile(
    r"^\s*(#{1,4}\s*)?\d+[\.．、]|^\s*\|\s*\d+\s*\||^_生成日")
# 同じく対象外にする期間表現。prompt が「最初の 90 日を 3 期に分ける」と指示している
# 以上、「31〜60 日目」は実績の捏造ではなく構造。ここを見ないと 90 日計画を書くたびに
# 是正再生成が 1 回無駄に走る（実測で踏んだ）
_PERIOD_RE = re.compile(
    r"\d{1,3}\s*[〜~\-–ー]\s*\d{1,3}\s*(日|週|ヶ月|か月|カ月)目?|"
    r"\d{1,3}\s*(日目|週目|ヶ月目|か月目)|第\s*\d{1,2}\s*期|Day\s*\d{1,3}")
# 日付も実績数値ではない。deck の表紙に生成日を入れられて 2 回無駄に再生成した
_DATE_RE = re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]?\s*\d{0,2}\s*日?")

# 漢数字の実績表現。Gate B に一度弾かれた後、再生成が数字を漢数字へ書き換えて
# 閘門を素通りした（実測: 「三万五千口」「二百五十件」）。数量として意味を持つ
# 助数詞が続くものだけを算用数字へ直してから検査する — 「一貫」「十分」のような
# 語まで数値化すると、逆に存在しない数字を捏造扱いしてしまう
_KANJI_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_SMALL = {"十": 10, "百": 100, "千": 1000}
_KANJI_BIG = {"万": 10 ** 4, "億": 10 ** 8}
_COUNTERS = "口件人社店舗個台名年回倍割％%"
_KANJI_NUM_RE = re.compile(
    r"[〇零一二三四五六七八九十百千万億]{2,}(?=[" + _COUNTERS + r"])")


# 「3万5,000口」のような算用数字と単位漢字の混在形。素材側（JD）はこの書き方、
# 生成側は漢数字で書くことがあり、片方だけ正規化すると素材にある数字を
# 「捏造」と誤判定する（実測で踏んだ）。両側に同じ変換をかける
_MIXED_MAN_RE = re.compile(r"(\d[\d,]*)\s*万\s*(\d[\d,]*)?")
_MIXED_OKU_RE = re.compile(r"(\d[\d,]*)\s*億\s*(\d[\d,]*)?")

# 企業サイトは字間演出で数字を「2 . 1 兆円」「5 0 万人」と分かち書きすることがある。
# 素材側だけがこの形だと、正しく引用した「2.1兆円」を捏造扱いしてしまう（実測）。
# 生成側・素材側の両方に同じ変換をかけるので、無い数字を作り出すことはない
_SPACED_DIGIT_RE = re.compile(r"(?<=\d)[ 　]+(?=\d)")
_SPACED_POINT_RE = re.compile(r"(?<=\d)[ 　]*([.,．，])[ 　]*(?=\d)")


def _join_spaced_digits(text: str) -> str:
    return _SPACED_DIGIT_RE.sub("", _SPACED_POINT_RE.sub(r"\1", text))


def _mixed_to_arabic(text: str) -> str:
    def make(scale: int):
        def conv(m: re.Match) -> str:
            head = int(m.group(1).replace(",", ""))
            tail = int((m.group(2) or "0").replace(",", ""))
            return str(head * scale + tail)
        return conv
    text = _MIXED_OKU_RE.sub(make(10 ** 8), text)
    return _MIXED_MAN_RE.sub(make(10 ** 4), text)


def _kanji_to_arabic(text: str) -> str:
    """漢数字の数量表現を算用数字へ寄せる（Gate B の前処理）。"""
    def conv(m: re.Match) -> str:
        total = section = digit = 0
        for ch in m.group():
            if ch in _KANJI_DIGIT:
                digit = _KANJI_DIGIT[ch]
            elif ch in _KANJI_SMALL:
                section += (digit or 1) * _KANJI_SMALL[ch]
                digit = 0
            elif ch in _KANJI_BIG:
                total += (section + digit or 1) * _KANJI_BIG[ch]
                section = digit = 0
        return str(total + section + digit)
    return _KANJI_NUM_RE.sub(conv, text)

MAX_UNSUPPORTED = 0          # 素材に無い数字は 1 つも許さない
MIN_HYPOTHESIS_MARKS = 3     # 会社事実が無い提案で仮説マークがこれ未満 = 知ったかぶり
MAX_FEEDBACK_PER_GATE = 5    # prompt 肥大化防止

# ---- Gate F（事実引用）
_FACT_TAG = re.compile(r"\[事実\]|【事実】")
_QUOTE_RE = re.compile(r"[「『]([^」』]{4,})[」』]")
MIN_QUOTE_ANCHOR = 0.5       # 取得原文に対する部分一致率がこれ未満 = 出所不明
# 正規化後にこれ以上あれば「完全一致」で判定してよい。日本語の事実引用は
# 「2.1兆円突破」のように短く、記号を落とすと窓幅 12 に届かない。窓幅方式のまま
# だと 0.0 になり、原文にある引用を捏造扱いした（実測で踏んだ）。
# 4 字は「生成AI」「NISA」のような固有名詞が通る下限。これ未満（「AI」「兆円」）は
# 識別力が無いので引用として認めない
MIN_SHORT_QUOTE = 4

# ---- Gate G（実績紐付）
# 「該当なし」を正直に書くのは許す。誤魔化しの空欄・一般論だけを落とす
_NO_EXPERIENCE = re.compile(r"(直接の実績なし|該当なし|実績なし|近いのは|経験なし)")
_VAGUE_EXPERIENCE = re.compile(
    r"^(多数|various|いろいろ|さまざま|様々|複数|多くの|豊富な)[^。]{0,12}$")

# ---- Gate H（自己採点）
SCORE_AXES = ("事業理解", "課題定義", "根拠", "解決策", "指標", "実行可能性", "リスク認識")
_SCORE_ROW = re.compile(r"\|\s*([^|]+?)\s*\|\s*(\d{1,2})\s*/\s*10\s*\|")
REFINE_THRESHOLD = 45        # 70 点満点。これ未満なら主提案を書き直す価値がある


# ---- Gate I（事例の使い回し・警告のみ）
# 正規化後にこれだけ連続一致したら「同じ場面」の候補とみなす。実データ
# （4407 playbook v6.2・8 枚すべて別場面）と使い回しの再現ケースの両方へ
# 総当たりして決めた値 — 12〜14 は prompt が指定した雛形の断片
# （「い状況を想定しますPdM」等 13 字前後）を拾って誤報し、**15 以上で
# 誤報 0・検出 1 が両立**する。16 は margin を 1 字取ったもの。
# 固有名（「マルチテナント型チケット予約SaaS」= 18 字）はこの上でも拾える。
# ⚠ 校正に使った実データは 1 パックだけ。別領域の求人で誤報が出たら
# `12_review_report.md` §2b を見て見直すこと（警告なので実害は無い）
DUP_NGRAM = 16


def _boilerplate_df(n_blocks: int) -> int:
    """これ以上のブロックに出る語句は「定型」とみなして重複判定から外す。

    prompt が書き方の雛形を指定していると、その雛形は全ブロックに出る。頻度で
    切らないと**自分が指定した雛形**を使い回しの証拠として数える（実測 v6.2:
    「状況を想定しますPdMは」で 5 件の誤報）。一方、本当の使い回しは 2〜3
    ブロックに留まる。過半数を超えたら定型、が判定の骨。

    下限 3 が要る — ブロックが 2 つしか無いとき「過半数」= 2 になり、唯一の
    使い回しペアが定型に分類されてしまう。
    """
    return max(3, n_blocks // 2 + 1)


def _grams_outside(norm_text: str, common: set[str]) -> set[str]:
    """`common` の語句が覆う文字位置を除いた上で {DUP_NGRAM} 字の窓を集める。

    n-gram 単位で高頻度語を捨てるだけでは足りない — 同じ雛形が 1 文字ずれた窓
    （「ない状況を想定しますPd」「れる状況を想定しますPd」…）へ散らばると個々の
    df が下がり、雛形の断片が「重複の証拠」として生き残る（実測 v6.2）。覆われた
    **文字**を落とせば、ずれた窓もまとめて消える。
    """
    if not norm_text:
        return set()
    masked = [True] * len(norm_text)      # True = 比較に使ってよい文字
    for i in range(len(norm_text) - DUP_NGRAM + 1):
        if norm_text[i:i + DUP_NGRAM] in common:
            for j in range(i, i + DUP_NGRAM):
                masked[j] = False
    out: set[str] = set()
    for i in range(len(norm_text) - DUP_NGRAM + 1):
        if all(masked[i:i + DUP_NGRAM]):
            out.add(norm_text[i:i + DUP_NGRAM])
    return out


@dataclass
class GateSpec:
    required_sections: list[str] = field(default_factory=list)
    min_chars: int = 1000
    # 0 = 上限なし。話す原稿（pitch）のように「長いこと自体が不合格」の
    # stage だけ入れる。分量が仕様の一部なら、下限と同じく機械で測る
    max_chars: int = 0
    min_lines: int = 25
    require_table: bool = False
    block_marker: str = ""
    block_required: list[list[str]] = field(default_factory=list)
    require_hypothesis_marks: bool = False
    required_markers: list[str] = field(default_factory=list)  # 本文に必須の文字列
    check_capability_coverage: bool = False
    check_numbers: bool = True
    check_fact_quotes: bool = False       # Gate F
    mapping_evidence_col: int = 0         # Gate G: 1-indexed。0 = 検査しない
    require_score_table: bool = False      # Gate H
    warn_duplicate_field: str = ""        # Gate I: 使い回しを見る行の見出し語


@dataclass
class GateResult:
    ok: bool
    errors: list[str]
    warnings: list[str]

    def as_feedback(self, previous: str = "") -> str:
        """是正指示。見出しまでここが持つ — 呼び出し側で見出しを付けると、
        採点駆動の書き直し（閘門は通っている）にも「閘門で不合格」と書いてしまう。

        `previous` に前回の本文を渡すと**差分修正**の指示になる。渡さないと
        LLM は「どこが悪かったか」だけを見て白紙から書き直すので、通っていた
        節まで別物になる（是正 1 回目で Gate A は直ったが Gate B が新しく
        落ちる、という往復が起きる）。前回本文を持たせて「直す箇所以外は
        そのまま再掲」と指示する方が、試行あたりの改善が単調になる。
        """
        if not self.errors:
            return ""
        head = ("# ★前回の出力が機械閘門で不合格だった。以下を直して全文を書き直す★\n"
                + "\n".join(f"- {e}" for e in self.errors))
        if not previous.strip():
            return head + "\n（指摘されていない部分の質は落とさないこと）"
        return (head + "\n\n## 直し方\n"
                "下の前回本文を土台にする。**指摘された箇所だけを直し、それ以外の"
                "段落・表・図は一字一句そのまま再掲する**（書き直すと通っていた"
                "箇所が壊れる）。出力は前回同様、本文全文のみ。差分や説明は書かない。\n\n"
                "<前回の本文>\n" + previous.strip() + "\n</前回の本文>")


def self_check(spec: GateSpec, *, has_research: bool = True) -> str:
    """GateSpec を生成側の自己点検リストへ翻訳する。

    閘門の条件を prompt に手で書き写すと、spec を変えたときに片方だけ古くなる
    （実測: `min_lines` を緩めても prompt には旧値が残っていた）。同じ
    dataclass から両方を出せば、生成側と検査側が原理的にずれない。

    ここに書くのは**機械が実際に測るものだけ**。「良い提案か」のような
    測れない項目を混ぜると、通過に効かない指示で prompt が薄まる。
    """
    items: list[str] = []
    if spec.required_sections:
        items.append(f"見出し {len(spec.required_sections)} 個を、指定の文字列の"
                     f"まま・指定の順で書いたか（1 字でも違うと差し戻し）")
    if spec.required_markers:
        items.append("必須の記述を指定の書式のまま入れたか: "
                     + " / ".join(f"「{m}」" for m in spec.required_markers))
    if spec.block_marker and spec.block_required:
        keys = "・".join(g[0] for g in spec.block_required)
        items.append(f"`{spec.block_marker}` の各ブロックに {keys} の行が"
                     "すべて揃っているか（1 ブロックでも欠けると差し戻し）")
    if spec.max_chars:
        items.append(f"本文は {spec.min_chars}〜{spec.max_chars} 字に収まっているか"
                     "（**上限を超えたら内容を捨てる**。薄く縮めない）")
    else:
        items.append(f"本文は {spec.min_chars} 字・{spec.min_lines} 行以上あるか")
    if spec.require_table:
        items.append("指定の列で Markdown 表を書いたか")
    if spec.check_numbers:
        items.append("**素材に無い数字を 1 つも書いていないか**（素材＝プロフィール・"
                     "既存回答・JD・会社事実・取得原文。目安の数字や概算を"
                     "作らない。書けないときは数字を使わず定性的に述べる）")
    if spec.require_hypothesis_marks:
        items.append("社内の実態に触れた箇所を「仮説：」「〜であれば」で "
                     f"{MIN_HYPOTHESIS_MARKS} 箇所以上明示したか")
    if spec.check_fact_quotes:
        items.append("`[事実]` を付けた行すべてに、取得原文か JD からの"
                     "**原文どおりの**「」引用があるか（要約・言い換えは"
                     "照合に失敗する）"
                     + ("" if has_research else
                        " — 今回は原文が 1 ページも取れていないので `[事実]` は使えない"))
    if spec.check_capability_coverage:
        items.append("指定された能力の一覧すべてに、見出し付きのカードが 1 枚ずつあるか")
    if spec.mapping_evidence_col:
        items.append("表の実例欄に空欄・「多数」「豊富な経験」等の定型句が無いか"
                     "（該当が無い行は「直接の実績なし。近いのは〜」と書く）")
    if spec.require_score_table:
        items.append(f"採点表に {len(SCORE_AXES)} 軸すべてを `n/10` の書式で書いたか")
    items.append("氏名・連絡先・取引先ブランド名を書いていないか")
    return ("# 提出前の自己点検（同じ項目を機械が検査します。1 つでも欠けると差し戻し）\n\n"
            + "\n".join(f"- [ ] {x}" for x in items))


def _spoken_body(text: str) -> str:
    """読み上げる部分だけを返す（最初の `## ` 見出しより前を捨てる）。

    `max_chars` は「時間内に話し切れるか」の判定なので、**声に出さない行を
    数えてはいけない** — タイトルと `_生成日: …_` の注記で 80 字取られると、
    本文を削って上限に合わせる羽目になる（実測 4752 で踏んだ）。
    `min_chars` は全文で測ったままにする（薄い出力を見逃す方向へは緩めない）。
    """
    m = re.search(r"^## ", text, re.MULTILINE)
    return text[m.start():] if m else text


def _gate_a(text: str, spec: GateSpec) -> list[str]:
    errs: list[str] = []
    for sec in spec.required_sections:
        if sec not in text:
            errs.append(f"[Gate A] 必須見出しが無い: 「{sec}」をそのままの文字列で書くこと")
    if len(text) < spec.min_chars:
        errs.append(f"[Gate A] 内容が薄い: {len(text)} 字 < 必要 {spec.min_chars} 字")
    if spec.max_chars and len(_spoken_body(text)) > spec.max_chars:
        # 話す原稿では長さが仕様。縮め方まで指示しないと、LLM は内容を残したまま
        # 全体を圧縮して「読めはするが何も言っていない原稿」を返す
        errs.append(f"[Gate A] 長すぎて時間内に話せない: {len(text)} 字 > 上限 "
                    f"{spec.max_chars} 字。**節を薄く縮めるのではなく、"
                    "打ち手か具体例を 1 つ丸ごと捨てて残りの密度を保つこと**")
    n_lines = len([l for l in text.splitlines() if l.strip()])
    if n_lines < spec.min_lines:
        errs.append(f"[Gate A] 行数不足: {n_lines} 行 < 必要 {spec.min_lines} 行")
    if spec.require_table and "|" not in text:
        errs.append("[Gate A] 表が無い: 指定の列で Markdown 表を書くこと")
    for marker in spec.required_markers:
        if marker not in text:
            errs.append(f"[Gate A] 必須の記述が無い: 「{marker}」を含む行を書くこと"
                        "（prompt の指定形式のまま）")
    if spec.block_marker and spec.block_required:
        blocks = re.split(rf"^{re.escape(spec.block_marker)}\s+", text, flags=re.M)[1:]
        if not blocks:
            errs.append(f"[Gate A] 「{spec.block_marker}」レベルの見出しが 1 つも無い")
        for blk in blocks:
            title = blk.splitlines()[0].strip()[:30] if blk.strip() else "(無題)"
            for group in spec.block_required:
                if not any(kw in blk for kw in group):
                    errs.append(f"[Gate A] 「{title}」に {group[0]} の行が無い")
    return errs[:MAX_FEEDBACK_PER_GATE * 2]


def _gate_b(text: str, spec: GateSpec, corpus: str) -> list[str]:
    """本文の数字が素材に実在するか。章番号など構造的な数字は除外する。"""
    if not spec.check_numbers or not corpus:
        return []
    body = "\n".join(l for l in text.splitlines() if not _STRUCTURAL_NUM.match(l))
    body = _PERIOD_RE.sub("〈期間〉", body)
    body = _DATE_RE.sub("〈日付〉", body)
    norm = lambda t: _mixed_to_arabic(_kanji_to_arabic(_join_spaced_digits(t)))
    suspects = unsupported_numbers(norm(body), norm(corpus))
    if len(suspects) <= MAX_UNSUPPORTED:
        return []
    return [
        f"[Gate B] 素材に無い数字を書いている: {suspects[:8]}。"
        "プロフィール・既存回答・JD・会社事実にある数字だけを使い、"
        "それ以外は数字を書かずに定性的に述べること"
    ]


def _gate_c(text: str, spec: GateSpec, job: dict,
            has_facts: bool) -> tuple[list[str], list[str]]:
    """会社の内部事情を知った風に断定していないか（近似判定）。"""
    if not spec.require_hypothesis_marks:
        return [], []
    errs: list[str] = []
    warns: list[str] = []
    marks = sum(text.count(m) for m in _HYPOTHESIS_MARKS)
    if marks < MIN_HYPOTHESIS_MARKS:
        errs.append(
            f"[Gate C] 仮説であることの明示が {marks} 箇所しかない（最低 "
            f"{MIN_HYPOTHESIS_MARKS}）。社内の実態は知らない前提なので、"
            "断定できない箇所は「仮説：」か「〜であれば」で書くこと")

    company = (job.get("company") or "").strip()
    if company and not has_facts:
        short = re.sub(r"株式会社|有限会社|\s", "", company)
        for i, line in enumerate(text.splitlines(), 1):
            if short and short in line and _ASSERT_PAT.search(line):
                if not any(m in line for m in _HYPOTHESIS_MARKS):
                    warns.append(
                        f"[Gate C 参考] {i} 行目: 会社事実を渡していないのに社内の状態を"
                        "断定している可能性。仮説の書き方か確認すること")
    return errs, warns[:MAX_FEEDBACK_PER_GATE]


def _gate_d(text: str, spec: GateSpec, capabilities: list[str]) -> list[str]:
    """人物像で挙げた能力すべてにカードがあるか。"""
    if not spec.check_capability_coverage or not capabilities:
        return []
    headings = "\n".join(l for l in text.splitlines() if l.strip().startswith("###"))
    missing = []
    for cap in capabilities:
        key = re.sub(r"[\s（）()・/／]", "", cap)[:6]
        if key and key not in re.sub(r"[\s（）()・/／]", "", headings):
            missing.append(cap)
    if not missing:
        return []
    return [f"[Gate D] カードが無い能力: {missing[:6]}。一覧すべてに 1 枚ずつ書くこと"]


def _mask_code_blocks(text: str) -> list[str]:
    """コードブロックの中身を空行に置き換える（行番号は保つ）。"""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return out


def _quote_hit(quote: str, corpus_norm: str) -> float:
    """引用が取得原文にあるか。短い引用は窓幅方式が使えないので完全一致で見る。"""
    n = anchor_norm(quote)
    if len(n) < QUOTE_WINDOW:
        return 1.0 if len(n) >= MIN_SHORT_QUOTE and n in corpus_norm else 0.0
    return anchored(quote, corpus_norm)


def _gate_f(text: str, spec: GateSpec, research: str) -> list[str]:
    """`[事実]` と書いた行の引用が、実際に取得した会社原文にあるか。

    Gate C（仮説マークの数）は「知った風に書いていないか」の近似でしかない。
    こちらは研究層が取ってきた原文だけを照合先にするので、出所の有無を実測できる。
    研究が空振りした求人では `[事実]` タグそのものを禁止する。
    """
    if not spec.check_fact_quotes:
        return []
    # コードブロック（構造図）内は対象外。図では引用が次の行へ折り返すので、
    # 同一行に引用を求めると図を描くたびに落ちる（実測で踏んだ）。図の中身の
    # 出所は図の下の補足文で担保させる
    fact_lines = [(i, l) for i, l in enumerate(_mask_code_blocks(text), 1)
                  if _FACT_TAG.search(l)]
    if not fact_lines:
        return []
    if not research.strip():
        return [f"[Gate F] 出所にできる原文が無いのに `[事実]` タグを "
                f"{len(fact_lines)} 箇所に付けている。"
                "会社に関する記述はすべて `[仮説]` にすること"]
    corpus = anchor_norm(research)
    errs: list[str] = []
    for i, line in fact_lines:
        quotes = _QUOTE_RE.findall(line)
        if not quotes:
            errs.append(f"[Gate F] {i} 行目: `[事実]` なのに出典の引用が無い。"
                        "取得原文か JD から「」でそのまま引くか、`[仮説]` に落とすこと")
            continue
        if max(_quote_hit(q, corpus) for q in quotes) < MIN_QUOTE_ANCHOR:
            errs.append(f"[Gate F] {i} 行目: 引用「{quotes[0][:24]}…」が"
                        "取得原文にも JD にも見つからない（言い換えず原文どおりに引く／"
                        "本当に出所が無いなら `[仮説]` にする）")
    return errs[:MAX_FEEDBACK_PER_GATE]


def _split_row(line: str) -> list[str]:
    if not line.strip().startswith("|"):
        return []
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _gate_g(text: str, spec: GateSpec) -> list[str]:
    """経験マッピング表の各行に、本人の実例が実際に入っているか。

    「豊富な経験」「多数の案件」で埋めた行は面接で 1 問目に崩れる。空欄と
    定型の誤魔化しだけを落とす — 「直接の実績なし」と正直に書く行は通す。
    """
    col = spec.mapping_evidence_col
    if not col:
        return []
    errs: list[str] = []
    rows = 0
    for i, line in enumerate(text.splitlines(), 1):
        cells = _split_row(line)
        if len(cells) < col:
            continue
        if re.fullmatch(r"[-:\s|]+", line.strip()):        # 区切り行
            continue
        head = cells[0]
        if head in ("JD 要件", "JD要件", "要件", "#") or not head:
            continue
        rows += 1
        val = cells[col - 1]
        if not val or val in ("-", "—", "なし", "TBD"):
            errs.append(f"[Gate G] {i} 行目「{head[:20]}」: 本人の実例が空欄。"
                        "該当が無いなら「直接の実績なし。近いのは〜」と書くこと")
        elif _VAGUE_EXPERIENCE.match(val) and not _NO_EXPERIENCE.search(val):
            errs.append(f"[Gate G] {i} 行目「{head[:20]}」: 「{val[:16]}」は実例ではない。"
                        "どの案件で何をしたかを具体的に書くこと")
    if rows == 0:
        errs.append("[Gate G] マッピング表の行が 1 つも読めない。"
                    "指定の列構成で Markdown 表を書くこと")
    return errs[:MAX_FEEDBACK_PER_GATE]


def parse_scores(text: str) -> dict[str, int]:
    """紅隊の自己採点表を読む（`| 軸 | 7/10 |` 形式）。"""
    found: dict[str, int] = {}
    for name, val in _SCORE_ROW.findall(text or ""):
        key = re.sub(r"[\s*]", "", name)
        for axis in SCORE_AXES:
            if axis in key:
                found[axis] = min(10, int(val))
                break
    return found


def total_score(text: str) -> int | None:
    scores = parse_scores(text)
    return sum(scores.values()) if len(scores) == len(SCORE_AXES) else None


def _gate_h(text: str, spec: GateSpec) -> list[str]:
    if not spec.require_score_table:
        return []
    scores = parse_scores(text)
    missing = [a for a in SCORE_AXES if a not in scores]
    if missing:
        return [f"[Gate H] 採点表に無い軸: {missing}。"
                "7 軸すべてを | 軸 | n/10 | 根拠 | の表で採点すること"]
    return []


def _gate_e(text: str) -> list[str]:
    errs: list[str] = []
    _, pii_hits = scrub_for_external(text)
    if pii_hits:
        errs.append(f"[Gate E] PII 残存: {sorted(set(pii_hits))[:3]}")
    brand_hits = redact_scan(text)
    if brand_hits:
        errs.append(f"[Gate E] 取引先ブランド名残存: {sorted(set(brand_hits))[:3]}")
    return errs


def _gate_i(text: str, spec: GateSpec) -> list[str]:
    """同じ事例を複数のブロックで使い回していないか（**警告のみ・非ブロッキング**）。

    実測 4407（playbook v6）: 8 枚のカードが 5 事例の再利用で、予約 SaaS が
    3 枚・ポイント接続が 2 枚に出た。深掘りされると「結局その 1 件しか無い」と
    読まれる型で、prompt 側に禁止を書いて解消した（v6.1 は 8/8 が別事例）。

    **落とさず警告に留める**のは、プロフィールの案件数が能力数より少ない求人が
    あり得るから。その場合の正解は「同じ案件の違う局面を切り出す」ことで、
    機械には局面が違うかを判定できない。エラーにすると、正直に書いた出力を
    書き直させて悪化させる。人が `12_review_report.md` §2b で見る。

    判定は正規化後の {DUP_NGRAM} 字連続一致。ただし**多くのブロックに出る語句は
    数えない**（`_boilerplate_df`）。prompt 側が「〜という状況を想定します。PdM は
    〜」と書き方を指定している以上、その定型は全ブロックに出る — 素の n-gram 一致で
    見ると*自分が指定した雛形*を重複の証拠として数えてしまう（v6.2 実測: 8 枚すべて
    別場面なのに 5 件の誤報、共通語句はすべて「状況を想定しますPdMは」）。
    定型は df が高く、本当の使い回しは df が低い（2〜3 ブロック）ので、頻度で分けられる。
    停用語リストを持たずに済むぶん、prompt の言い回しを変えても腐らない。
    """
    if not spec.warn_duplicate_field or not spec.block_marker:
        return []
    blocks = re.split(rf"^{re.escape(spec.block_marker)}\s+", text, flags=re.M)[1:]
    norms: list[tuple[str, str]] = []
    for blk in blocks:
        title = blk.splitlines()[0].strip()[:24] if blk.strip() else "(無題)"
        line = next((l for l in blk.splitlines()
                     if spec.warn_duplicate_field in l), "")
        # 見出し語そのもの（`- **想定シナリオ**: `）は全ブロック共通の飾りで
        # 中身ではない。落とさないと項目名が「重複の証拠」として出てくる
        _, _, body = line.partition(spec.warn_duplicate_field)
        norms.append((title, anchor_norm(body or line)))
    grams = [(t, _grams_outside(n, set())) for t, n in norms]

    df: dict[str, int] = {}
    for _t, g in grams:
        for x in g:
            df[x] = df.get(x, 0) + 1
    cutoff = _boilerplate_df(len(grams))
    common = {x for x, c in df.items() if c >= cutoff}
    # 高頻語句は**文字位置ごと遮蔽**してから数え直す。df を n-gram 単位で見るだけ
    # では、同じ雛形が 1 文字ずれた窓（「ない状況を想定しますPd」「れる状況を想定
    # しますPd」…）に散らばって個々の df が下がり、素通りする（実測 v6.2 で残った）
    grams = [(t, _grams_outside(txt, common)) for t, txt in norms]

    warns: list[str] = []
    for i, (t1, g1) in enumerate(grams):
        for t2, g2 in grams[i + 1:]:
            shared = list(g1 & g2)
            if not shared:
                continue
            longest = max(shared, key=len)
            warns.append(
                f"[Gate I 参考] 「{t1}」と「{t2}」が同じ場面を使っている可能性"
                f"（共通: 「{longest}」）。同じ話の言い換えが並ぶと読む価値が消える。"
                "別の場面へ振り替えるか、同じ機能でも違う判断を切り出すこと")
    return warns[:MAX_FEEDBACK_PER_GATE]


def check(text: str, spec: GateSpec, job: dict, *, corpus: str = "",
          capabilities: list[str] | None = None,
          has_facts: bool = False, research: str = "") -> GateResult:
    errors = _gate_a(text, spec)
    errors += _gate_b(text, spec, corpus)
    c_err, c_warn = _gate_c(text, spec, job, has_facts)
    errors += c_err
    errors += _gate_d(text, spec, capabilities or [])
    errors += _gate_e(text)
    errors += _gate_f(text, spec, research)
    errors += _gate_g(text, spec)
    errors += _gate_h(text, spec)
    return GateResult(ok=not errors, errors=errors,
                      warnings=c_warn + _gate_i(text, spec))
