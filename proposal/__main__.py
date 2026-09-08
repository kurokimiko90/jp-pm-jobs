"""CLI — python3 -m proposal <job_id> [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tracker.db import connect

from . import frameworks, images, pipeline, prompts, voice
from ._llm import LLMUnavailable

ROOT = Path(__file__).parent.parent


def _load_job(job_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {job_id} が DB に見つかりません")
    job = dict(row)
    if not (job.get("raw_jd") or "").strip():
        sys.exit(f"job_id {job_id} は raw_jd が空。先に JD を取得すること "
                 f"(python3 -m tools.refetch_jd --job-id {job_id})")
    return job


def _auto_facts(job: dict) -> str:
    """会社事実の自動拾い上げ — apply/interview パックの company brief を探す。

    interview/qa/build.py と同じ発想。ここを拾わないと提案が JD の再話になる。
    """
    for base in ("apply", "prep"):
        for d in sorted((ROOT / "output" / base).glob(f"{job['id']}_*")):
            for name in ("_facts.md", "00_company_brief.md", "01_company_brief.md"):
                p = d / name
                if p.exists():
                    return p.read_text(encoding="utf-8")
    return ""


def _run_voice(job: dict, pdir: Path, args) -> list:
    """口に出す 3 本を音声化する。本文の生成が終わった後の後処理。

    音声化は失敗しても本文の価値を損なわない（研究層・思考層は目で読む資料で、
    音声はその一部を移動中に練習するためのもの）。例外で全体を落とさない。
    """
    tracks = [t.strip() for t in (args.voice_track or "").split(",") if t.strip()]
    print(f"\n■ 音声化（{args.voice_engine}）: "
          f"{', '.join(tracks or voice.DEFAULT_TRACKS)}")
    try:
        results = voice.run(job, pdir, tracks or None,
                            force=args.force, engine=args.voice_engine,
                            send=not args.no_send)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 音声化に失敗（本文は正常）— {str(e)[:160]}")
        return []
    print(f"  → {(pdir / voice.AUDIT_FILE).relative_to(ROOT)}")
    return results


def _default_case_anchor() -> str:
    """`config/app.yaml` の `proposal_case_anchor`（缺檔・空欄なら錨定しない）。

    毎回 `--case-anchor` を手で打たずに済ませるための既定値。**全ての求人に同じ
    案件が刺さるわけではない**ので、常用するなら「どの職種でも切り口を作れる
    案件」を選ぶこと。求人ごとに外したいときは `--case-anchor ""` で空にできる。
    """
    from tools import app_config
    return (app_config.get("app", "proposal_case_anchor", "") or "").strip()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m proposal",
        description="提案型面接パック — JD が求める人物像を割り出し、その視点で"
                    "対象企業のプロダクトへ具体提案を書く")
    p.add_argument("job_id", type=int, nargs="?", help="jobs.sqlite の job id")
    p.add_argument("--stage", default=None,
                   help=f"カンマ区切り。候補: {','.join(prompts.STAGE_ORDER)}")
    p.add_argument("--layer", default=None, choices=sorted(prompts.LAYERS),
                   help="層ごとにまとめて実行（research / thinking / interview）")
    p.add_argument("--facts", default=None,
                   help="会社調査済み事実 Markdown のパス（省略時は既存パックから自動探索）")
    p.add_argument("--no-facts", action="store_true",
                   help="自動探索もしない（全て仮説として書かせる）")
    p.add_argument("--force", action="store_true", help="キャッシュを無視して再生成")
    p.add_argument("--refine", action="store_true",
                   help="紅隊の採点が低ければ主提案を書き直して再採点する（LLM 追加 4 call）")
    p.add_argument("--skip-research", action="store_true",
                   help="官網の取得をしない（既存の _research_raw.md があればそれを使う）")
    p.add_argument("--research-force", action="store_true",
                   help="既存の _research_raw.md があっても取り直す")
    p.add_argument("--site", default="",
                   help="官網 URL をカンマ区切りで明示指定（検索が求人媒体を"
                        "引いてしまうとき。複数指定すると全部巡回する）")
    p.add_argument("--research-only", action="store_true",
                   help="官網の取得だけして終わる（LLM を一切呼ばない）")
    p.add_argument("--from-fields", action="store_true",
                   help="deck: _deck.fields.json を手直しした後、LLM を呼ばず描き直す")
    p.add_argument("--no-llm", action="store_true",
                   help="prompt だけ落として LLM を呼ばない")
    p.add_argument("--iterate", action="store_true",
                   help="生成後に versions/v{N} へ快照し Telegram へ推送（採点待ちにする）")
    p.add_argument("--score", type=int, default=None, metavar="0-100",
                   help="人間の採点を記録。90 未満なら書き直して次の版を推送する")
    p.add_argument("--note", default="",
                   help="--score に添える指摘（書き直しの是正指示に翻訳される）")
    p.add_argument("--scope", default="auto",
                   choices=("auto", "full", "thinking", "deck"),
                   help="書き直しの範囲。auto=note にスライド語があれば deck だけ。"
                        "thinking=課題仮説から下流を全部やり直す")
    p.add_argument("--images", action="store_true",
                   help="研究層〜思考層 6 stage（company〜plan90）が閘門を通った"
                        "直後にインフォグラフィック PNG を 1 枚ずつ生成する。"
                        "既存パックに対して単独で付けると、md 済みの stage 分だけ"
                        "後追いで画像を埋める")
    p.add_argument("--image-engine", default=images.DEFAULT_ENGINE,
                   choices=("codex", "agy", "gemini1"),
                   help=f"生図エンジンの第一希望（既定 {images.DEFAULT_ENGINE}）。"
                        "codex=codex exec 内蔵 image_gen（遅いが日本語が崩れにくい）、"
                        "agy=Antigravity CLI の generate_image（Gemini 画像。7 倍速いが"
                        "小さい日本語が崩れ、素材に無い数字やロゴを描く）。"
                        "失敗時の備援順は miko-ws 指揮中心が持つ")
    p.add_argument("--images-dry-run", action="store_true",
                   help="--images と併用。LLM を呼ばず、組み立てた画像 prompt を"
                        "_prompts/{stage}.image.prompt.md に落とすだけで終わる")
    p.add_argument("--voice", action="store_true",
                   help="本文の生成後、口に出す 3 本（pitch / qa / cards）を"
                        "ChatGPT の読み上げで音声化し Telegram へ送る。"
                        "全文は読み上げない（研究層・思考層は目で読む資料）")
    p.add_argument("--voice-track", default="",
                   help=f"--voice の対象をカンマ区切りで絞る"
                        f"（既定 {','.join(voice.DEFAULT_TRACKS)}）")
    p.add_argument("--voice-engine", choices=("gpt", "theater"), default="gpt",
                   help="gpt=ChatGPT の読み上げを捕捉（既定）、"
                        "theater=edge_tts 合成音（0 call・数秒。下見用）")
    p.add_argument("--voice-only", action="store_true",
                   help="本文の生成をせず、既存パックの音声化だけ行う")
    p.add_argument("--no-send", action="store_true",
                   help="--voice の Telegram 送信をスキップ")
    p.add_argument("--case-anchor", default=_default_case_anchor(), metavar="案件名",
                   help="能力カード（cards）の「本人の実績」を指定した 1 案件へ寄せ、"
                        "能力ごとに違う側面を切り出させる。面接の主題を 1 件に絞って"
                        "追問を集めたいときに使う。該当が無い能力には紐づけない"
                        "（そのカードは正直に「直接の実績なし」になる）")
    p.add_argument("--list-stages", action="store_true")
    args = p.parse_args()

    if args.list_stages:
        for i, name in enumerate(prompts.STAGE_ORDER, 1):
            meta = prompts.STAGES[name]
            deps = ",".join(meta["deps"]) or "-"
            print(f"{i:>2}. [{meta.get('layer', '-'):<9}] {name:<11} → "
                  f"{meta['file']:<24} deps={deps}")
        print("\n層: " + " / ".join(f"{k}={','.join(v)}"
                                    for k, v in prompts.LAYERS.items()))
        return 0

    if args.job_id is None:
        p.error("job_id が必要（--list-stages 以外）")

    job = _load_job(args.job_id)

    from tools.blacklist import guard as blacklist_guard
    blacklist_guard(job.get("company") or "")

    if args.score is not None:
        if not (0 <= args.score <= 100):
            p.error("--score は 0〜100")
        from . import iterate
        facts = (Path(args.facts).read_text(encoding="utf-8") if args.facts
                 else ("" if args.no_facts else _auto_facts(job)))
        try:
            return iterate.handle_score(job, args.score, args.note,
                                        scope=args.scope, facts=facts)
        except LLMUnavailable as e:
            sys.exit(f"\n{e}")

    if args.research_only:
        from . import research
        pdir = pipeline.pack_dir(job)
        print(f"■ {job.get('company')} / {job.get('title')} (job_id={job['id']})")
        sites = [x for x in (args.site or "").split(",") if x.strip()]
        status, res = research.run(job, pdir, force=True, sites=sites or None)
        print(f"  {status}: {len(res.pages)} ページ取得"
              f"（官網: {res.official or '特定できず'}）")
        for pg in res.pages:
            print(f"    [{pg.kind}] {pg.url}")
        for note in res.notes:
            print(f"    備考: {note}")
        print(f"  → {research.raw_path(pdir).relative_to(ROOT)}")
        return 0 if res.ok else 1

    if args.voice_only:
        pdir = pipeline.pack_dir(job)
        if not pdir.exists():
            sys.exit(f"パックが無い: {pdir}（先に python3 -m proposal {job['id']}）")
        print(f"■ {job.get('company')} / {job.get('title')} (job_id={job['id']})")
        results = _run_voice(job, pdir, args)
        return 1 if any(r.status == "failed" for r in results) else 0

    if args.stage and args.layer:
        p.error("--stage と --layer は同時に使えない")
    if args.images_dry_run and not args.images:
        p.error("--images-dry-run は --images と併用すること")
    if args.layer:
        names = list(prompts.LAYERS[args.layer])
    elif args.stage:
        names = [s.strip() for s in args.stage.split(",")]
    else:
        names = list(prompts.STAGE_ORDER)
    for n in names:
        if n not in prompts.STAGES:
            sys.exit(f"未知 stage: {n}（候補: {', '.join(prompts.STAGE_ORDER)}）")

    if args.facts:
        facts = Path(args.facts).read_text(encoding="utf-8")
        facts_src = args.facts
    elif args.no_facts:
        facts, facts_src = "", "（--no-facts）"
    else:
        facts = _auto_facts(job)
        facts_src = "自動探索で発見" if facts else "見つからず（全て仮説扱い）"

    n_fw = len(frameworks.load())
    if n_fw == 0:
        print("⚠ 方法論庫が未構築。先に構築すると提案の型が本人の実践に固定される:")
        print("    python3 -m proposal.frameworks_build\n")

    print(f"■ {job.get('company')} / {job.get('title')} (job_id={job['id']})")
    print(f"  出力先: {pipeline.pack_dir(job).relative_to(ROOT)}/")
    print(f"  会社事実: {facts_src} / 方法論庫: {n_fw} 件")
    print(f"  実行 stage: {', '.join(names)}\n")

    try:
        results = pipeline.run(job, names, facts, force=args.force,
                               no_llm=args.no_llm, from_fields=args.from_fields,
                               skip_research=args.skip_research,
                               research_force=args.research_force,
                               refine=args.refine,
                               sites=[x for x in (args.site or "").split(",")
                                      if x.strip()] or None,
                               with_images=args.images,
                               images_dry_run=args.images_dry_run,
                               image_engine=args.image_engine,
                               case_anchor=args.case_anchor)
    except LLMUnavailable as e:
        sys.exit(f"\n{e}")

    print("\n■ 結果")
    bad = 0
    for r in results:
        extra = f"指摘 {len(r.errors)} 件" if r.errors else ""
        print(f"  {r.status:<9} {r.name:<10} 試行 {r.attempts} 回 {extra}")
        if r.status in ("degraded", "failed"):
            bad += 1
    if not args.no_llm:
        total, _ = pipeline.redteam_score(pipeline.pack_dir(job))
        if total is not None:
            print(f"\n  採用側の目での自己採点: {total}/70")
        print(f"\n  → {pipeline.pack_dir(job).relative_to(ROOT)}/README.md")
        print("     先に 09_redteam.md を読むこと（自分の提案の穴が書いてある）")

    if args.iterate and not args.no_llm:
        from . import iterate
        pdir = pipeline.pack_dir(job)
        v = iterate.snapshot(pdir)
        iterate.push(job, pdir, v)
        print(f"  → v{v} 快照・Telegram 推送済み。採点は "
              f"/pscore {job['id']} <0-100> か --score")

    if args.voice and not args.no_llm:
        _run_voice(job, pipeline.pack_dir(job), args)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
