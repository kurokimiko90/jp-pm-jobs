"""研究層〜思考層 6 stage の md 出力を、日本語インフォグラフィック 1 枚ずつにする。

miko-ws の深度研究配図（`ResearchImageService` / `codexImage.generateImageFromText`）
と同じ発想: 生成済み文書を codex に読ませて 1 枚の情報整理図にする。あちらは
Traditional Chinese の研究報告を対象にするが、こちらの stage 出力は全部日本語
（面接でそのまま見せる資料のため）なので、図の言語・用語も日本語の IT 現場に
合わせる（`prompts.JA_IT_STYLE_RULES` — 各 stage の本文 prompt と同じ規則）。

対象は 2 通り:

- **1 stage 1 枚**（`IMAGE_STAGES` = company/product/persona/hypotheses/main_case/
  plan90）——「提案の骨格」を作る研究層＋思考層。
- **1 ブロック 1 枚**（`CARD_IMAGE_STAGES` = playbook）—— 能力ごとの手引きは
  それぞれ独立した仕事の型なので、8 能力を 1 枚へ潰すとどの能力も読めない密度に
  なる。`generate_cards()` が `###` を数えた枚数だけ呼ぶ（**1 能力 = 1 call**）。

面接層の残り（cards/mapping/redteam/deck）は文書としてそのまま話す／deck が既に
投影用の絵を持つので対象外。

この 6 stage はどれも `needs_profile: False`（`proposal/CLAUDE.md` の設計）なので、
渡す md に本人のプロフィールは最初から入っていない。追加の去識別化は不要。

**呼び出しのタイミング。** `pipeline.run_stage()` が md 生成の直後（初回成功時・
キャッシュ命中時の両方）に呼ぶ（`with_images=True` のとき）。stage 単体の
`python3 -m proposal <id> --images` でも同じ関数を使って既存パックの画像だけ
後追いで埋められる（md が既にあれば同じロジックが動く）。

呼び出しは `proposal._llm.image()` 経由 — miko-ws 指揮中心のみ、外部 fallback なし
（`proposal/CLAUDE.md` の方針と同じ）。

**エンジンは既定 `codex`。** `--image-engine agy`（Antigravity CLI の `generate_image`
＝ Gemini 画像）へ切り替えると同一 prompt で 236s → 20〜36s まで速くなるが、実測で
**本文の小さい日本語が崩れ**（「データ漤備プロセで」）、素材に無い数字と存在しない
企業ロゴを描いた。画像の中身を検査する閘門は無いので、既定は遅くても字が保つ方に
置く。速さが要る下見のときだけ `agy` を明示する。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import prompts, trace
from ._llm import LLMUnavailable, image as llm_image

IMAGE_STAGES = ["company", "product", "persona", "hypotheses", "main_case", "plan90"]

# 「1 stage 1 枚」ではなく「1 ブロック 1 枚」で作る stage。playbook は能力ごとに
# 独立した手引き（表れ方/想定シナリオ/進め方/注意点/思考ロジック/答えの論点）で、
# 8 能力を 1 枚へ潰すと**どの能力の話も読めない密度**になる。面接直前に 1 能力
# ずつ確認する使い方にも、1 枚 1 能力の方が合う。
CARD_IMAGE_STAGES = ["playbook"]

MD_MAX_CHARS = 8000  # 生図 prompt に載せる元文書の上限
CARD_MAX_CHARS = 2600  # 1 枚 1 能力なので元文書より小さくてよい
TIMEOUT = 560

# 既定の生図エンジン（指揮中心へ渡す第一希望）。備援順は指揮中心が持つ。
DEFAULT_ENGINE = "codex"

_STYLE_BLOCK = """\
- 縦向き 4:5、上から下へ読む順序がはっきり分かるレイアウト
- 見出し・小見出し・本文の階層を色と余白で表現する（装飾的な 3D レンダリングや
  ヒーロー画像は禁止。あくまで情報整理図）
- 言語は日本語のみ（英語見出し・簡体字を混在させない）
- 数字・固有名詞は元の文書にある表記をそのまま使う（新しい数字を作らない）
"""


def build_prompt(stage: str, md_content: str) -> str:
    title = prompts.STAGES[stage]["title"]
    body = md_content[:MD_MAX_CHARS]
    return f"""以下は提案資料の1節（{title}）です。この内容を1枚の日本語
インフォグラフィック（情報整理図）にしてください。

内容要件：
- 元の文書の要点・構造・結論を抽出する（本文の言い換えではなく、図として要約する）
- 見出しに「{title}」を使う
- 図中の文言は日本の IT / プロダクト組織で実際に使われる自然な表現にする
  （中国語を漢字だけ置き換えた表現・翻訳調は禁止）

{prompts.JA_IT_STYLE_RULES}

{_STYLE_BLOCK}

--- 元の文書 ---
{body}
"""


# 実測（4407 の 1 枚目・codex）で踏んだ 2 つを潰した版:
#   ①「注意点は流れの脇に」と書くと版面が横へ引っ張られ、**正方形**で出力されて
#     最下段の思考ロジックが切れた（他 6 stage の図は 1003x1568 等の縦長なので、
#     エンジンが縦長を出せないわけではない — こちらの指示が横を示唆していた）
#   ② アイコンに小さな説明文字を添えると崩れる（「契約」→「答約にご参約」、
#     「指標」→「指樏」）。小さい文字を作らせないのが唯一効く対策
#   ③「つまずきを各ステップの右側に添える」と「注意点の区画」を両方書いたら、
#     **注意点が 2 箇所に出た上に数を水増しされた** — 元の注意点は 2 件なのに
#     ステップ 5 件分の枠を作り、埋めるために 2 件を捏造した（「先行指標が成果と
#     つながるか曖昧」等、md に無い）。画像の中身を検査する閘門は無いので、
#     **枠の数を先に決めさせない**のが唯一の防ぎ方
_CARD_STYLE_BLOCK = """\
- **縦長**（幅より高さが 1.4 倍以上）。正方形にしない — 正方形だと最下段が切れる
- 上から下へ「場面 → 進め方 → つまずき → 判断」の 4 区画。**4 区画すべてを
  画面内に収める**。最下段の「思考ロジック」が途中で切れたら失敗
- 進め方の 5 ステップは番号付きの縦の流れにする（1 ステップ 1 行、矢印でつなぐ）
- **つまずきは独立した 1 区画にまとめ、ステップの脇には置かない。**
  件数は元の文書にある数のまま（ステップ数に合わせて水増ししない）
- **アイコンに小さな説明文字を付けない。** 図中の文字は本文の見出しと各項目だけ
  にして、読める大きさを保つ（小さい文字は崩れる）
- 装飾的な 3D レンダリングや人物イラストは禁止。あくまで情報整理図
- 言語は日本語のみ（英語見出し・簡体字を混在させない）
- 数字・固有名詞は元の文書にある表記をそのまま使う（新しい数字を作らない）
"""


def build_card_prompt(capability: str, card_body: str) -> str:
    """能力カード 1 枚分の生図 prompt。

    stage 全体の図（`build_prompt`）が「節の要点を 1 枚にまとめる」のに対し、
    こちらは**1 つの能力の仕事の流れ**を図にする。元が既に 6 項目の構造を
    持っているので、図側でも構造をそのまま活かす（要約し直すと手順が消える）。
    """
    body = card_body[:CARD_MAX_CHARS]
    return f"""以下は面接準備資料の「能力プレイブック」の 1 枚
（能力: {capability}）です。この 1 つの能力について、実務でどう仕事が動くかを
1 枚の日本語インフォグラフィック（情報整理図）にしてください。

内容要件：
- 見出しに能力名「{capability}」を使う
- 「想定シナリオ（どんな場面か）」「進め方（手順）」「注意点（つまずき）」
  「思考ロジック（何を先に決めるか）」の 4 区画を、この順で**すべて**入れる
- 手順は元の文書のステップ数・順序を変えない
- 各項目は要約して短くする（本文をそのまま書き写すと入りきらない）
- **元の文書に無い項目を足さない。** 枠が余っても埋めない — 図に新しい
  「注意点」「ステップ」「観点」を発明すると、この資料は使えなくなる
- 図中の文言は日本の IT / プロダクト組織で実際に使われる自然な表現にする
  （中国語を漢字だけ置き換えた表現・翻訳調は禁止）

{prompts.JA_IT_STYLE_RULES}

{_CARD_STYLE_BLOCK}

--- 元の文書 ---
{body}
"""


def cards_of(md_content: str) -> list[tuple[str, str]]:
    """playbook の md を (能力名, カード本文) へ割る。

    区切りは `### `（`gates._gate_i` と同じ規約）。見出し行そのものは本文にも
    残す — 図の見出しに能力名を使わせるので、素材から落とすと参照できない。
    """
    out: list[tuple[str, str]] = []
    for blk in re.split(r"^###\s+", md_content, flags=re.M)[1:]:
        lines = blk.splitlines()
        if not lines or not lines[0].strip():
            continue
        out.append((lines[0].strip(), "### " + blk.rstrip()))
    return out


def image_path(pdir: Path, stage: str) -> Path:
    md_file = prompts.STAGES[stage]["file"]
    return pdir / f"{Path(md_file).stem}.png"


def card_image_path(pdir: Path, stage: str, index: int) -> Path:
    """`11_capability_playbook_01.png` … 連番のみ。

    能力名をファイル名へ入れると、人物像の書き換えで能力名が変わったときに
    **古い名前の PNG が残って**どれが今のカードか分からなくなる。連番なら
    枚数が減ったときの余りだけを機械で特定できる（`_prune_card_images`）。
    中身がどの能力かは図の見出しに書かれている。
    """
    stem = Path(prompts.STAGES[stage]["file"]).stem
    return pdir / f"{stem}_{index:02d}.png"


def card_prompt_dump_path(pdir: Path, stage: str, index: int) -> Path:
    return pdir / "_prompts" / f"{stage}.image.{index:02d}.prompt.md"


# 縦横比の閘門。prompt で「縦長」と指示しても実測 8 枚中 1 枚が正方形で返り、
# その 1 枚は下 2 区画（注意点・思考ロジック）が丸ごと切れていた。**比率は機械が
# 判定できる性質**なので、運任せにせず測って作り直す（図の中身は判定できないが、
# 「正方形＝下が切れている」は実測で対応が付いている）。
CARD_MIN_ASPECT = 1.4    # 高さ ÷ 幅。prompt の指示と同じ値
CARD_ASPECT_RETRY = 1    # 作り直しは 1 回だけ（それ以上は課金に見合わない）


def portrait_ok(path: Path) -> bool | None:
    """縦長として十分か。判定できないときは None（Pillow 未導入など）。

    None は「合格」ではなく「測れない」。呼び出し側は None のとき作り直しを
    しない — 測れないのに作り直すと、同じものを課金しながら回し続ける。
    """
    try:
        from PIL import Image           # 任意依存（研究層の playwright と同じ扱い）
    except Exception:
        return None
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    return h >= w * CARD_MIN_ASPECT


def _prune_card_images(pdir: Path, stage: str, n_cards: int) -> list[str]:
    """能力が減ったときに余った連番 PNG を消す（対応するカードが無い＝確実に古い）。"""
    stem = Path(prompts.STAGES[stage]["file"]).stem
    removed = []
    for p in sorted(pdir.glob(f"{stem}_[0-9][0-9].png")):
        try:
            idx = int(p.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if idx > n_cards:
            p.unlink()
            removed.append(p.name)
    return removed


def prompt_dump_path(pdir: Path, stage: str) -> Path:
    return pdir / "_prompts" / f"{stage}.image.prompt.md"


def generate(job: dict, pdir: Path, stage: str, *, force: bool = False,
            no_llm: bool = False, engine: str | None = None) -> Path:
    """1 stage 分の PNG を生成する。md が無ければ FileNotFoundError。

    no_llm=True: LLM を呼ばず、組み立てた prompt を `_prompts/{stage}.image.prompt.md`
    に落として終わる（本文 stage の `--no-llm` と同じ dry-run 規約）。戻り値はその
    prompt ファイルのパス。

    engine: None なら DEFAULT_ENGINE（agy）。"codex" / "gemini1" で上書き可。
    """
    meta = prompts.STAGES[stage]
    md_path = pdir / meta["file"]
    if not md_path.exists():
        raise FileNotFoundError(f"{meta['file']} が無い（先に stage を生成すること）")
    out_path = image_path(pdir, stage)

    if no_llm:
        prompt = build_prompt(stage, md_path.read_text(encoding="utf-8"))
        dump = prompt_dump_path(pdir, stage)
        dump.parent.mkdir(parents=True, exist_ok=True)
        dump.write_text(prompt, encoding="utf-8")
        return dump

    if out_path.exists() and not force:
        return out_path

    md_content = md_path.read_text(encoding="utf-8")
    prompt = build_prompt(stage, md_content)
    eng = engine or DEFAULT_ENGINE
    t0 = time.time()
    try:
        llm_image(prompt, out_path, timeout=TIMEOUT, engine=eng)
    except LLMUnavailable:
        raise
    except Exception as e:
        trace.record(pdir, job=job, stage=f"image_{stage}", attempt=1, prompt=prompt,
                     output="", meta={"engine_requested": eng},
                     gate="error", errors=[f"[{eng}] {e}"],
                     elapsed=time.time() - t0, prompt_version=prompts.PROMPT_VERSION)
        raise
    trace.record(pdir, job=job, stage=f"image_{stage}", attempt=1, prompt=prompt,
                 output=f"[image:{eng}] {out_path.name}",
                 meta={"engine_requested": eng}, gate="pass",
                 elapsed=time.time() - t0, prompt_version=prompts.PROMPT_VERSION)
    return out_path


def generate_cards(job: dict, pdir: Path, stage: str = "playbook", *,
                   force: bool = False, no_llm: bool = False,
                   engine: str | None = None,
                   indices: list[int] | None = None) -> list[Path]:
    """能力カード 1 枚ごとに PNG を作る（playbook 用）。

    `generate()` が「1 stage 1 枚」なのに対し、こちらは md 内の `###` ブロックを
    数えた枚数だけ呼ぶ。**1 能力 = 1 call** なので、8 能力なら 8 call かかる —
    呼び出し側は枚数を人へ知らせてから回すこと。

    1 枚失敗しても残りは作る（例外は握って次へ）。`LLMUnavailable` だけは
    指揮中心自体が落ちているので中断する（本文 stage と同じ規約）。

    indices: 1 始まりの連番で対象を絞る。**版面の下見**（1 枚だけ出して構成を
    確かめてから残りを回す）と、md の 1 枚だけ直したときの作り直しに使う。
    絞ったときは古い連番の掃除をしない — 全体を見ていないので「余り」を
    判定できない（3 枚目だけ作って 4 枚目以降を消したら事故）。
    """
    meta = prompts.STAGES[stage]
    md_path = pdir / meta["file"]
    if not md_path.exists():
        raise FileNotFoundError(f"{meta['file']} が無い（先に stage を生成すること）")
    cards = cards_of(md_path.read_text(encoding="utf-8"))
    if not cards:
        raise ValueError(f"{meta['file']} に `###` の能力カードが 1 枚も無い")

    out: list[Path] = []
    for i, (name, body) in enumerate(cards, 1):
        if indices and i not in indices:
            continue
        prompt = build_card_prompt(name, body)
        if no_llm:
            dump = card_prompt_dump_path(pdir, stage, i)
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.write_text(prompt, encoding="utf-8")
            out.append(dump)
            continue
        dest = card_image_path(pdir, stage, i)
        if dest.exists() and not force:
            out.append(dest)
            continue
        eng = engine or DEFAULT_ENGINE
        made = False
        for attempt in range(1, CARD_ASPECT_RETRY + 2):
            t0 = time.time()
            try:
                llm_image(prompt, dest, timeout=TIMEOUT, engine=eng)
            except LLMUnavailable:
                raise
            except Exception as e:
                trace.record(pdir, job=job, stage=f"image_{stage}_{i:02d}",
                             attempt=attempt, prompt=prompt, output="",
                             meta={"engine_requested": eng, "capability": name},
                             gate="error", errors=[f"[{eng}] {e}"],
                             elapsed=time.time() - t0,
                             prompt_version=prompts.PROMPT_VERSION)
                print(f"      ✗ {i:02d} {name[:24]}: {str(e)[:90]}")
                break
            ok = portrait_ok(dest)
            trace.record(pdir, job=job, stage=f"image_{stage}_{i:02d}",
                         attempt=attempt, prompt=prompt,
                         output=f"[image:{eng}] {dest.name}",
                         meta={"engine_requested": eng, "capability": name,
                               "portrait_ok": ok},
                         gate="pass" if ok is not False else "fail",
                         errors=[] if ok is not False else ["[比率] 正方形で出力された"],
                         elapsed=time.time() - t0,
                         prompt_version=prompts.PROMPT_VERSION)
            if ok is not False:
                print(f"      ✓ {i:02d} {name[:24]} → {dest.name}")
                made = True
                break
            if attempt <= CARD_ASPECT_RETRY:
                print(f"      ↻ {i:02d} {name[:24]}: 正方形（下段が切れる）— 作り直す")
            else:
                print(f"      ⚠ {i:02d} {name[:24]}: 正方形のまま。"
                      "下段が切れている可能性 — 目視すること")
                made = True
        if made:
            out.append(dest)

    if not no_llm and not indices:
        for gone in _prune_card_images(pdir, stage, len(cards)):
            print(f"      🗑 能力が減ったため削除: {gone}")
    return out


def generate_all(job: dict, pdir: Path, stages: list[str] | None = None, *,
                 force: bool = False, no_llm: bool = False,
                 engine: str | None = None) -> dict[str, str]:
    """存在する md から画像を作る。無い stage はスキップし理由を残す（全体は止めない）。"""
    results: dict[str, str] = {}
    for stage in (stages or IMAGE_STAGES + CARD_IMAGE_STAGES):
        try:
            if stage in CARD_IMAGE_STAGES:
                paths = generate_cards(job, pdir, stage, force=force,
                                       no_llm=no_llm, engine=engine)
                mark = "…" if no_llm else "✓"
                results[stage] = f"{mark} {len(paths)} 枚（能力ごと）"
                continue
            path = generate(job, pdir, stage, force=force, no_llm=no_llm,
                            engine=engine)
            mark = "…" if no_llm else "✓"
            results[stage] = f"{mark} {path.name}"
        except FileNotFoundError as e:
            results[stage] = f"— スキップ（{e}）"
        except LLMUnavailable:
            raise
        except Exception as e:
            results[stage] = f"✗ 失敗: {e}"
    return results
