"""提案パックの音声化 — 面接パックと同じ「ChatGPT に話させて捕捉する」方式。

**全文は読み上げない。** パック 13 文書は約 3.9 万字あり、素直に音声化すると
2.5 時間になって誰も聞かない。しかも研究層・思考層の本体は表・層図・KPI ツリーで、
耳では構造が入らない（そこは `--images` の PNG が担当する）。

音声が担うのは 1 つだけ — **自分が口に出すものを、移動中に練習できる形にする**。
それはパックの中で一番小さい部分で、3 本に収まる:

| track | 素材 | 何のために聞くか | 長さ（theater 実測・4407） |
|---|---|---|---|
| `pitch` | `13_pitch.md` | 冒頭 5 分の主張そのもの。丸ごと話せる状態にする | 未計測（原稿 1500 字 ≒ 5 分） |
| `qa`    | `09_redteam.md` §3 | 提案を出した直後に必ず来る追問への応答 | 91 秒（5 問） |
| `cards` | `07_capability_cards.md` | 能力ごとの言い方を面接前日に一周する | 328 秒（8 枚） |

⚠ 実測は edge_tts の直読み（`--voice-engine theater`）での値。gpt は話し言葉へ
書き直してから読むので、これより長くなる（まだ計測していない）。

対象外にした文書と理由: 01〜04（研究・分析。目で読む構造）/ 06 90 日計画
（時系列の表）/ 08 経験マッピング（表が本体。§5「なぜ私か」は pitch に合流済み）/
10 deck（絵）/ 11 プレイブック（最長 6947 字だが「仕事の型」の参考書で、口に
出すものではない。能力ごとの PNG が既にある）/ 12 レビュー（機械の検査結果）。

PII: 素材は外部（ChatGPT）へ出るので、`tts.gpt_voice` 側で送信前に
`pii_gate.scrub_for_external()` → `redact()` の二段閘門を通る（面接パックと同じ）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import prompts

ROOT = Path(__file__).resolve().parents[1]

# 音檔は pack 直下に平置き（一目で見つかる）。監査は _voice/ にまとめる
AUDIO_PREFIX = "14_voice_"
AUDIT_FILE = "15_voice_audit.md"
AUDIT_DIR = "_voice"


@dataclass
class Segment:
    """1 音声単位。`tts.gpt_voice` が期待する形（question / conclusion / points）。

    名前が「質問」なのは面接パック由来だが、実体は「ChatGPT へ渡す 1 塊の骨子」。
    pitch のように問いでない素材では、節の見出しがここに入る。
    """
    question: str
    conclusion: str
    points: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 素材の抽出

# 丸数字は上流 prompt が手順の番号に使っている（「①〜を置く ②〜を明記する」）。
# 音声では読めない（合成音は「まるいち」と読むか黙って落とす）ので、聞いて順番が
# 分かる日本語へ開いてから渡す
_CIRCLED = {c: f"{i}つ目に、" for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩", 1)}


def _clean(text: str) -> str:
    """行内の markdown 記号を落とす。声に出せない記号は音声化で必ず事故になる。"""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"\{\{.*?\}\}", "", text)
    for mark, spoken in _CIRCLED.items():
        text = text.replace(mark, spoken)
    return text.strip()


def _blocks(md: str, marker: str = "###") -> list[tuple[str, str]]:
    """`### 見出し` 単位で (見出し, 本文) に割る。"""
    pat = re.compile(rf"^{re.escape(marker)}\s+(.+?)\s*$", re.MULTILINE)
    hits = list(pat.finditer(md))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(md)
        out.append((_clean(m.group(1)), md[m.end():end].strip()))
    return out


def _section(md: str, title_re: str) -> str:
    """`## <title>` 節の本文を返す（次の `##` まで）。"""
    m = re.search(rf"^##\s*{title_re}.*$", md, re.MULTILINE)
    if not m:
        return ""
    nxt = re.compile(r"^##\s+", re.MULTILINE).search(md, m.end())
    return md[m.end():nxt.start() if nxt else len(md)].strip()


def _bullet_value(body: str, label: str) -> str:
    """`- **ラベル**: 値` を 1 つ取り出す（複数行に折り返していても拾う）。"""
    pat = re.compile(rf"^\s*[-*]\s*\*\*{re.escape(label)}\*\*\s*[:：]\s*(.*)$",
                     re.MULTILINE)
    m = pat.search(body)
    if not m:
        return ""
    rest = body[m.end():]
    lines = [m.group(1)]
    for line in rest.splitlines():
        # 次の項目・見出しに当たるまでが 1 項目（折り返し行を落とさない）
        if re.match(r"^\s*[-*]\s*\*\*|^#{1,4}\s", line):
            break
        if line.strip():
            lines.append(line.strip())
    return _clean(" ".join(lines))


def extract_pitch(md: str) -> list[Segment]:
    """13_pitch.md → 節ごとの Segment。原稿は既に口語なので整形だけさせる。"""
    out: list[Segment] = []
    for title, body in _blocks(md, "##"):
        text = _clean("\n".join(l for l in body.splitlines() if l.strip()))
        if text:
            out.append(Segment(question=title, conclusion=text))
    return out


# 紅隊 §3 の書き方はパック間で揺れる（prompt が形式を固定していないため）。
# 実測 2 通り — どちらも同じ意味なので、片方だけ拾う parser は静かに 0 件を返す:
#   4407: `### 弱点1への質問` + `**質問**：…` + `**応答案**：…`
#   4752: 見出し無し + `**質問：…**`（コロンが太字の内側）+ `応答案：…`（太字なし）
# ラベルの前後に `**` が付くかどうかを問わない形で拾う
_LABEL_TMPL = r"^\s*\*{{0,2}}\s*{label}\s*\*{{0,2}}\s*[:：]\s*(.*)$"


def _label_hits(text: str, label: str) -> list[re.Match]:
    return list(re.finditer(_LABEL_TMPL.format(label=re.escape(label)), text,
                            re.MULTILINE))


def _strip_emphasis(text: str) -> str:
    # 硬改行（行末 2 スペース）を挟んで連結すると空白が重なる。読み上げには
    # 影響しないが、監査 md と manifest が読みにくくなる
    return re.sub(r"\s{2,}", " ", _clean(text)).strip("*").strip("「」 ").strip()


def extract_redteam_qa(md: str) -> list[Segment]:
    """09_redteam.md §3「想定質問と応答案」→ 質問と応答案の対。

    紅隊が自分で挙げた弱点への追問なので、**面接で最も痛いところ**が並ぶ。
    ここが答えられれば提案は持ち込める。

    質問マークの位置で本文を割り、各区間の中の応答案を拾う（見出しの有無・
    太字の付き方に依存しない）。応答案は複数行に折り返すので区間末まで取る。
    """
    body = _section(md, r"[0-9０-９]+\.?\s*想定質問")
    if not body:
        return []
    qs = _label_hits(body, "質問")
    out: list[Segment] = []
    for i, m in enumerate(qs):
        end = qs[i + 1].start() if i + 1 < len(qs) else len(body)
        question = _strip_emphasis(m.group(1))
        region = body[m.end():end]
        answers = _label_hits(region, "応答案")
        if not (question and answers):
            continue
        tail = region[answers[0].end():]
        lines = [answers[0].group(1)]
        for line in tail.splitlines():
            if re.match(r"^\s*#{1,4}\s|^\s*\*{0,2}\s*(質問|指摘)\s*[:：]", line):
                break
            if line.strip():
                lines.append(line.strip())
        answer = _strip_emphasis(" ".join(lines))
        if question and answer:
            out.append(Segment(question=question, conclusion=answer))
    return out


def extract_cards(md: str) -> list[Segment]:
    """07_capability_cards.md → 能力ごとの Segment。

    「主提案との関係」は入れない — 音声で聞くのは能力の話で、提案との接続は
    pitch が担当する（同じ内容を 2 本の音声で聞くと、どちらも印象に残らない）。
    """
    out: list[Segment] = []
    for title, block in _blocks(md, "###"):
        scene = _bullet_value(block, "この会社での出番")
        todo = _bullet_value(block, "具体的にやること")
        fact = _bullet_value(block, "本人の実績")
        points = [p for p in (todo, fact) if p]
        if title and (scene or points):
            out.append(Segment(question=f"{title}について話してください",
                               conclusion=scene, points=points))
    return out


# ---------------------------------------------------------------- track 定義

TRACKS: dict[str, dict] = {
    "pitch": {
        "source": "pitch",           # prompts.STAGES のキー（ファイル名の単一の出所）
        "extract": extract_pitch,
        "title": "5 分ピッチ",
        "primer": "script",          # 原稿整形（内容を作り変えさせない）
        "caption": "冒頭 5 分。これだけは丸ごと話せる状態にする",
    },
    "qa": {
        "source": "redteam",
        "extract": extract_redteam_qa,
        "title": "紅隊の追問",
        "primer": "qa",
        "caption": "提案を出した直後に来る追問。痛いところから順に",
    },
    "cards": {
        "source": "cards",
        "extract": extract_cards,
        "title": "能力カード",
        "primer": "qa",
        "caption": "能力ごとの言い方。面接前日に一周する",
    },
}

DEFAULT_TRACKS = ("pitch", "qa", "cards")


def audio_path(pdir: Path, track: str) -> Path:
    return pdir / f"{AUDIO_PREFIX}{track}.mp3"


def source_path(pdir: Path, track: str) -> Path:
    return pdir / prompts.STAGES[TRACKS[track]["source"]]["file"]


def segments_for(pdir: Path, track: str) -> list[Segment]:
    src = source_path(pdir, track)
    if not src.exists():
        raise FileNotFoundError(
            f"{src.name} が無い（先に python3 -m proposal <id> "
            f"--stage {TRACKS[track]['source']}）")
    return TRACKS[track]["extract"](src.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 録音

def _record(pdir: Path, track: str, segments: list[Segment], *,
            force: bool, limit: int | None, log=print):
    """音声化の唯一の呼び出し口。

    ⚠ **ここが差し替え点。** GPT 音声化は将来 miko-ws の LLM 指揮中心へ移り、
    統一の開放機能として呼べるようになる予定。そのときに触るのはこの関数だけで、
    素材の抽出（上）と Telegram 送信（下）は影響を受けない。
    """
    from tts import gpt_voice

    primer = (gpt_voice.SCRIPT_PRIMER if TRACKS[track]["primer"] == "script"
              else gpt_voice.SYSTEM_PRIMER)
    return gpt_voice.generate_items(
        pdir, segments, track=track, primer=primer,
        audit_path=pdir / AUDIT_DIR / f"{track}.md",
        force=force, limit=limit, log=log)


def _concat(files: list[Path], out: Path) -> None:
    from tools.interview_voice import concat_audio
    concat_audio(files, out)


def _theater_fallback(pdir: Path, track: str, segments: list[Segment],
                      out: Path, log=print) -> bool:
    """gpt が失敗したときの受け皿 — edge_tts で合成音を作る。

    面接パック（`tools.interview_voice.generate`）と同じ考え方: 音檔ゼロで
    面接前日を迎えるくらいなら、機械音でも内容が耳に入る方がいい。
    """
    from tts import synthesize
    from tools.pii_gate import scrub_for_external
    from tools.redact import redact

    tmp = pdir / AUDIT_DIR / f"_{track}_theater"
    tmp.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, seg in enumerate(segments, 1):
        text = "。".join(x for x in [seg.question, seg.conclusion, *seg.points] if x)
        clean, _ = redact(scrub_for_external(text)[0])
        try:
            res = synthesize(clean, lang="ja")
        except Exception as e:  # noqa: BLE001
            log(f"    ✗ {i} 件目の合成に失敗: {str(e)[:80]}")
            continue
        p = tmp / f"{i:03d}.{res.audio_format}"
        p.write_bytes(res.audio_bytes)
        parts.append(p)
    if not parts:
        return False
    try:
        _concat(parts, out)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        log(f"    ✗ concat 失敗: {e}")
        return False
    return True


@dataclass
class TrackResult:
    track: str
    status: str          # ok / degraded（theater 代替）/ failed / skipped
    segments: int
    path: Path | None = None
    degraded: int = 0
    note: str = ""


def run_track(pdir: Path, track: str, *, force: bool = False,
              limit: int | None = None, engine: str = "gpt",
              log=print) -> TrackResult:
    """1 track を音声化する。gpt が失敗したら theater で代替する。"""
    try:
        segments = segments_for(pdir, track)
    except FileNotFoundError as e:
        log(f"  ⏭  {track}: {e}")
        return TrackResult(track, "skipped", 0, note=str(e))
    if not segments:
        note = f"{source_path(pdir, track).name} から素材を 1 件も取り出せなかった"
        log(f"  ✗ {track}: {note}")
        return TrackResult(track, "failed", 0, note=note)

    out = audio_path(pdir, track)
    log(f"  ♪ {track}（{TRACKS[track]['title']}）— 素材 {len(segments)} 件")
    if engine == "gpt":
        try:
            res = _record(pdir, track, segments, force=force, limit=limit, log=log)
            for err in res.errors:      # 端末に流れて消えないよう理由をここでも出す
                span = (f"第{err['ordinals'][0]}〜{err['ordinals'][-1]}問"
                        if len(err["ordinals"]) > 1 else f"第{err['ordinals'][0]}問")
                log(f"    ✗ {span} [{err.get('backend')}/{err.get('stage')}]: "
                    f"{err.get('error')}")
            if res.failed and res.failed >= res.done + res.cached:
                # 歯抜けの音檔を採用すると、面接前日に一部しか入っていない
                # ファイルを渡すことになる。合成音でも全部揃う方を選ぶ
                log(f"  ✗ {track}: {res.failed}/{res.batches} バッチが録れず"
                    " — 歯抜けのまま採用しません")
            elif res.wav_files:
                _concat(res.wav_files, out)
                status = "degraded" if res.degraded else "ok"
                log(f"  {'⚠' if res.degraded else '✓'} {track} → {out.name}"
                    + (f"（要確認 {res.degraded} 件 — {AUDIT_FILE}）"
                       if res.degraded else ""))
                return TrackResult(track, status, len(segments), out, res.degraded)
            else:
                log(f"  ✗ {track}: 音檔が 1 件も取れなかった")
        except Exception as e:  # noqa: BLE001
            # 型名を落とすと、str() が空の playwright / requests 例外が
            # 「✗ qa: 」だけになって原因が消える
            log(f"  ✗ {track}: {type(e).__name__}: {str(e)[:160]}")
        log(f"  ↓ {track}: GPT 音声化に失敗 — 合成音（theater）で代替します")

    if _theater_fallback(pdir, track, segments, out, log=log):
        log(f"  ⚠ {track} → {out.name}（合成音）")
        return TrackResult(track, "degraded", len(segments), out,
                           note="theater 代替")
    return TrackResult(track, "failed", len(segments), note="音檔を作れなかった")


def write_audit(pdir: Path, results: list[TrackResult]) -> Path:
    """track ごとの監査を 1 枚にまとめる（読むのは面接前の 1 回だけなので）。"""
    lines = ["# 音声原稿の監査", "",
             "ChatGPT に話し言葉へ整えさせた原稿が、**渡した骨子と本人の経歴の"
             "範囲に収まっているか**の機械チェック。⚠ の項目は本番で口に出す前に"
             "裏を取ること。", "",
             "| track | 状態 | 素材 | 要確認 | 音檔 |", "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.track} | {r.status} | {r.segments} 件 | "
                     f"{r.degraded or '—'} | "
                     f"{r.path.name if r.path else '—'} |")
    for r in results:
        detail = pdir / AUDIT_DIR / f"{r.track}.md"
        if detail.exists():
            body = detail.read_text(encoding="utf-8")
            body = re.sub(r"^# .*$", "", body, count=1, flags=re.MULTILINE)
            lines += ["", f"## {r.track} — {TRACKS[r.track]['title']}", "",
                      body.strip()]
    path = pdir / AUDIT_FILE
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def push(job: dict, pdir: Path, results: list[TrackResult]) -> None:
    """音檔を Telegram へ。**監査 md と本文 md は送らない**（音声だけ届けば足りる）。"""
    from notify import send_audio

    performer = f"{job.get('company') or '要確認'} / {(job.get('title') or '')[:30]}"
    for r in results:
        if not (r.path and r.path.exists()):
            continue
        mark = "⚠ " if r.status == "degraded" else ""
        send_audio(r.path,
                   caption=f"{mark}#{job['id']} {TRACKS[r.track]['title']} — "
                           f"{TRACKS[r.track]['caption']}",
                   title=TRACKS[r.track]["title"], performer=performer)


def run(job: dict, pdir: Path, tracks: list[str] | None = None, *,
        force: bool = False, limit: int | None = None, engine: str = "gpt",
        send: bool = True, log=print) -> list[TrackResult]:
    names = list(tracks or DEFAULT_TRACKS)
    for n in names:
        if n not in TRACKS:
            raise ValueError(f"未知 track: {n}（候補: {', '.join(TRACKS)}）")
    results = [run_track(pdir, n, force=force, limit=limit, engine=engine, log=log)
               for n in names]
    write_audit(pdir, results)
    if send:
        push(job, pdir, results)
    return results


def main() -> int:
    import argparse

    from tracker.db import connect

    p = argparse.ArgumentParser(
        prog="python3 -m proposal.voice",
        description="提案パックの音声化（ChatGPT 読み上げ捕捉）")
    p.add_argument("job_id", type=int)
    p.add_argument("--track", default="", help=f"カンマ区切り。既定は全部"
                                               f"（{','.join(DEFAULT_TRACKS)}）")
    p.add_argument("--engine", choices=("gpt", "theater"), default="gpt")
    p.add_argument("--force", action="store_true", help="キャッシュ無視で録り直し")
    p.add_argument("--limit", type=int, default=None, help="先頭 N 件だけ")
    p.add_argument("--no-send", action="store_true", help="Telegram 送信をスキップ")
    args = p.parse_args()

    from . import pipeline

    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {args.job_id} が DB に見つかりません")
    job = dict(row)
    pdir = pipeline.pack_dir(job)
    if not pdir.exists():
        sys.exit(f"パックが無い: {pdir}（先に python3 -m proposal {args.job_id}）")

    tracks = [t.strip() for t in args.track.split(",") if t.strip()] or None
    results = run(job, pdir, tracks, force=args.force, limit=args.limit,
                  engine=args.engine, send=not args.no_send)
    print(f"\n  → {(pdir / AUDIT_FILE).relative_to(ROOT)}")
    return 1 if any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
