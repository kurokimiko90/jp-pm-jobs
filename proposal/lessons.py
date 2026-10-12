"""教訓庫 — 過去の提案の弱点と面接の実測を、次の提案の prompt へ戻す。

自己進化の中身。素材は 2 つとも**自分が既に持っているもの**で、外部知識は入れない:

  1. `output/proposal/*/09_redteam.md` — 採用側の目で潰した記録。同じ指摘が複数の
     求人で出るなら、それは求人固有ではなく**書き方の癖**。prompt で直せる。
     v1 のパックでは `04_redteam.md` だったので、両方の名前を拾う。
  2. `interview/retros/*.md` — 実際の面接で何を聞かれ、何に詰まったか。

`data/proposal_lessons.yaml` は User Layer（手で直してよい・自動上書きしない）。
`--rebuild` を明示したときだけ作り直す。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml

from ._llm import call

ROOT = Path(__file__).parent.parent
PATH = ROOT / "data" / "proposal_lessons.yaml"
PACKS = ROOT / "output" / "proposal"
RETROS = ROOT / "interview" / "retros"

MAX_LESSONS = 8
RENDER_LIMIT = 6
# 教訓として採るのに必要な最低出現数（1 求人だけの指摘は求人固有かもしれない）
MIN_OCCURRENCE = 2

_PROMPT = """\
以下は、同じ人が複数の求人に対して作った提案について、**採用側の視点で潰した記録**
（紅隊レビュー）と、実際の面接の振り返りです。

# 素材

{material}

# あなたの仕事

求人ごとの個別事情ではなく、**この人の提案の書き方に繰り返し出る癖**を抽出して
ください。次に提案を書くときの指示文へ組み込むためのものです。

ルール:
1. 複数の求人にまたがって出ている指摘だけを採る。1 件しか出ていないものは捨てる。
2. 「もっと具体的に」のような一般論は書かない。**どう書けば直るか**まで書く。
3. 素材に無い問題を想像で足さない。最大 {max_lessons} 件。
4. 各項目に `seen_in`（その指摘が出ていた求人名か振り返りの日付）を必ず付ける。

# 出力形式

JSON のみ（説明文・コードフェンスなし）。

{{"lessons": [
  {{"id": "短い英小文字スラッグ",
    "pattern": "繰り返し出ている癖（1 文）",
    "why_it_hurts": "面接でどう不利になるか（1 文）",
    "instruction": "次の提案を書くときの具体的な指示（です・ます調・1〜2 文）",
    "seen_in": ["求人名または日付", "..."]}}
]}}
"""


def path() -> Path:
    return PATH


def exists() -> bool:
    return PATH.exists()


def load() -> list[dict]:
    if not PATH.exists():
        return []
    try:
        data = yaml.safe_load(PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    items = data.get("lessons") or []
    return [i for i in items if isinstance(i, dict) and i.get("instruction")]


def _redteam_files() -> list[Path]:
    """現行と v1 のパックの紅隊レポート（同じパックで重複しないよう名前で束ねる）。"""
    from . import prompts
    seen: dict[Path, Path] = {}
    for pattern in (f"*/{prompts.STAGES['redteam']['file']}", "*/04_redteam.md"):
        for md in PACKS.glob(pattern):
            seen.setdefault(md.parent, md)
    return [seen[k] for k in sorted(seen)]


def material() -> tuple[str, int]:
    """素材と、拾えた紅隊レポートの数。"""
    parts: list[str] = []
    n_redteam = 0
    for md in _redteam_files():
        pack = md.parent.name
        body = md.read_text(encoding="utf-8")
        # 「1. 致命的な弱点」だけで足りる — 修正案の表まで入れると prompt が膨らむ
        section = body.split("## 2. ")[0]
        parts.append(f"## 紅隊レビュー: {pack}\n{section[:3000]}")
        n_redteam += 1
    for md in sorted(RETROS.glob("*.md"))[-4:]:
        parts.append(f"## 面接の振り返り: {md.stem}\n"
                     f"{md.read_text(encoding='utf-8')[:2000]}")
    return "\n\n".join(parts), n_redteam


def build(*, dry_run: bool = False) -> list[dict]:
    mat, n_redteam = material()
    if n_redteam < MIN_OCCURRENCE:
        print(f"■ 紅隊レポートが {n_redteam} 件しかない。"
              f"{MIN_OCCURRENCE} 件以上たまってから実行すること"
              "（1 件だけでは求人固有か癖か区別できない）")
        return []
    prompt = _PROMPT.format(material=mat, max_lessons=MAX_LESSONS)
    print(f"■ 素材 {len(mat)} 字（紅隊 {n_redteam} 件）")
    if dry_run:
        out = PACKS / "_lessons_prompt.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt, encoding="utf-8")
        print(f"  → prompt を落とした: {out.relative_to(ROOT)}")
        return []

    try:
        text = call(prompt, timeout=420,
                    accept={"minChars": 200, "regex": r"\"lessons\""})
    except Exception as e:
        print(f"  ✗ 抽出に失敗: {str(e)[:120]}")
        return []
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        print("  ✗ JSON を取り出せなかった")
        return []
    try:
        items = json.loads(m.group()).get("lessons") or []
    except Exception:
        print("  ✗ JSON が壊れていた")
        return []

    kept = [i for i in items
            if isinstance(i, dict) and i.get("instruction")
            and len(i.get("seen_in") or []) >= MIN_OCCURRENCE]
    dropped = len(items) - len(kept)
    if dropped:
        print(f"  ✗ 出現が {MIN_OCCURRENCE} 件未満の項目を {dropped} 件捨てた")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(yaml.safe_dump(
        {"version": 1, "generated_at": date.today().isoformat(),
         "source_packs": n_redteam,
         "note": "User Layer。手で直してよい / 自動では上書きしない。"
                 "再構築は python3 -m proposal.evolve --lessons --rebuild",
         "lessons": kept[:MAX_LESSONS]},
        allow_unicode=True, sort_keys=False, width=100), encoding="utf-8")
    print(f"■ 確定 {len(kept)} 件 → {PATH.relative_to(ROOT)}")
    for k in kept:
        print(f"  [{len(k.get('seen_in') or [])} 件で観測] {k.get('pattern')}")
    return kept


def render(limit: int = RENDER_LIMIT) -> str:
    """prompt へ載せる形。過去の失敗を「やるな」ではなく「こう書け」で渡す。"""
    items = load()[:limit]
    if not items:
        return ""
    lines = ["# 過去の提案から学んだこと（同じ失敗を繰り返さない）", ""]
    for i, l in enumerate(items, 1):
        lines.append(f"{i}. {l.get('instruction')}")
        if l.get("why_it_hurts"):
            lines.append(f"   （理由: {l['why_it_hurts']}）")
    return "\n".join(lines)
