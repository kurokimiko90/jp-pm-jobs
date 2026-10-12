"""想定問答音声化 — 逐句音檔 concat 成整包 MP3 + Telegram 推送。

エンジンは 2 つ（`config/tts.yaml` の `voice_engine`）:

- `gpt`（既定）— ChatGPT に話し言葉へ書き直させ、その読み上げ音声を捕捉（tts.gpt_voice）。
  実際に面接で話す速度・間になる代わり、ブラウザ操作が要るので数分かかる。
- `theater` — edge_tts 逐句合成（面接シアターと同じ音檔庫、zero-token・数秒）。
  gpt が失敗したときの受け皿でもある（音檔ゼロで終わるよりは合成音を出す）。

用法:
    python3 -m tools.interview_voice --job-id 123
    python3 -m tools.interview_voice --job-id 123 --engine theater
    python3 -m tools.interview_voice --job-id 123 --no-send   # Telegram 送信をスキップ
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OUT_NAME = "04_qa_audio.mp3"


def concat_audio(files: list[Path], out_path: Path) -> None:
    """ffmpeg concat（re-encode — 混合 codec/取樣率也安全）。"""
    if not files:
        raise ValueError("concat 対象の音檔が 0 件")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in files:
            f.write(f"file '{p.as_posix()}'\n")
        list_path = f.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c:a", "libmp3lame", "-q:a", "4", str(out_path)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat 失敗: {proc.stderr[-400:]}")
    finally:
        Path(list_path).unlink(missing_ok=True)


def generate_theater(pack_dir: Path, out_path: Path, job: dict | None = None) -> bool:
    """theater 資産を確保 → 逐句 concat → out_path。失敗時 False。"""
    from tts.theater import audio_files_in_order, ensure_assets
    try:
        result = ensure_assets(pack_dir, job)
    except (FileNotFoundError, ValueError) as e:
        print(f"  ✗ {e}", file=sys.stderr)
        return False
    files = audio_files_in_order(pack_dir)
    if not files:
        print("  ✗ 音檔が 1 件も生成できませんでした（TTS provider 全滅？）", file=sys.stderr)
        return False
    try:
        concat_audio(files, out_path)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✗ concat 失敗: {e}", file=sys.stderr)
        return False
    print(f"  ✓ {out_path.name}（{result['questions']}問 / {len(files)}句"
          + (f" / PII置換 {result['pii_findings']}句" if result["pii_findings"] else "") + "）")
    return True


def generate_gpt(pack_dir: Path, out_path: Path, limit: int | None = None,
                 force: bool = False) -> bool:
    """ChatGPT の読み上げを 1 問ずつ捕捉 → wav を concat → out_path。失敗時 False。"""
    from tts import gpt_voice
    try:
        res = gpt_voice.generate(pack_dir, limit=limit, force=force)
    except Exception as e:  # noqa: BLE001
        # playwright の Error も requests の例外も RuntimeError ではない。型で
        # 絞ると、そこだけ theater 備援に落ちず prep.py ごと落ちる（実測）
        print(f"  ✗ {type(e).__name__}: {e}", file=sys.stderr)
        return False
    for err in res.errors:
        span = (f"第{err['ordinals'][0]}〜{err['ordinals'][-1]}問"
                if len(err["ordinals"]) > 1 else f"第{err['ordinals'][0]}問")
        print(f"  ✗ {span} 録音失敗 [{err.get('backend')}/{err.get('stage')}]: "
              f"{err.get('error')}", file=sys.stderr)
    if not res.wav_files:
        print("  ✗ 音檔が 1 件も取れませんでした", file=sys.stderr)
        return False
    try:
        concat_audio(res.wav_files, out_path)
    except (RuntimeError, ValueError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  ✗ concat 失敗: {e}", file=sys.stderr)
        return False
    if res.failed and res.failed >= res.done + res.cached:
        # 半分以上が歯抜けの音檔を「成功」として返すと、面接前日に一部しか
        # 入っていない音檔を渡すことになる。合成音でも全問揃う方を選ぶ
        print(f"  ✗ {res.failed}/{res.batches} バッチが録れず — 歯抜けのまま採用しません",
              file=sys.stderr)
        return False
    print(f"  ✓ {out_path.name}（{len(res.wav_files)}問 / 生成 {res.done} / キャッシュ {res.cached}"
          + (f" / 失敗 {res.failed}" if res.failed else "")
          + (f" / 要確認 {res.degraded}" if res.degraded else "") + "）")
    if res.degraded:
        print(f"  ⚠ 骨子・経歴の裏が取れない語が {res.degraded} 問に残っています"
              " — 06_voice_audit.md を確認", file=sys.stderr)
    return True


def generate(pack_dir: Path, out_path: Path, job: dict | None = None, *,
             engine: str | None = None, limit: int | None = None,
             force: bool = False) -> bool:
    """設定のエンジンで音声化。gpt が失敗したら theater へ落ちる（無音で終わらせない）。"""
    from tools import app_config
    engine = engine or app_config.get("tts", "voice_engine", "theater")
    if engine == "gpt":
        if generate_gpt(pack_dir, out_path, limit=limit, force=force):
            return True
        print("  ↓ GPT 音声化に失敗 — 合成音（theater）で代替します", file=sys.stderr)
    return generate_theater(pack_dir, out_path, job)


def main() -> None:
    p = argparse.ArgumentParser(description="想定問答の音声化 + Telegram 送信")
    p.add_argument("--job-id", type=int, required=True)
    p.add_argument("--engine", choices=["gpt", "theater"], default=None,
                   help="既定は config/tts.yaml の voice_engine")
    p.add_argument("--limit", type=int, default=None, help="先頭 N 問だけ（gpt のみ）")
    p.add_argument("--force", action="store_true", help="キャッシュ無視で録り直し（gpt のみ）")
    p.add_argument("--no-send", action="store_true", help="Telegram 送信をスキップ")
    args = p.parse_args()

    from tracker.db import connect
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
    if row is None:
        sys.exit(f"job_id {args.job_id} が DB に見つかりません")
    job = dict(row)

    from tts.theater import find_pack_dir
    pack_dir = find_pack_dir(args.job_id)
    out_path = pack_dir / OUT_NAME
    ok = generate(pack_dir, out_path, job, engine=args.engine,
                  limit=args.limit, force=args.force)
    if ok and not args.no_send:
        from notify import send_audio
        performer = f"{job.get('company') or '{{要確認}}'} / {job['title'][:30]}"
        send_audio(out_path, caption=f"#{job['id']} 想定問答音声", title="想定問答", performer=performer)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
