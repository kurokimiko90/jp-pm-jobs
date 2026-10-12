"""方法論庫の自動構築 — 3 モデル草案 → 機械採点 → 合併裁決。

    python3 -m proposal.frameworks_build [--dry-run] [--rebuild]

なぜ 3 モデルか: 同じ素材を読ませても抽出の切り口はモデルごとに違う。3 票そろった
方法論は高信度、割れた部分だけが裁決の仕事になる（合議で「一番うまい文章」を選ぶ
のではない — それをやると顧問腔が勝つ）。

**「最優」の判定は機械が先に決める。** 各項目の evidence.quote が素材に実在するか
（錨定）を機械で測り、実在しない項目は裁決モデルに見せる前に捨てる。裁決モデルの
仕事は残った項目の重複統合と言い回しの調整だけで、採否の権限は持たない。

素材（外へ出るのは去識別化済みのみ）:
  data/candidate_profile.yaml（deid 白名單）/ interview/question-bank/core/*.md
  interview/retros/*.md（あれば）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import context, frameworks
from ._llm import LLMUnavailable, call
from .anchor import QUOTE_WINDOW, anchored as anchor_rate, norm as anchor_norm

ROOT = Path(__file__).parent.parent
RETRO_DIR = ROOT / "interview" / "retros"

# 草案を書かせる 3 brain（provider が別々になるよう選ぶ）と、合併を担当する 1 brain
DRAFT_ENGINES = ("codex", "antigravity", "gemini1")
JUDGE_ENGINE = "codex"

MAX_ITEMS = 10          # 1 モデルあたりの抽出上限
MIN_ANCHOR = 0.5        # これ未満の錨定率の項目は裁決前に捨てる

# 「誰でも書ける顧問腔」検出 — 本人の型ではなく一般論を出してきた印
_GENERIC_NAMES = (
    "PDCA", "SWOT", "3C分析", "4P", "5フォース", "ロジックツリー", "MECE",
    "PEST", "バリューチェーン", "デザイン思考", "リーンスタートアップ",
    "アジャイル開発", "スクラム", "OKR運用", "KPT",
)

_PROMPT = """\
以下は、ある日本で働くプロダクトマネージャー本人の職務情報と、本人が面接で実際に
話している回答の全文です。この人が**実際に使っている思考の型（方法論）**を抽出して
ください。

# 素材（この範囲の記述だけを根拠にすること）

{material}

# 抽出のルール（守らないと機械検査で捨てられます）

1. 一般的なフレームワーク名（PDCA・SWOT・MECE 等）を答えにしない。素材の中で
   **この人が実際にやっている手順**を読み取り、それに名前を付ける。
2. 各項目には必ず `evidence.quote` を付ける。**素材の中の原文をそのまま 20 文字以上
   コピーする**（要約・言い換えは禁止。機械で素材と照合し、見つからない項目は捨てる）。
3. 素材から読み取れないことは書かない。項目数を埋めるための水増しをしない。
   本当に読み取れるものだけを {max_items} 件以内で出す。
4. `steps` は 2〜5 個。抽象語（「分析する」「検討する」）ではなく、この人が実際に
   何をしているかの動作で書く。
5. `one_liner` は面接で口に出して 15 秒で言える日本語。です・ます調。

# 出力形式

JSON のみを出力してください（説明文・前置き・コードフェンスは書かない）。

{{"frameworks": [
  {{"name": "方法の名前（この人のやり方を表す短い日本語）",
    "when": "どういう場面で使うか",
    "steps": ["実際の動作1", "実際の動作2"],
    "evidence": {{"project": "素材中のどの仕事か",
                  "what_happened": "そこで実際に起きたこと（1文）",
                  "quote": "素材からの原文コピー20文字以上"}},
    "one_liner": "面接での言い方（です・ます調）"}}
]}}
"""

_JUDGE_PROMPT = """\
同じ素材から 3 つのモデルが抽出した「ある PdM 本人の思考の型」の候補です。
機械検査（原文照合）を通過した項目だけがここに載っています。

# 候補（votes = 何モデルが同じ趣旨を挙げたか。機械が算出済み）

{candidates}

# あなたの仕事

**採否の判断はしないでください。** どれを残すかは機械が決めています。あなたは:

1. 同じ趣旨の項目が別の名前で複数ある場合、1 つに統合する（votes は合計する）
2. 名前・when・steps・one_liner の日本語を、面接で使える自然な言い方に整える
3. `evidence` は**一字も変えない**（原文照合済みのため、書き換えると無効になる）

新しい項目を足さないでください。統合以外で項目を減らさないでください。

# 出力形式

JSON のみ（説明文・コードフェンスなし）。入力と同じスキーマに `votes` を残すこと。

{{"frameworks": [
  {{"name": "...", "when": "...", "steps": ["..."],
    "evidence": {{"project": "...", "what_happened": "...", "quote": "..."}},
    "one_liner": "...", "votes": 2}}
]}}
"""


def material() -> str:
    """抽出元。去識別化済みプロフィール＋正典回答＋面接覆盤。"""
    parts = [
        "## 職務プロフィール（去識別化済み）",
        "```yaml", context.profile_text(compact=False), "```",
        "",
        "## 面接での実際の回答（本人が書いた原文）",
        context.core_answers(),
    ]
    if RETRO_DIR.exists():
        retros = sorted(RETRO_DIR.glob("*.md"))[-3:]
        if retros:
            parts += ["", "## 面接後の振り返り"]
            for p in retros:
                parts.append(p.read_text(encoding="utf-8")[:2000])
    return "\n".join(parts)


# 錨定判定は gates（Gate F の引用照合）と共用なので `anchor.py` が本体。
# 既存の呼び出し名（`fb._norm` / `fb.anchored`）はそのまま使えるようにしておく。
_norm = anchor_norm
anchored = anchor_rate


def _parse(text: str) -> list[dict]:
    """モデル出力から frameworks 配列を取り出す。壊れていたら空。"""
    t = (text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group())
    except Exception:
        return []
    items = data.get("frameworks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("name")]


def draft(engine: str, prompt: str) -> tuple[str, list[dict], str]:
    """1 brain に草案を書かせる。失敗しても例外を投げない（他の草案で続行）。"""
    try:
        text = call(prompt, timeout=420, engine=engine,
                    accept={"minChars": 300, "regex": r"\"frameworks\""})
        items = _parse(text)
        if not items:
            return engine, [], "JSON を取り出せなかった"
        return engine, items[:MAX_ITEMS], ""
    except LLMUnavailable:
        raise
    except Exception as e:
        return engine, [], str(e)[:160]


def score(items: list[dict], corpus_norm: str) -> list[dict]:
    """機械採点 — 錨定率を付け、基準未満と顧問腔を捨てる。"""
    kept: list[dict] = []
    for it in items:
        ev = it.get("evidence") or {}
        rate = anchored(str(ev.get("quote") or ""), corpus_norm)
        name = str(it.get("name") or "")
        if rate < MIN_ANCHOR:
            continue
        if any(g.lower() in name.lower() for g in _GENERIC_NAMES):
            continue
        if not (it.get("steps") and ev.get("project")):
            continue
        out = dict(it)
        out["anchor_score"] = round(rate, 2)
        kept.append(out)
    return kept


def _quote(item: dict) -> str:
    return _norm(str((item.get("evidence") or {}).get("quote") or ""))


def same_source(a: str, b: str) -> bool:
    """2 つの項目が素材の同じ箇所を根拠にしているか（＝同趣旨とみなす）。

    名前での重複判定は使えない — モデルごとに命名が違い、実測で一致率 0% になった
    （同じ内容でも別項目に見える）。根拠の原文が重なっているかで見る。
    """
    if len(a) < QUOTE_WINDOW or len(b) < QUOTE_WINDOW:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    windows = [short[i:i + QUOTE_WINDOW] for i in range(0, len(short) - QUOTE_WINDOW + 1)]
    return any(w in long for w in windows)


def count_votes(item: dict, drafts: dict[str, list[dict]]) -> tuple[int, list[str]]:
    """この項目と同じ根拠を挙げたモデルの数（機械判定・裁決の申告は使わない）。"""
    q = _quote(item)
    voters = [engine for engine, items in drafts.items()
              if any(same_source(q, _quote(i)) for i in items)]
    return len(voters), voters


def merge_votes(drafts: dict[str, list[dict]]) -> list[dict]:
    """モデル横断で同じ根拠の項目を 1 つにまとめる（機械のみ）。"""
    buckets: list[dict] = []
    for engine, items in drafts.items():
        for it in items:
            q = _quote(it)
            hit = next((b for b in buckets if same_source(q, _quote(b))), None)
            if hit is None:
                b = dict(it)
                b["votes"], b["voters"] = 1, [engine]
                buckets.append(b)
            elif engine not in hit["voters"]:
                hit["votes"] += 1
                hit["voters"].append(engine)
                if it.get("anchor_score", 0) > hit.get("anchor_score", 0):
                    hit.update({x: it[x] for x in
                                ("name", "when", "steps", "evidence", "one_liner",
                                 "anchor_score") if x in it})
    return sorted(buckets, key=lambda i: (-i["votes"], -i.get("anchor_score", 0)))


def verify_final(items: list[dict], corpus_norm: str,
                 drafts: dict[str, list[dict]]) -> list[dict]:
    """裁決後の最終検査 — 機械が最後に判断する。

    裁決モデルが evidence を書き換えた／項目を捏造した場合、ここで落ちる。
    votes と anchor_score も裁決の自己申告ではなく機械で付け直す。
    """
    out: list[dict] = []
    for it in items:
        rate = anchored(str((it.get("evidence") or {}).get("quote") or ""), corpus_norm)
        if rate < MIN_ANCHOR:
            print(f"  ✗ 裁決後の錨定検査で落選（{rate:.2f}）: {it.get('name')}")
            continue
        votes, voters = count_votes(it, drafts)
        if votes == 0:
            print(f"  ✗ 元の草案に対応が無い（裁決が創作した疑い）: {it.get('name')}")
            continue
        it["anchor_score"] = round(rate, 2)
        it["votes"] = votes
        it["voters"] = voters
        out.append(it)
    return sorted(out, key=lambda i: (-i["votes"], -i["anchor_score"]))


def judge(items: list[dict]) -> list[dict]:
    """統合と言い回しの調整だけを LLM に任せる（採否の権限は与えない）。"""
    payload = json.dumps({"frameworks": items}, ensure_ascii=False, indent=1)
    try:
        text = call(_JUDGE_PROMPT.format(candidates=payload), timeout=420,
                    engine=JUDGE_ENGINE,
                    accept={"minChars": 200, "regex": r"\"frameworks\""})
        merged = _parse(text)
    except LLMUnavailable:
        raise
    except Exception as e:
        print(f"  ⚠ 合併裁決に失敗（{str(e)[:80]}）— 機械集計の結果をそのまま使う")
        return items
    if not merged:
        print("  ⚠ 裁決の出力が壊れていた — 機械集計の結果をそのまま使う")
        return items
    return merged


def build(*, dry_run: bool = False) -> list[dict]:
    mat = material()
    corpus_norm = _norm(mat)
    prompt = _PROMPT.format(material=mat, max_items=MAX_ITEMS)
    print(f"■ 素材 {len(mat)} 字 / 草案モデル: {', '.join(DRAFT_ENGINES)}")

    if dry_run:
        out = ROOT / "output" / "proposal" / "_frameworks_prompt.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt, encoding="utf-8")
        print(f"  → prompt を落とした（LLM 未呼出）: {out.relative_to(ROOT)}")
        return []

    with ThreadPoolExecutor(max_workers=len(DRAFT_ENGINES)) as pool:
        results = list(pool.map(lambda e: draft(e, prompt), DRAFT_ENGINES))

    drafts: dict[str, list[dict]] = {}
    for engine, items, err in results:
        if err:
            print(f"  ✗ {engine}: {err}")
            continue
        scored = score(items, corpus_norm)
        drafts[engine] = scored
        print(f"  ✓ {engine}: 抽出 {len(items)} 件 → 錨定合格 {len(scored)} 件")

    if not drafts:
        sys.exit("全モデルが草案を出せなかった。指揮中心の状態を確認すること。")

    voted = merge_votes(drafts)
    overlap = sum(1 for v in voted if v["votes"] >= 2)
    print(f"\n■ 機械集計（根拠原文の重なりで判定）: 全 {len(voted)} 件"
          f"（2 モデル以上一致 {overlap} 件 = {overlap / max(len(voted), 1):.0%}）")

    final = verify_final(judge(voted), corpus_norm, drafts)
    if not final:
        sys.exit("裁決後の機械検査で全項目が落ちた。data/frameworks.yaml は更新しない。")
    frameworks.save(final, {
        "version": 1,
        "generated_at": date.today().isoformat(),
        "built_by": list(drafts.keys()),
        "judge": JUDGE_ENGINE,
        "note": "自動生成だが User Layer。手で直してよい / 自動では上書きしない。"
                "再構築は python3 -m proposal.frameworks_build --rebuild",
    })
    print(f"■ 確定 {len(final)} 件 → {frameworks.path().relative_to(ROOT)}")
    for f in final:
        print(f"  [{f.get('votes')}票 錨定{f.get('anchor_score')}] {f.get('name')}")
    return final


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m proposal.frameworks_build",
        description="方法論庫を 3 モデル草案＋機械採点で構築する")
    p.add_argument("--dry-run", action="store_true",
                   help="LLM を呼ばず prompt だけ落とす")
    p.add_argument("--rebuild", action="store_true",
                   help="既存の data/frameworks.yaml があっても作り直す")
    args = p.parse_args()

    if frameworks.exists() and not args.rebuild and not args.dry_run:
        items = frameworks.load()
        print(f"既に {len(items)} 件構築済み: {frameworks.path().relative_to(ROOT)}")
        print("作り直すなら --rebuild（手で直した内容は失われる）")
        return 0
    build(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
