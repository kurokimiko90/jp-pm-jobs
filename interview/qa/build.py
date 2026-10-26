"""qa stage の入口 — prep.py はこの build() だけを呼ぶ。

流れ: 正典読込 → **求める人物像を LLM に言語化させる(1 call)**
      → **JD・会社事実の製品名を LLM に特定させる(1 call)** → JD 向け調整(1 call)
      → JD 特化生成(1 call, 定番と重なる問は捨てる) → 深掘り 2 層(2 call)
      → 機械閘門 → 落ちた問だけ一括修復(≤2 call)
      → **面接官の目での批評 → 是正の巡回**(≤11 call) → 1 枚へ描画。

検収は二段構え。順番に意味がある:

  1. 機械閘門（`gates`）… 事実・形式。零 LLM で確実に落とせるものを先に落とす
  2. 批評（`critic`）  … 「この答えで通るか」。問ごと・カテゴリごとに軸を変えて
                         採点する（`critic.axes_for`）。LLM にしか見えないが、
                         そのままは信じない（指摘には本文からの逐語引用を要求し
                         機械照合する）

高いほうの検収を先に回すと、事実違反を含んだままの答えを面接官役が褒めることが
ある。安いほうから順に潰す。合否は問ごと（1 問でも `critic.TARGET_SCORE` 未満
なら不合格）。書き直して**対象問の合計点が下がったら前の版へ戻す**
（`critic.round_delta`）— 迭代は良くなる方向にしか進めない。改善が
`critic.STALL_ROUNDS` 回続けて止まれば `critic.MAX_ROUNDS`（5）前でも打ち切る。

LLM 呼び出しは最大 19 回（人物像 1 + 製品特定 1 + 生成 4 + 機械修復 2 + 批評巡回 11）。問数に比例して
増えない。`build(..., critique=False)` で批評巡回だけを切れる（最大 6 回に戻る）。
90 点という合格線は全軸 9 点以上を要求する厳しい基準で、5 巡回しても届かない問が
残ることは普通にある（`STALL_ROUNDS` で早期に打ち切った場合は特に）。監査
（`05_qa_audit.md`）に隠さず出る。
"""

from __future__ import annotations

import json
import re
import sys
from functools import partial
from pathlib import Path

import yaml

from tools.deid import build_deid_profile
from tools.gap_facts import _items, _load, match_evidence
from tools.knowledge_sources import knowledge_block
from tools.redact import redact

from . import bank, critic, gates, generate, render
from .render import QA

QA_FILENAME = "01_interview_qa.md"
AUDIT_FILENAME = "05_qa_audit.md"
JD_MAX_CHARS = 6000
MAX_REPAIR_ROUNDS = 2
DRILLDOWN_PICKS = 6
DRILLDOWN_SECOND_PICKS = 4
# 質問文がこの割合以上重なる JD 特化問は、定番の言い換えとみなして捨てる
DUPLICATE_THRESHOLD = 0.55
# tune_core が結論文をここまで書き換えたら「別の問への答え」に化けたとみなし、
# 調整前へ戻す。実測 4441: C05 キャリアビジョンが強みの答えへ差し替わった（0.03）。
# 正しい調整（会社への接続を足しただけ）は 0.5 前後
TUNE_MIN_SIMILARITY = 0.15
# 生成 prompt の字数予算。全部載せると 5.7 万字になり、指揮中心の brain が
# 総当たりの末に全滅した（実測 2026-10-18 4441: tune_core が 2 回とも失敗。
# 10/16 に実践記録を足して以降、QA パックは 1 本も最後まで生成できていなかった）。
# 生成側は圧縮プロフィール＋実践記録 1 本 3,500 字＋官網 4,000 字に絞る。
# 数字の裏取り（`_material`）は全文で行うので、絞っても Gate A は緩まない。
RESEARCH_MAX_CHARS = 4000
KNOWLEDGE_MAX_CHARS = 3500
PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "candidate_profile.yaml"

# JD 本文からの要件抽出（Gate B 用）。日本の求人票は見出し語が数パターンに収まるが、
# 表記ゆれが激しい。行頭アンカー(?m)^ に固定した上で「必須」「歓迎」は括弧書き
# （必須（MUST）等）や単独見出し（【必須】）も見出しとして拾う（実測: 2,144 件中
# 旧パターン一致は 408 件のみ、【必須】単独/必須： 型が 697 件、必須（括弧型が
# 152 件 — 旧パターンは過半数の求人票で要件を拾えていなかった）
# 見出し行の末尾に付く空白・改行コードだけを吸収する（\s だと \n も食うため、
# 「見出し行\r\n・1つ目の箇条書き」の 1 行目のつもりが 2 行目まで丸ごと呑み込む
# バグを踏んだ — \r\n 混在の求人票で実測）。改行は絶対に含めない。
_LINE_WS = r"[ \t\r]*"
_REQ_HEADING_RE = re.compile(
    rf"(?m)^[ \t]*(?:\*\*)?(?:【{_LINE_WS})?"
    r"(?:求める(?:能力|経験|人物像|スキル|要件)[^\n*】]*|応募(?:資格|要件)|"
    rf"必須(?:条件|要件|スキル|経験)?(?:{_LINE_WS}[（(][^）)\n]{{0,12}}[）)])?|"
    rf"歓迎(?:条件|要件|スキル|経験)?(?:{_LINE_WS}[（(][^）)\n]{{0,12}}[）)])?)"
    rf"{_LINE_WS}】?(?:\*\*)?[^\n]*\n")
# 「求める人物像」専用の見出し（Gate F 用）。上の _REQ_HEADING_RE は最初に見つかった
# 見出し（多くは必須要件）で止まるため、人物像だけを別に狙って拾う
_PERSONA_HEADING_RE = re.compile(
    rf"(?m)^[ \t]*(?:\*\*)?(?:【{_LINE_WS})?求める人物像{_LINE_WS}】?(?:\*\*)?[^\n]*\n")
# 次の見出し（**見出し** か ## 見出し か 【見出し】）まで。日本の求人票は
# 【入社後のキャリア】のような角括弧見出しが多く、これを次見出しと認識しないと
# 人物像の後ろに続く無関係な段落（会社アピール文等）まで拾ってしまう（実測）
_NEXT_HEADING_RE = re.compile(r"\n\s*(?:\*\*[^\n*]+\*\*|##\s|【[^\n】]+】)")
_BULLET_SPLIT_RE = re.compile(r"[■●◆▪]|\n")
_LABEL_RE = re.compile(r"【[^】]*】|＜[^＞]*＞|<[^>]*>")
# ≪急成長ベンチャー≫ のような求人票の売り文句。要件ではないので数に入れない
_PITCH_RE = re.compile(r"[≪≫《》]")
MIN_REQUIREMENT_CHARS = 6
MAX_REQUIREMENT_CHARS = 80
MAX_JD_REQUIREMENTS = 8


def _job_block(job: dict, facts: str, persona_traits: list[str] | None = None,
               product_names: list[str] | None = None) -> str:
    """求人側の文脈だけ — 会社・JD・確認済みの会社事実。応募者の情報は入らない。

    `persona_traits` / `product_names` が None のときはそのセクション自体を省く —
    LLM に言語化・特定させる前段（`generate.persona_traits` /
    `generate.product_relationships` への入力）を作るときに使う。値（空リストも
    含む）が渡されたときだけセクションを出す。
    """

    jd = (job.get("raw_jd") or "")[:JD_MAX_CHARS]
    salary = ""
    if job.get("salary_min"):
        salary = f"年収レンジ: {job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    extra_sections = ""
    if persona_traits is not None:
        extra_sections += (
            "\n# 求める人物像（QA 生成では各特性に具体的実績を対応させること）\n"
            f"{_bullet_block(persona_traits, '会社固有の人物像への接続は無理に作らない')}\n"
        )
    if product_names is not None:
        extra_sections += (
            "\n# JD・会社事実に登場する製品（QA 生成では製品ごとに関係・貢献・"
            "次にすることを回答へ明示すること）\n"
            f"{_bullet_block(product_names, '製品名の明記なし — 無理に製品名を作らない')}\n"
        )
    return f"""\
# 対象求人
- 会社: {job.get('company') or '{{要確認}}'}
- 職位: {job.get('title') or ''}
- 勤務地: {job.get('location') or '?'} / {salary}
{extra_sections}
# JD 全文（抜粋）
{jd}

# 確認済みの会社事実
{facts or '（未提供 — 会社固有の数字は書かない）'}
"""


def _context(job: dict, facts: str, persona_traits: list[str] | None = None,
            product_names: list[str] | None = None) -> str:
    """生成・是正用の文脈。字数予算内に収める（`KNOWLEDGE_MAX_CHARS` の注記）。"""

    block = f"""\
{_job_block(job, facts, persona_traits, product_names)}
{match_evidence(job)}

# 応募者プロフィール（事実の出所はこの節と次の実践記録だけ）
{build_deid_profile(compact=True)}

{knowledge_block(max_chars_per_source=KNOWLEDGE_MAX_CHARS)}"""
    redacted, _ = redact(block)
    return redacted


def _review_context(job: dict, facts: str, persona_traits: list[str] | None = None,
                    product_names: list[str] | None = None) -> str:
    """批評に渡す文脈 — 求人側だけ。応募者プロフィールは渡さない。

    2 つ理由がある:

    - 批評は**書かれた答えを判定する層**であって、素材から新しい事実を持ち出す層
      ではない。プロフィール全文を見せると「ここに書いてあるこの実績を足せ」と
      言い始め、是正側（`refine_batch`）の仕事を侵す。批評が言えるのは
      「この答えは弱い」までにしておく。
    - プロフィールは約 27,000 字ある。批評 prompt には問答全文も載るので、
      渡すと prompt が倍になり、指揮中心が読み切る前に時間切れになる（実測）。
    """

    redacted, _ = redact(_job_block(job, facts, persona_traits, product_names))
    return redacted


def _material(context: str) -> str:
    """数字の裏取り素材 — JD・会社事実・プロフィールに加え、手書き原典も出所と認める。

    プロフィールと実践記録は**全文**を足す（生成側の文脈は字数予算で絞っているが、
    正典の手書き回答は全文プロフィール由来の数字を使うため、絞った版だけで照合すると
    正しい数字を捏造扱いする）。

    正典の本文は入れない。正典自身を出所にすると、正典が持ち込んだ誤った数字を
    永久に検出できなくなる（自己参照）。
    """

    handwritten = "\n".join(bank.load_sources().values())
    full, _ = redact(f"{build_deid_profile()}\n{knowledge_block()}")
    return "\n".join((context, full, handwritten))


def _heading_bullets(jd: str, heading: re.Pattern[str], limit: int) -> list[str]:
    """見出し直後から次の見出しまでを箇条書き単位に割って拾う。零 LLM 共通処理。"""

    match = heading.search(jd)
    if not match:
        return []
    body = jd[match.end():]
    end = _NEXT_HEADING_RE.search(body)
    if end:
        body = body[:end.start()]
    items: list[str] = []
    for chunk in _BULLET_SPLIT_RE.split(body):
        # 【必須】【歓迎】【求める人物像】は見出しであって要件ではない
        text = _LABEL_RE.sub("", chunk).strip(" 　　・-\r\n")
        if _PITCH_RE.search(text):
            continue
        if MIN_REQUIREMENT_CHARS <= len(text) <= MAX_REQUIREMENT_CHARS:
            items.append(text)
    return items[:limit]


def _jd_requirements(job: dict) -> list[str]:
    """JD 本文（日本語）から必須・歓迎要件を拾う。零 LLM。

    Gate B は「要件の語が回答に出てくるか」で覆蓋を測る。ところが
    `gap_analysis.requirements` は内部閲覧用に**中国語**で書かれているため
    （`config/app.yaml`）、日本語の回答とは語が一致せず、覆蓋が常に 0 付近へ
    倒れる（実測: 中国語要件 5 件に対して 0/5、英字の略語が偶然当たると 2/5）。
    JD 本文は日本語なので、そこから拾えたときはそちらを Gate B に渡す。
    """

    return _heading_bullets(job.get("raw_jd") or "", _REQ_HEADING_RE, MAX_JD_REQUIREMENTS)


def _requirements(job: dict) -> list[str]:
    """Gate B に渡す要件。日本語で拾えたら JD 本文を優先し、駄目なら gap を使う。"""

    return _jd_requirements(job) or _items(_load(job), "requirements")


def _persona_traits_fallback(job: dict) -> list[str]:
    """JD 本文の「求める人物像」見出しだけを狙って拾う。零 LLM。

    本命は `generate.persona_traits`（LLM に読ませる — 下の関数の説明を見ること）。
    こちらはその呼び出しが失敗した（miko-ws 不達等）ときだけ使うフォールバック。
    見出しが無い JD では空を返す（regex なので推測はしない）。
    """

    return _heading_bullets(job.get("raw_jd") or "", _PERSONA_HEADING_RE, MAX_JD_REQUIREMENTS)


def _bullet_block(items: list[str], empty_note: str) -> str:
    """生成 prompt に渡す箇条書きブロック（人物像・製品名で共用）。

    空リストのときは何も無いと分かるようにする。無理に埋めさせない。
    """

    if not items:
        return f"（未取得 — {empty_note}）"
    return "\n".join(f"- {item}" for item in items)


def _bigrams(text: str) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace())
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _drop_duplicates(items: list[QA], existing: list[str]) -> tuple[list[QA], list[str]]:
    """定番の言い換えになっている JD 特化問を捨てる。捨てた質問文も返す。"""

    kept: list[QA] = []
    dropped: list[str] = []
    seen = list(existing)
    for item in items:
        if any(_similarity(item.question, other) >= DUPLICATE_THRESHOLD for other in seen):
            dropped.append(item.question)
            continue
        kept.append(item)
        seen.append(item.question)
    return kept, dropped


def _used_evidence(core: list[bank.CoreAnswer], limit: int = 12) -> list[str]:
    """正典が既に使っている実績 — JD 特化側に「別の場面を選べ」と伝えるための材料。"""

    seen: list[str] = []
    for answer in core:
        for item in answer.evidence:
            if item and item not in seen:
                seen.append(item)
    return seen[:limit]


def _research_facts(job: dict, pack_dir: Path) -> str:
    """会社事実が無いとき、官網の原文を取りに行く（零 LLM・`proposal.research`）。

    事実なしで生成すると、答えは JD の再話になり、最悪は自分の案件の構成を
    御社のことのように語る（実測 4441）。人が `--facts` を用意する運用は
    実際には回らないので、ここで自動で取る。取れなければ空のまま進める。
    """

    try:
        from proposal import research
        from proposal.pipeline import research_block
        status, _res = research.run(job, pack_dir)
        block = research_block(pack_dir, max_chars=RESEARCH_MAX_CHARS)
    except Exception as exc:  # noqa: BLE001 — 取得失敗で pack 生成を止めない
        print(f"  ⚠ 官網の自動取得に失敗（会社事実なしで続行）: {exc}", file=sys.stderr)
        return ""
    if not block:
        return ""
    print(f"  [facts] 官網原文を自動取得（{status}・{len(block)} 字）")
    return f"# 公式サイトの原文（自動取得・ここに書かれていることだけが御社の事実）\n{block}"


def _project_names() -> list[str]:
    """個人開発の名前 — Gate J（証拠の出所）が数える。括弧内の別名も拾う。"""

    try:
        profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
        projects = (profile.get("proof_projects") or {}).get("projects") or []
    except (OSError, yaml.YAMLError, AttributeError):
        return []
    names: list[str] = []
    for project in projects:
        name = str((project or {}).get("name") or "")
        for part in re.split(r"[（()）]", name):
            part = part.strip()
            # 「個人 AI 自動化 OS」のような説明句は名前ではない
            if part and not re.search(r"\s", part) and len(part) >= 4:
                names.append(part)
    return names


def _keep_intent(base: list[bank.CoreAnswer],
                 tuned: dict[str, bank.CoreAnswer]) -> tuple[list[bank.CoreAnswer], list[str]]:
    """調整で結論文の主張が別物になった問は調整前へ戻す。戻した qid も返す。"""

    kept: list[bank.CoreAnswer] = []
    reverted: list[str] = []
    for answer in base:
        new = tuned.get(answer.qid)
        if new is None:
            kept.append(answer)
        elif _similarity(answer.conclusion, new.conclusion) < TUNE_MIN_SIMILARITY:
            kept.append(answer)
            reverted.append(answer.qid)
        else:
            kept.append(new)
    return kept, reverted


def build(job: dict, facts: str, pack_dir: Path, critique: bool = True) -> dict:
    core = bank.load_core()
    if not core:
        raise RuntimeError(
            "正典題庫が空です。先に `python3 -m tools.oneoff.build_qa_core` を実行してください"
        )
    if not facts:
        facts = _research_facts(job, pack_dir)

    # 求める人物像 — LLM に読ませる（1 call）。JD に見出しが無くても、募集背景・
    # 業務内容・企業文化の書きぶりから採りたい人物像を読み取れることが多く、
    # 正規表現の見出し検出よりずっと拾える範囲が広い（見出しが無い JD で
    # `_persona_traits_fallback` は必ず空を返す）。応募者情報はまだ渡さない
    # （`_review_context` と同じ理由 — 会社側だけで判断させる）。
    persona_traits = generate.persona_traits(_review_context(job, facts)) \
        or _persona_traits_fallback(job)
    # JD・会社事実に登場する製品名 — LLM に読ませる（1 call、求人側だけの文脈）。
    # 見つかった製品ごとに jd_specific() が「関係・貢献・次にすること」の 3 点を
    # 回答へ明示する（`_context` の製品セクション経由でプロンプトへ渡す）。
    # regex フォールバックは無い — 製品名の特定は見出しに頼れる性質の情報ではなく、
    # LLM が失敗したら無理に拾わず空のまま進める（該当質問が作られないだけで
    # パック生成自体は止めない）。
    product_names = generate.product_relationships(_review_context(job, facts, persona_traits))

    context = _context(job, facts, persona_traits, product_names)
    material = _material(context)
    job_material, _ = redact(_job_block(job, facts))
    check = partial(_check, material=material, requirements=_requirements(job),
                    persona_traits=persona_traits, product_names=product_names,
                    job_material=job_material, project_names=_project_names())

    tuned = generate.tune_core(context, [a for a in core if a.jd_dependent])
    core, reverted = _keep_intent(core, tuned)

    jd_items = generate.jd_specific(
        context,
        avoid_questions=[a.question for a in core],
        used_evidence=_used_evidence(core),
    )
    jd_items, duplicates = _drop_duplicates(jd_items, [a.question for a in core])

    picks: list = jd_items[:DRILLDOWN_PICKS // 2]
    picks += [a for a in core if a.qid in ("C07", "C10", "C11")]
    drill = generate.drilldown(context, picks)
    drill += generate.drilldown_second(context, drill[:DRILLDOWN_SECOND_PICKS],
                                       picks=DRILLDOWN_SECOND_PICKS)

    report = check(render.labelled(core, jd_items, drill))

    rounds = 0
    for _ in range(MAX_REPAIR_ROUNDS):
        if report.ok:
            break
        rounds += 1
        core, jd_items, drill = _repair_round(context, report, core, jd_items, drill)
        report = check(render.labelled(core, jd_items, drill))

    history: list[critic.Critique] = []
    adopted = 0
    if critique:
        (core, jd_items, drill), report, history, adopted = _critique_loop(
            context, _review_context(job, facts, persona_traits, product_names),
            check, core, jd_items, drill)

    company = job.get("company") or "{{要確認}}"
    text = render.render(company, core, jd_items, drill)
    out_path = pack_dir / QA_FILENAME
    if out_path.exists():
        # 既存の問答は面接前に読み込んでいる可能性がある。黙って捨てない。
        backup = pack_dir / f"{out_path.stem}.bak{out_path.suffix}"
        backup.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    out_path.write_text(text, encoding="utf-8")

    total = len(core) + len(jd_items) + len(drill)
    audit = gates.render_report(report, total)
    if not facts:
        audit += ("\n## 会社事実なしで生成した\n\n官網の自動取得も失敗した。御社の事業・製品に"
                  "触れる答えは JD の範囲でしか書けていない。`_facts.md` を置いて再生成する。\n")
    if reverted:
        audit += ("\n## JD 向け調整を取り消した定番問\n\n調整で結論文の主張が別物になった"
                  "（別の問への答えに化けた）ため、調整前の答えを残した。\n\n"
                  + "\n".join(f"- {qid}" for qid in reverted) + "\n")
    if duplicates:
        audit += "\n## 定番と重複したため捨てた JD 特化問\n\n"
        audit += "\n".join(f"- {question}" for question in duplicates) + "\n"
    audit += "\n" + critic.render_report(history, adopted or None)
    (pack_dir / AUDIT_FILENAME).write_text(audit, encoding="utf-8")

    return {
        "questions": total,
        "core": len(core),
        "jd": len(jd_items),
        "drilldown": len(drill),
        "dropped": len(duplicates),
        "tune_reverted": len(reverted),
        "facts": len(facts),
        "repair_rounds": rounds,
        "blocking": len(report.blocking),
        "overused": len(report.overused),
        "coverage": f"{report.covered_requirements}/{report.total_requirements}",
        "persona_coverage": f"{report.covered_persona}/{report.total_persona}",
        "product_coverage": f"{report.covered_products}/{report.total_products}",
        "critique_rounds": len(history),
        "score": history[adopted - 1].total if adopted else None,
        "remaining": len(history[adopted - 1].findings) if adopted else 0,
    }


def _problems(report: gates.GateReport) -> dict[str, str]:
    """ラベル → その問を作り直す理由。同じ問に複数該当したらまとめて渡す。"""

    problems: dict[str, list[str]] = {}
    for label, numbers in report.unsupported.items():
        problems.setdefault(label, []).append(
            f"素材に無い数字を使っている: {' / '.join(numbers)}")
    for label in report.thin_points:
        problems.setdefault(label, []).append(
            "具体的な実績・数字が無く、抽象論だけになっている")
    for label in report.year_offenders:
        problems.setdefault(label, []).append(
            "経験年数が他の問と食い違っている（少数派の値を使っている）")
    for label in report.overused_offenders:
        problems.setdefault(label, []).append(
            "他の問と同じ実績・数字の使い回しだけで出来ている。別の場面へ差し替える")
    for label, terms in report.written_style.items():
        problems.setdefault(label, []).append(
            f"口に出すと浮く書面語が残っている: {' / '.join(terms)}")
    for label, terms in report.self_undermining.items():
        problems.setdefault(label, []).append(
            f"自己減点（資料の話・冒頭の不足宣言）: {' / '.join(terms)}")
    for label, numbers in report.company_claims.items():
        problems.setdefault(label, []).append(
            f"求人側の素材に無い数字で御社を語っている: {' / '.join(numbers)}")
    return {label: " / ".join(reasons) for label, reasons in problems.items()}


def _repair_round(context: str, report: gates.GateReport, core, jd_items, drill):
    """閘門に落ちた問だけを 1 回の呼び出しでまとめて作り直す。

    作り直せなかった問は元のまま残す（黙って空にしない）。
    """

    problems = _problems(report)
    if not problems:
        return core, jd_items, drill

    targets: list[tuple[str, str, list[str], str]] = []
    for item in render.labelled(core, jd_items, drill):
        problem = problems.get(item.label)
        if problem:
            targets.append((item.label, item.question, item.points, problem))
    targets = targets[:gates.MAX_REPAIR_TARGETS]

    fixed = generate.repair_batch(
        context, targets, used_evidence=list(report.overused))
    return _apply(fixed, core, jd_items, drill)


def _apply(fixed: dict[str, QA], core, jd_items, drill):
    """書き直された問を差し戻す。返ってこなかった問は元のまま残す。"""

    if not fixed:
        return core, jd_items, drill

    def apply_core(answer: bank.CoreAnswer) -> bank.CoreAnswer:
        new = fixed.get(answer.question)
        if new is None:
            return answer
        return bank.CoreAnswer(
            qid=answer.qid, category=answer.category, question=answer.question,
            conclusion=new.conclusion, points=new.points,
            evidence=answer.evidence, jd_dependent=answer.jd_dependent,
        )

    def apply_qa(item: QA) -> QA:
        new = fixed.get(item.question)
        if new is None:
            return item
        return QA(item.question, new.conclusion, new.points, item.section,
                  item.keyword or new.keyword)

    return ([apply_core(a) for a in core],
            [apply_qa(q) for q in jd_items],
            [apply_qa(q) for q in drill])


def _refine_round(context: str, critique: critic.Critique, core, jd_items, drill):
    """批評で指摘された問だけを 1 回の呼び出しでまとめて書き直す。"""

    findings = {finding.label: finding for finding in critique.findings}
    targets = []
    for item in render.labelled(core, jd_items, drill):
        finding = findings.get(item.label)
        if finding:
            targets.append((item.label, item.question, item.conclusion,
                            item.points, finding.problem, finding.fix))
    refined = generate.refine_batch(context, targets, focus=critique.weakest)
    return _apply(refined, core, jd_items, drill)


def _check(items, *, material: str, requirements: list[str],
           persona_traits: list[str], product_names: list[str],
           job_material: str, project_names: list[str]) -> gates.GateReport:
    """機械閘門 — build 内で引数を固定して使い回す（`partial`）。"""

    return gates.run(items, material, requirements, persona_traits, product_names,
                     job_material=job_material, project_names=project_names)


def _critique_loop(context: str, review_context: str, check, core, jd_items, drill):
    """採点 → 是正 → 再採点。良くなった版だけを採る。

    `context` は是正用（応募者プロフィールを含む）、`review_context` は採点用
    （求人側だけ）。渡し分けの理由は `_review_context` の説明を見ること。
    `check` は機械閘門（`_check` を build で固定したもの）。

    戻り値: ((core, jd_items, drill), 機械閘門レポート, 採点履歴, 採用した巡)。

    初回は全問を採点し、2 巡目以降は**書き直した問だけ**を採点し直す
    （`critic.review(only=...)`）。合否は問ごと（`Critique.passed`）。書き直した問の
    合計点が下がった巡は捨てて前の版を返す（`critic.round_delta`）。改善が
    `critic.STALL_ROUNDS` 回続けて止まったら `critic.MAX_ROUNDS` 前でも打ち切る。
    批評が失敗した（None）ときも前の版を返す — 「批評できなかった」を「合格」と
    読み替えない。
    """

    items = render.labelled(core, jd_items, drill)
    current = critic.review(review_context, items)
    if current is None:
        return (core, jd_items, drill), check(items), [], 0

    history = [current]
    adopted = 1
    best = (core, jd_items, drill)
    best_report = check(items)
    stall = 0
    for _ in range(critic.MAX_ROUNDS):
        if current.passed or not current.findings:
            break
        targets = [item.label for item in current.failing
                   if any(f.label == item.label for f in current.findings)]
        if not targets:
            break
        core, jd_items, drill = _refine_round(context, current, core, jd_items, drill)
        # 批評の是正が事実閘門を壊していないか見る。壊していたら 1 回だけ機械修復
        report = check(render.labelled(core, jd_items, drill))
        if not report.ok:
            core, jd_items, drill = _repair_round(context, report, core, jd_items, drill)
            report = check(render.labelled(core, jd_items, drill))
        # 不合格なのに指摘の無い問（指摘の上限・却下で漏れた）も採点し直して指摘を取る。
        # 入れないと 1 巡目で漏れた問は最後まで書き直されない（実測 6094: 57 問不合格・
        # 書き直し 20 問、v3 で指摘 0 件となり打ち切り）。改善判定は targets だけで見る。
        orphans = [item.label for item in current.failing
                   if item.label not in targets][:critic.REVIEW_BATCH]
        nxt = critic.review(review_context, render.labelled(core, jd_items, drill),
                            previous=current, only=targets + orphans)
        if nxt is None:
            break
        history.append(nxt)
        delta = critic.round_delta(current, nxt, targets)
        if delta < 0:
            core, jd_items, drill = best
            break  # 書き直した問の合計点が悪化 — この巡は捨てて打ち切る
        best, best_report, current = (core, jd_items, drill), report, nxt
        adopted = len(history)
        if delta == 0:
            stall += 1
            if stall >= critic.STALL_ROUNDS:
                break
        else:
            stall = 0
    return best, best_report, history, adopted


def _cli() -> None:
    import argparse
    import sqlite3

    parser = argparse.ArgumentParser(description="面接 QA を単独で生成する")
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--out", help="出力ディレクトリ（既定: output/prep/{id}_{slug}）")
    parser.add_argument("--no-critique", action="store_true",
                        help="面接官の目での批評 → 是正の巡回を回さない（LLM 6 回に戻る）")
    args = parser.parse_args()

    conn = sqlite3.connect(bank.ROOT / "data" / "jobs.sqlite")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
    if row is None:
        raise SystemExit(f"job {args.job_id} が見つかりません")
    job = dict(row)

    if args.out:
        pack_dir = Path(args.out)
    else:
        import re
        slug = re.sub(r"株式会社|有限会社|\s+", "", job.get("company") or "company")
        slug = re.sub(r"[^\w一-鿿぀-ヿa-zA-Z0-9]", "", slug)[:20]
        pack_dir = bank.ROOT / "output" / "prep" / f"{job['id']}_{slug}"
    pack_dir.mkdir(parents=True, exist_ok=True)

    facts = ""
    for name in ("_facts.md", "00_company_brief.md", "01_company_brief.md"):
        candidate = pack_dir / name
        if candidate.exists():
            facts = candidate.read_text(encoding="utf-8")
            print(f"[facts] {name}（{len(facts)} 字）")
            break
    stats = build(job, facts, pack_dir, critique=not args.no_critique)
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    _cli()
