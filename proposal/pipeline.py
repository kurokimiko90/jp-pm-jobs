"""stage 編排 — 生成 → 機械閘門 → 是正再生成 → degraded 記録。

growth/pipeline.py と同じ骨格（内容尋址キャッシュ・RETRY_MAX・degraded 保存）だが、
LLM は `proposal._llm`（miko-ws 指揮中心のみ）を通す。

stage 間の受け渡しで 1 箇所だけ機械的な抽出がある: `capabilities()` が persona の
能力表から能力名を取り出し、cards の生成指示と Gate D の覆蓋判定の両方に使う。
LLM に「前段で挙げた能力」を思い出させるのではなく、機械で渡して機械で照合する。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import (context, frameworks, gates, history, images, japanese_quality,
               lessons, prompts, research, trace)
from ._llm import LLMUnavailable, call_traced

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "output" / "proposal"
RETRY_MAX = 2
PREV_MAX_CHARS = 4000
RESEARCH_MAX_CHARS = 13000   # prompt に載せる取得原文の上限
RESEARCH_MIN_PER_PAGE = 450  # どの頁も最低これだけは prompt に顔を出す
# 種別ごとの分量の重み。プロダクトそのものの説明と、事業の前提が変わった
# ニュース記事に厚く配る。トップページは各事業の見出しの寄せ集めなので薄くてよく、
# 採用ページの文言は提案の材料にならない
_RESEARCH_WEIGHT = {
    "service": 1.8, "pricing": 1.8, "about": 1.7, "news_article": 1.5,
    "case": 1.3, "ir": 1.2, "offsite": 0.8, "top": 0.7, "news": 0.5,
    "careers": 0.5, "other": 0.8,
}


@dataclass
class StageResult:
    name: str
    status: str            # ok | degraded | cached | skipped | failed
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    path: str = ""
    elapsed: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    s = re.sub(r"株式会社|有限会社|\s+", "", text or "company")
    s = re.sub(r"[^\w一-鿿぀-ヿa-zA-Z0-9]", "", s)
    return s[:20] or "company"


def pack_dir(job: dict) -> Path:
    return OUT_DIR / f"{job['id']}_{_slug(job.get('company') or '')}"


def _read_prev(pdir: Path, stage: str) -> str:
    meta = prompts.STAGES.get(stage)
    if not meta:
        return ""
    path = pdir / meta["file"]
    if not path.exists():
        return "（未生成）"
    text = path.read_text(encoding="utf-8")
    if len(text) > PREV_MAX_CHARS:
        text = text[:PREV_MAX_CHARS] + "\n…（以下略）"
    return text


def capabilities(pdir: Path, job: dict) -> list[str]:
    """persona の能力表から能力名を取り出す（cards の対象と Gate D の照合基準）。

    表が読めなければ gap 分析の要件リストへ退避する。両方無ければ空 —
    その場合 cards stage は覆蓋判定をしない（無い基準で落とさない）。
    """
    path = pdir / prompts.STAGES["persona"]["file"]
    caps: list[str] = []
    if path.exists():
        text = path.read_text(encoding="utf-8")
        section = text.split("## 3. 評価される能力")
        if len(section) > 1:
            body = section[1].split("\n## ")[0]
            for line in body.splitlines():
                cells = [c.strip() for c in line.split("|") if c.strip()]
                if len(cells) < 3:
                    continue
                if re.fullmatch(r"[-:\s]+", cells[0]) or cells[0] in ("順位", "能力"):
                    continue
                name = cells[1] if re.match(r"^\d", cells[0]) else cells[0]
                name = re.sub(r"\*\*", "", name).strip()
                if name and name not in caps and len(name) <= 30:
                    caps.append(name)
    if not caps:
        caps = context.gap_requirements(job)
    return caps[:8]


def verification_questions(pdir: Path) -> list[str]:
    """persona §4「選考で必ず確かめられること」の問いを取り出す（playbook 用）。

    §3 の能力表（`capabilities()`）が「何を評価されるか」なら、こちらは
    「それをどう確かめられるか」。playbook のカードはこの問いに答えられる形で
    書かせたいので、LLM に前段を思い出させず機械で渡す（`capabilities()` と
    同じ方針）。読めなければ空 — その場合 prompt 側が「自分で想定追問を置く」
    へ退避する（無い基準でカードを落とさない）。
    """
    path = pdir / prompts.STAGES["persona"]["file"]
    if not path.exists():
        return []
    section = path.read_text(encoding="utf-8").split("## 4. 選考で必ず確かめられること")
    if len(section) < 2:
        return []
    out: list[str] = []
    for line in section[1].split("\n## ")[0].splitlines():
        q = re.sub(r"^[-*+]\s+", "", line.strip())
        # 箇条書き記号が取れた＝元が箇条書き行、かつ問いの体裁があるものだけ
        if q != line.strip() and q and q not in out:
            out.append(re.sub(r"\*\*", "", q))
    return out[:6]


def research_block(pdir: Path, max_chars: int = RESEARCH_MAX_CHARS) -> str:
    """取得原文の本文部分（prompt 用に長さを切る）。空なら空文字。

    先頭から一括で切ると、後から巡回したページ（事業・料金・導入事例など、
    トップより後ろに並ぶもの）が丸ごと落ちる。ページごとに予算を分けて頭から
    取り、全ページが必ず prompt に顔を出すようにする。

    ただし**等分もしない**。研究層が 4 頁から 15 頁へ増えた結果、等分だと
    1 頁 600 字になり、プロダクトサイトも「事例は準備中です」の 1 行ページも
    同じ枠を取った。種別ごとに重みを付け、事業・製品・価格・個別記事へ厚く配る。
    """
    pages = [(k, b) for k, b in research.quote_pages(pdir) if b.strip()]
    if not pages:
        return ""
    weights = [_RESEARCH_WEIGHT.get(k, 1.0) for k, _b in pages]
    total = sum(weights) or 1.0
    out = []
    for (_kind, block), w in zip(pages, weights):
        per = max(RESEARCH_MIN_PER_PAGE, int(max_chars * w / total))
        out.append(_clip(block, per))
    return "\n".join(out)[:max_chars]


def _clip(block: str, budget: int) -> str:
    """頁が予算を超えるときは**頭と尻の両方**を残す。

    頭から一律に切ると、会社概要ページの末尾にある「従業員数 / 設立 / 資本金」が
    毎回消える（実測: 従業員数 79 名が prompt へ一度も届いていなかった）。
    組織の規模は提案の前提そのもの — 専任チームがある想定で書いた打ち手は、
    数十人の会社では成立しない。料金表やスペック表も同じく頁の下にある。
    """
    if len(block) <= budget:
        return block
    # 中略の印も予算のうち。数えないと合計が上限を超え、最後の `[:max_chars]` が
    # **末尾から**削る — 尻を残すために入れた仕組みが、その尻を落とす
    marker = "\n…（中略）…\n"
    room = max(200, budget - len(marker))
    head = int(room * 0.6)
    return f"{block[:head]}{marker}{block[-(room - head):]}"


def build_prompt(job: dict, stage: str, pdir: Path, facts: str,
                 feedback: str = "", case_anchor: str = "") -> str:
    meta = prompts.STAGES[stage]
    raw = research_block(pdir) if meta.get("needs_research") else ""
    fields = {
        # 研究層と persona は相手を読む stage。プロフィールを渡すと人物像も評価
        # モデルも本人の形へ寄る（自分に有利な配点を引いてしまう）。Gate B の
        # 数字素材は `evidence_corpus` が別途持つので、外しても閘門は緩まない
        "context": context.build(job, facts,
                                 with_profile=meta.get("needs_profile", True)),
        "common_rules": prompts.COMMON_RULES,
        "frameworks": (frameworks.render() if meta.get("needs_frameworks") else ""),
        "capabilities": "",
        "verification_questions": "",
        # 指定が無ければ空文字。prompt 側の {case_anchor} が消えるだけで既定挙動は不変
        "case_anchor": (prompts.CASE_ANCHOR_BLOCK.format(case_name=case_anchor)
                        if case_anchor else ""),
        "fact_rules": prompts.FACT_TAG_RULES,
        "research": raw or "（官網から 1 ページも取得できなかった。"
                           "この求人では `[事実]` タグを使えない — すべて `[仮説]` で書くこと）",
    }
    for dep in meta["deps"]:
        fields[f"prev_{dep}"] = _read_prev(pdir, dep)
    if stage in ("cards", "playbook"):
        caps = capabilities(pdir, job)
        fields["capabilities"] = ("\n".join(f"- {c}" for c in caps)
                                  if caps else "（人物像の能力表が読めなかった — "
                                               "JD から自分で 5〜6 個選ぶこと）")
    if stage == "playbook":
        qs = verification_questions(pdir)
        fields["verification_questions"] = (
            "\n".join(f"- {q}" for q in qs) if qs else
            "（人物像の §4 が読めなかった — 能力ごとに出そうな追問を自分で 1 つ置くこと）")
    prompt = meta["prompt"].format(**fields)
    # 過去の紅隊指摘から学んだ癖は、提案を書く側にだけ渡す。紅隊 stage に渡すと
    # 「指摘済みのことは指摘しない」方向へ寄って、批判が甘くなる
    if stage in ("main_case", "cards"):
        learned = lessons.render()
        if learned:
            prompt += f"\n\n{learned}\n"
    # 閘門の条件は prompt に手で書かず、GateSpec から生成して末尾に置く。
    # 一番最後に置くのは、長い素材のあとで「何を守るか」を読み直させるため。
    # Gate F の照合先は「取得原文 ＋ JD」なので、needs_research でない stage
    # （hypotheses）でも JD があれば `[事実]` は使える — raw の有無だけで
    # 判定すると「今回は事実タグを使えない」と嘘を教えることになる
    if meta.get("spec"):
        has_source = bool(raw or research.quote_corpus(pdir).strip()
                          or context.jd_text(job).strip())
        prompt += "\n\n" + gates.self_check(
            meta["spec"], has_research=has_source) + "\n"
    # 見出しは feedback 側が持つ（閘門不合格なのか採点が低いのかで文言が変わる）
    if feedback:
        prompt += f"\n\n{feedback}\n"
    return prompt


def _maybe_generate_image(job: dict, pdir: Path, stage: str, *, force: bool,
                          no_llm: bool, engine: str | None = None) -> None:
    """md が通った直後に対応するインフォグラフィックを作る（`with_images=True` 時のみ）。

    force: 呼び出し側と揃える — md を新規/強制再生成した直後は画像も古いので
    強制で作り直す（cached 経路は force=False で「無ければ埋める」だけ）。
    画像の生成失敗は本文の生成を無駄にしないよう本流を止めない
    （LLMUnavailable ＝ 指揮中心が死んでいるときだけ本流と同じく止める）。
    """
    if stage not in images.IMAGE_STAGES + images.CARD_IMAGE_STAGES:
        return
    try:
        if stage in images.CARD_IMAGE_STAGES:
            # 能力ごとに 1 枚 = 能力数だけ call が飛ぶ。枚数を先に出してから回す
            n = len(images.cards_of(
                (pdir / prompts.STAGES[stage]["file"]).read_text(encoding="utf-8")))
            icon = "…" if no_llm else "🖼 "
            print(f"  {icon} {stage}: 能力 {n} 件 → 1 件 1 枚で生成"
                  + ("（dry-run）" if no_llm else f"（{n} call）"))
            paths = images.generate_cards(job, pdir, stage, force=force,
                                          no_llm=no_llm, engine=engine)
            print(f"  {icon} {stage}: 画像 {len(paths)} 枚")
            return
        path = images.generate(job, pdir, stage, force=force, no_llm=no_llm,
                               engine=engine)
        icon = "…" if no_llm else "🖼 "
        print(f"  {icon} {stage}: 画像 → {path.name}")
    except LLMUnavailable:
        raise
    except Exception as e:
        print(f"  ⚠ {stage}: 画像生成失敗（本文は正常） — {str(e)[:120]}")


def _accept_for(spec: gates.GateSpec) -> dict:
    """指揮中心の受け入れ条件 — 形式だけ（禁止語は入れない）。

    interview/qa の教訓: 長文の一括出力に禁止語 accept を入れると 1 語で全体が
    差し戻され、brain 総当たりの末に gateway が 500 を返す。
    """
    acc: dict = {"minChars": int(spec.min_chars * 0.7),
                 "minLines": int(spec.min_lines * 0.7)}
    if spec.required_sections:
        acc["includesAll"] = spec.required_sections[:3]
    return acc


def _run_deck(job: dict, pdir: Path, facts: str, *, force: bool,
              no_llm: bool, from_fields: bool = False,
              extra_feedback: str = "") -> StageResult:
    """deck は成果物が HTML + 構成 JSON なので共通枠を通さず deck.py へ委譲する。"""
    from . import deck
    t0 = time.time()
    if no_llm:
        (pdir / "_prompts").mkdir(parents=True, exist_ok=True)
        (pdir / "_prompts" / "deck.prompt.md").write_text(
            deck.build_prompt(pdir), encoding="utf-8")
        print("  → deck: prompt 落地（--no-llm）")
        return StageResult("deck", "skipped", 0, [], "")
    if not force and not from_fields and deck.html_path(pdir).exists():
        print("  ⏭  deck: 既存（作り直すなら --force / 手直し後は --from-fields）")
        return StageResult("deck", "cached", 0, [], str(deck.html_path(pdir)))
    status, errors = deck.build(job, pdir, context.evidence_corpus(job, facts),
                                from_fields=from_fields,
                                extra_feedback=extra_feedback)
    icon = {"ok": "✓", "degraded": "⚠", "failed": "✗"}[status]
    print(f"  {icon} deck: {status} → {prompts.STAGES['deck']['file']}")
    history.record(job, "deck", prompts.PROMPT_VERSION, status, 1, errors,
                   time.time() - t0)
    return StageResult("deck", status, 1, errors, str(deck.html_path(pdir)),
                       time.time() - t0)


def run_stage(job: dict, stage: str, pdir: Path, facts: str, *,
              force: bool = False, no_llm: bool = False,
              from_fields: bool = False, extra_feedback: str = "",
              with_images: bool = False, images_dry_run: bool = False,
              image_engine: str | None = None,
              case_anchor: str = "") -> StageResult:
    meta = prompts.STAGES[stage]
    if meta.get("custom") == "deck":
        return _run_deck(job, pdir, facts, force=force, no_llm=no_llm,
                         from_fields=from_fields, extra_feedback=extra_feedback)
    spec: gates.GateSpec = meta["spec"]
    out_path = pdir / meta["file"]
    cache_path = pdir / "_cache" / f"{stage}.json"
    t0 = time.time()

    prompt = build_prompt(job, stage, pdir, facts, extra_feedback, case_anchor)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    if not force and out_path.exists() and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
        if cached.get("digest") == digest and cached.get("status") in ("ok", "degraded"):
            print(f"  ⏭  {stage}: キャッシュ命中（素材変化なし）")
            if with_images:
                _maybe_generate_image(job, pdir, stage, force=False,
                                      no_llm=images_dry_run, engine=image_engine)
            return StageResult(stage, "cached", cached.get("attempts", 0),
                               cached.get("errors", []), str(out_path))

    if no_llm:
        (pdir / "_prompts").mkdir(parents=True, exist_ok=True)
        (pdir / "_prompts" / f"{stage}.prompt.md").write_text(prompt, encoding="utf-8")
        print(f"  → {stage}: prompt 落地（--no-llm）")
        return StageResult(stage, "skipped", 0, [], "")

    # prompt テンプレート自体の数字（「最初の 90 日を 3 期に分ける」等）は
    # こちらが指示した構造であって捏造ではない。素材に足しておかないと、
    # 指示どおりに書いた出力を Gate B が毎回弾く（実測で 2 求人とも踏んだ）
    # Gate F の照合先 = 取得原文 ＋ JD 本文。JD に書いてあることは「知った風」では
    # なく白紙黒字の事実なので、`[事実]` の出所として認める
    raw_research = research.quote_corpus(pdir)
    fact_sources = "\n".join(x for x in (raw_research, context.jd_text(job)) if x)
    corpus = (context.evidence_corpus(job, facts, raw_research) + "\n"
              + meta["prompt"])
    caps = capabilities(pdir, job) if stage in ("cards", "playbook") else []
    feedback = extra_feedback
    last_errors: list[str] = []
    last_warnings: list[str] = []
    text = ""
    for attempt in range(1, RETRY_MAX + 2):
        p = prompt if attempt == 1 else build_prompt(job, stage, pdir, facts,
                                                     feedback, case_anchor)
        label = "生成" if attempt == 1 else f"是正再生成 {attempt - 1}/{RETRY_MAX}"
        print(f"  … {stage}: {label} 中（最長 {meta['timeout']}s）")
        t_att = time.time()
        try:
            text, llm_meta = call_traced(p, timeout=meta["timeout"],
                                         accept=_accept_for(spec))
        except LLMUnavailable:
            raise
        except Exception as e:
            last_errors = [f"[LLM] 呼び出し失敗: {e}"]
            trace.record(pdir, job=job, stage=stage, attempt=attempt, prompt=p,
                         gate="error", errors=last_errors,
                         elapsed=time.time() - t_att,
                         prompt_version=prompts.PROMPT_VERSION)
            print(f"  ✗ {stage}: LLM 失敗 — {str(e)[:120]}")
            break
        result = gates.check(text, spec, job, corpus=corpus, capabilities=caps,
                             has_facts=bool(facts), research=fact_sources)
        # 通っても落ちても残す。落ちた本文こそ「何を直せば通ったか」の証拠になる
        trace.record(pdir, job=job, stage=stage, attempt=attempt, prompt=p,
                     output=text, meta=llm_meta,
                     gate="pass" if result.ok else "fail",
                     errors=result.errors, elapsed=time.time() - t_att,
                     prompt_version=prompts.PROMPT_VERSION)
        if result.ok:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_with_header(meta, text), encoding="utf-8")
            _write_cache(cache_path, digest, "ok", attempt, [])
            history.record(job, stage, prompts.PROMPT_VERSION, "ok", attempt,
                           [], time.time() - t0)
            print(f"  ✓ {stage}: 閘門通過（{attempt} 回目）→ {meta['file']}")
            if with_images:
                # 本文が今まさに新しくなったので、古い画像が残っていても作り直す
                _maybe_generate_image(job, pdir, stage, force=True,
                                      no_llm=images_dry_run, engine=image_engine)
            return StageResult(stage, "ok", attempt, [], str(out_path),
                               time.time() - t0, result.warnings)
        last_errors, last_warnings = result.errors, result.warnings
        # 是正指示は積む。紅隊由来の指摘（extra_feedback）を閘門の指摘で
        # 上書きすると、点が低かった軸を直さないまま形式だけ整った版が返る。
        # 落ちた本文も渡す — 指摘だけだと白紙から書き直され、通っていた節が
        # 巻き添えで別物になる（Gate A を直した版で Gate B が新しく落ちる往復）
        feedback = "\n".join(
            x for x in (extra_feedback, result.as_feedback(previous=text)) if x)
        print(f"  ⚠ {stage}: 閘門 {len(result.errors)} 件不合格")
        for e in result.errors[:3]:
            print(f"      {e[:100]}")

    if text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        warn = ("\n\n---\n> ⚠ **この文書は機械閘門を通過していない（degraded）。**\n"
                "> 未解決の指摘:\n"
                + "\n".join(f"> - {e}" for e in last_errors) + "\n")
        out_path.write_text(_with_header(meta, text) + warn, encoding="utf-8")
        _write_cache(cache_path, digest, "degraded", RETRY_MAX + 1, last_errors)
        history.record(job, stage, prompts.PROMPT_VERSION, "degraded",
                       RETRY_MAX + 1, last_errors, time.time() - t0)
        print(f"  ⚠ {stage}: degraded で保存 → {meta['file']}")
        return StageResult(stage, "degraded", RETRY_MAX + 1, last_errors,
                           str(out_path), time.time() - t0, last_warnings)

    _write_cache(cache_path, digest, "failed", RETRY_MAX + 1, last_errors)
    history.record(job, stage, prompts.PROMPT_VERSION, "failed", RETRY_MAX + 1,
                   last_errors, time.time() - t0)
    return StageResult(stage, "failed", RETRY_MAX + 1, last_errors, "",
                       time.time() - t0)


def _with_header(meta: dict, text: str) -> str:
    return f"# {meta['title']}\n\n_生成日: {date.today().isoformat()}_\n\n{text}\n"


def _write_cache(path: Path, digest: str, status: str, attempts: int,
                 errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "digest": digest, "status": status, "attempts": attempts,
        "errors": errors, "at": date.today().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def existing_results(pdir: Path, ran: set[str]) -> list[StageResult]:
    """今回動かさなかった stage の状態を、前回の成果物とキャッシュから復元する。

    これが無いと `--stage redteam` だけ回したときに検証レポートが 1 行になり、
    他の stage が「未実行」に見える（実際には前回の成果物が残っている）。
    """
    out: list[StageResult] = []
    for name in prompts.STAGE_ORDER:
        if name in ran:
            continue
        path = pdir / prompts.STAGES[name]["file"]
        if not path.exists():
            continue
        status, attempts, errors = "cached", 0, []
        cache = pdir / "_cache" / f"{name}.json"
        if cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                status = data.get("status", "cached")
                attempts = data.get("attempts", 0)
                errors = data.get("errors", [])
            except Exception:
                pass
        out.append(StageResult(name, status, attempts, errors, str(path)))
    return out


def write_review(job: dict, pdir: Path, results: list[StageResult],
                 caps: list[str], has_facts: bool) -> Path:
    icon = {"ok": "✅ 通過", "cached": "⏭ キャッシュ", "degraded": "⚠ 未通過",
            "failed": "❌ 失敗", "skipped": "— 未実行"}
    lines = [
        "# 検証レポート — 品質ゲートの結果と目視確認事項",
        "",
        f"_生成日: {date.today().isoformat()} / 対象: {job.get('company')} "
        f"{job.get('title')} (job_id={job['id']})_",
        "",
        "## 1. 各 stage の品質ゲート結果",
        "",
        "| stage | 出力 | 状態 | 試行 | 未解決の指摘 |",
        "|---|---|---|---|---|",
    ]
    order = {n: i for i, n in enumerate(prompts.STAGE_ORDER)}
    for r in sorted(results, key=lambda x: order.get(x.name, 99)):
        meta = prompts.STAGES.get(r.name, {})
        lines.append(f"| {r.name} | {meta.get('file', '-')} | "
                     f"{icon.get(r.status, r.status)} | {r.attempts} | {len(r.errors)} |")

    bad = [r for r in results if r.status in ("degraded", "failed")]
    lines += ["", "## 2. 未解決の指摘", ""]
    if not bad:
        lines.append("すべての stage が品質ゲートを通過した。")
    for r in bad:
        lines.append(f"### {r.name}")
        lines += [f"- {e}" for e in r.errors]
        lines.append("")

    warned = [r for r in results if r.warnings]
    if warned:
        lines += ["## 2b. 参考指摘（非ブロッキング・目視確認推奨）", ""]
        for r in warned:
            lines.append(f"### {r.name}")
            lines += [f"- {w}" for w in r.warnings]
            lines.append("")

    audit_path = pdir / japanese_quality.AUDIT_FILE
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception:
            audit = {}
        lines += [
            "## 2c. Deck の日本語最終校閲",
            "",
            f"- 判定: **{audit.get('verdict') or '不明'}**",
            f"- 実行方式: {audit.get('mode') or '不明'}",
            f"- 修正件数: {len(audit.get('changes') or [])}",
            f"- 監査記録: [`{japanese_quality.AUDIT_FILE}`]"
            f"({japanese_quality.AUDIT_FILE})",
            "",
        ]

    raw = research.load(pdir)
    n_pages = raw.count("出典: ")
    total, scores = redteam_score(pdir)
    lines += [
        "## 3. 研究層で実際に取れたもの",
        "",
        f"- 公式サイトから取得したページ数: **{n_pages}**"
        + ("（`_research_raw.md` に原文）" if n_pages else
           " — **1 ページも取れていない。会社に関する記述はすべて仮説のはず**"),
        "- Gate F はこの原文だけを `[事実]` の照合先にする。原文に無い引用は"
        "落とされるが、**原文にある = 今も正しい、ではない**（更新日を自分で見る）。",
        "",
    ]
    if total is not None:
        lines += [
            "## 3b. 採用側による採点（レッドチームの自己採点・参考値）",
            "",
            "| 評価軸 | 点数 |",
            "|---|---|",
        ] + [f"| {k} | {v}/10 |" for k, v in scores.items()] + [
            f"| **合計** | **{total}/70** |",
            "",
            "この点は LLM が自分の出力に付けた点で、面接の結果を予測するものではない。"
            "**低い軸がどこか**だけを見る（`--refine` はこの点で書き直しを判断する）。",
            "",
        ]
    lines += [
        "## 4. 必要な能力への対応状況",
        "",
        f"- 人物像から抽出した能力: {len(caps)} 個 — {', '.join(caps) or '（抽出できず）'}",
        "- Gate D は能力名がカード見出しにあるかの**文字列照合**。中身がその能力を"
        "本当に語れているかは判定していない。",
        "",
        "## 5. 機械では判定できないこと（必ず自分で読む）",
        "",
        "| # | 確認事項 | なぜ機械では無理か |",
        "|---|---|---|",
        "| 1 | 提案の筋が実際に通っているか | 事業の実態を知らないと判断できない。"
        "`09_redteam.md` の指摘を先に読む |",
        "| 2 | 製品理解が的外れでないか | "
        + ("会社事実を渡しているので断定は許可されている。出典を再確認する |"
           if has_facts else
           "会社事実**未提供**。製品への言及はすべて仮説のはず。断定が残っていないか |"),
        "| 3 | 実績の紐付けが誇張でないか | 「同じことをやった」と言えるかは本人しか"
        "分からない。Gate B は数字しか、Gate G は空欄と定型句しか見ていない |",
        "| 4 | 90 日計画の現実性 | 社内の会議体・割り込み量は外から見えない |",
        "| 5 | 仮説が的外れでないか | Gate F は出所の有無だけを見る。"
        "推論そのものの妥当性は判定していない |",
        "",
        "## 6. 事実の根拠として照合した範囲",
        "",
        "- 数字: プロフィール・正典回答・JD・`--facts`・取得原文にあるものだけ（Gate B）",
        "- 会社の記述: `[事実]` は取得原文への引用照合つき（Gate F）。"
        f"照合先は {n_pages} ページ",
        "- 方法論: `data/frameworks.yaml` の "
        f"{len(frameworks.load())} 件のみ（一般論のフレームワーク名は prompt で禁止）",
        "- 会社事実ファイル: "
        + ("`--facts` 提供済み" if has_facts else "未提供（研究層の取得原文で代替）"),
        "",
    ]
    path = pdir / prompts.REVIEW_FILE
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_readme(job: dict, pdir: Path, results: list[StageResult]) -> Path:
    done = {r.name for r in results if r.status in ("ok", "cached", "degraded")}
    lines = [
        f"# 提案型面接パック — {job.get('company')} / {job.get('title')}",
        "",
        f"job_id: {job['id']} ｜ 生成日: {date.today().isoformat()} ｜ "
        f"recommend_score: {job.get('recommend_score')}",
        "",
        "## 読む順番",
        "",
        "| # | ファイル | 内容 | 状態 |",
        "|---|---|---|---|",
    ]
    layer_ja = {"research": "研究", "thinking": "思考", "interview": "面接"}
    prev_layer = ""
    idx = 0
    for name in prompts.STAGE_ORDER:
        meta = prompts.STAGES[name]
        layer = meta.get("layer", "")
        if layer != prev_layer:
            lines.append(f"| | **{layer_ja.get(layer, layer)}層** | | |")
            prev_layer = layer
        idx += 1
        mark = "✅" if name in done else "—"
        lines.append(f"| {idx} | [{meta['file']}]({meta['file']}) | {meta['title']} | {mark} |")
    total, _ = redteam_score(pdir)
    lines += [
        f"| {idx + 1} | [{prompts.REVIEW_FILE}]({prompts.REVIEW_FILE}) | "
        "品質ゲートの結果と自分で確認すること | ✅ |",
        f"| | [{japanese_quality.AUDIT_FILE}]({japanese_quality.AUDIT_FILE}) | "
        "Deck の日本語最終校閲結果 | "
        + ("✅" if (pdir / japanese_quality.AUDIT_FILE).exists() else "—") + " |",
        "| | [_research_raw.md](_research_raw.md) | 公式サイトから取得した原文"
        "（分析の出所） | "
        + ("✅" if research.raw_path(pdir).exists() else "—") + " |",
        "",
    ]
    if total is not None:
        lines += [f"採用側の目での自己採点: **{total}/70**"
                  f"（{prompts.REVIEW_FILE} に内訳）", ""]
    lines += [
        "## 使い方",
        "",
        "1. **先に `09_redteam.md` を読む。** 自分の提案の穴が書いてある。"
        "そこを直せないまま面接に持ち込まない。",
        "2. `05_main_proposal.md` は 5 分で話す想定。丸暗記ではなく "
        "「課題認識 → 提案 → 進め方」の 3 点だけ頭に入れる。",
        "3. `04_hypotheses.md` は「なぜそう考えたか」を聞かれたときの土台。"
        "**断定せず、検証方法まで込みで話す**のがこのパックの肝。",
        "4. `08_experience_map.md` は「その経験はうちで通用するのか」への返し。",
        "5. `07_capability_cards.md` は追問への弾。1 枚 90 秒。",
        "6. `01_company.md` / `02_product.md` の `[仮説]` 行は面接で"
        "「〜と理解していますが合っていますか」と確認から入る。**断定しない。**",
        "7. `11_capability_playbook.md` は面接直前に一人で読み込む用。"
        "能力ごとの「実務でどう表れるか／進め方／注意点／思考ロジック」を"
        "自分の言葉で言えるかを確認する。",
        "",
        "## 再生成",
        "",
        "```bash",
        f"python3 -m proposal {job['id']}                       # 全 stage（キャッシュあり）",
        f"python3 -m proposal {job['id']} --layer research      # 会社/製品理解だけ",
        f"python3 -m proposal {job['id']} --stage main_case --force",
        f"python3 -m proposal {job['id']} --refine              # 採点が低ければ書き直す",
        f"python3 -m proposal {job['id']} --research-force      # 公式サイトを再取得",
        "```",
        "",
    ]
    path = pdir / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# v1（研究層が無かった頃）の成果物ファイル名。番号を振り直したので、同じ
# ディレクトリに残っていると `01_persona.md` と `01_company.md` が並んで
# 「どっちが今の分析か」が読み手に分からなくなる
_V1_FILES = ("01_persona.md", "02_main_proposal.md", "03_capability_cards.md",
             "04_redteam.md", "05_review_report.md", "06_deck.html")


def stale_v1_files(pdir: Path) -> list[str]:
    current = {m["file"] for m in prompts.STAGES.values()} | {prompts.REVIEW_FILE}
    return [f for f in _V1_FILES if f not in current and (pdir / f).exists()]


def redteam_score(pdir: Path) -> tuple[int | None, dict[str, int]]:
    path = pdir / prompts.STAGES["redteam"]["file"]
    if not path.exists():
        return None, {}
    text = path.read_text(encoding="utf-8")
    return gates.total_score(text), gates.parse_scores(text)


def _refine_feedback(pdir: Path, scores: dict[str, int], total: int) -> str:
    """紅隊の採点を主提案への是正指示に翻訳する（低い軸 2 つだけ渡す）。

    指摘を全部渡すと「全部直す」方向へ薄く広がる。点が低い軸に絞る。
    """
    worst = sorted(scores.items(), key=lambda kv: kv[1])[:2]
    text = (pdir / prompts.STAGES["redteam"]["file"]).read_text(encoding="utf-8")
    weak = text.split("## 1. 致命的な弱点")
    weak_body = weak[1].split("\n## ")[0].strip()[:1200] if len(weak) > 1 else ""
    axes = "、".join(f"{k}（{v}/10）" for k, v in worst)
    return (
        f"# ★採用側による採点が {total}/70 と低かった。以下を踏まえて書き直す★\n"
        f"特に点が低い軸: {axes}。この 2 軸を上げることだけに集中し、\n"
        f"他の部分は現状の質を落とさないこと。\n\n"
        f"採用側が挙げた致命的な弱点:\n{weak_body}\n")


def run(job: dict, stage_names: list[str], facts: str = "", *,
        force: bool = False, no_llm: bool = False,
        from_fields: bool = False, skip_research: bool = False,
        research_force: bool = False, refine: bool = False,
        sites: list[str] | None = None, with_images: bool = False,
        images_dry_run: bool = False,
        image_engine: str | None = None,
        case_anchor: str = "") -> list[StageResult]:
    pdir = pack_dir(job)
    pdir.mkdir(parents=True, exist_ok=True)

    stale = stale_v1_files(pdir)
    if stale:
        print(f"  ⚠ v1 の成果物が {len(stale)} 件残っている（番号が衝突して読み手が"
              f"混乱する）: {', '.join(stale[:3])}"
              + (" …" if len(stale) > 3 else ""))
        print(f"      消すなら: rm {pdir.relative_to(ROOT)}/{{"
              + ",".join(stale) + "}")

    # 研究層 — LLM を呼ばずに官網から原文を取る。ここが空だと後段は全部仮説になる
    if not skip_research and any(prompts.STAGES[n].get("needs_research")
                                 for n in stage_names):
        status, res = research.run(job, pdir, force=research_force or force,
                                   sites=sites)
        if status == "cached":
            print(f"  ⏭  research: 既存の生素材を使用（取り直すなら --research-force）")
        else:
            icon = "✓" if res.ok else "⚠"
            print(f"  {icon} research: {len(res.pages)} ページ取得"
                  f"（官網: {res.official or '特定できず'}） → {research.RAW_FILE}")
            for note in res.notes:
                print(f"      {note}")

    results = [run_stage(job, n, pdir, facts, force=force, no_llm=no_llm,
                         from_fields=from_fields, with_images=with_images,
                         images_dry_run=images_dry_run, image_engine=image_engine,
                         case_anchor=case_anchor)
               for n in stage_names]

    # 採点駆動の書き直し — 1 回だけ。紅隊が低い点を付けたら主提案を直して再採点する
    if refine and not no_llm and "redteam" in stage_names:
        total, scores = redteam_score(pdir)
        if total is not None and total < gates.REFINE_THRESHOLD:
            print(f"\n  ↻ 採点 {total}/70 < {gates.REFINE_THRESHOLD} — "
                  "主提案を書き直して再採点する（1 回だけ）")
            fb = _refine_feedback(pdir, scores, total)
            results.append(run_stage(job, "main_case", pdir, facts, force=True,
                                     extra_feedback=fb, with_images=with_images,
                                     images_dry_run=images_dry_run,
                                     image_engine=image_engine))
            for name in ("cards", "mapping", "redteam"):
                if name in stage_names:
                    results.append(run_stage(job, name, pdir, facts, force=True,
                                             with_images=with_images,
                                             images_dry_run=images_dry_run,
                                             image_engine=image_engine))
            new_total, _ = redteam_score(pdir)
            print(f"  ↻ 再採点: {total}/70 → {new_total}/70")
        elif total is not None:
            print(f"\n  ✓ 採点 {total}/70 ≥ {gates.REFINE_THRESHOLD} — 書き直し不要")

    if not no_llm:
        # 今回動かした結果 ＋ 前回の成果物が残っている stage（レポートを痩せさせない）
        full = results + existing_results(pdir, {r.name for r in results})
        write_review(job, pdir, full, capabilities(pdir, job), bool(facts))
        write_readme(job, pdir, full)
    return results
