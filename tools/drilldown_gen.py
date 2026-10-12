#!/usr/bin/env python3
"""面試反問產生器 — 批次 LLM 呼叫，對 QA 的每個回答生成 5 個尖銳追問 + 準備應答。

用法:
    python3 tools/drilldown_gen.py interview/question-bank/common_qa.md
    python3 tools/drilldown_gen.py output/prep/105_VOLTMIND/03_interview_qa.md
    python3 tools/drilldown_gen.py interview/question-bank/common_qa.md --force
    python3 tools/drilldown_gen.py interview/question-bank/common_qa.md --section 3
    python3 tools/drilldown_gen.py interview/question-bank/common_qa.md --no-llm

批次策略: ≤8 題 → 1 次呼叫；>8 題 → 每 7 題一批。
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interview._llm import call as llm_call
from tools.deid import build_deid_profile, load_profile

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 7

BATCH_PROMPT_TEMPLATE = """\
あなたは資深の採用 HR（人事部長クラス）とプロダクト責任者（VP of Product）の 2 つの視点を持つ面接官です。

以下は候補者の面接回答（複数）です。**各回答に対して 5 つの尖銳な深掘り反問と準備応答**を生成してください。

## 候補者プロフィール（去識別化済み）

{profile}

## 面接の質問と回答

{sections_block}

## 出力フォーマット（厳守）

各セクションを `## {{番号}}` で区切り、その下に D1〜D5 を出力。
各反問の末尾に（HR）または（Product）で視点を明記。
応答は候補者の一人称で、プロフィールに基づいた事実のみ使用。**編造禁止**。

```
## 1

### D1. [反問]（HR / Product）

> [候補者の準備応答。blockquote で。]

### D2. ...

## 2

### D1. ...
```

回答のみ出力。前置き・後書き不要。"""


def parse_common_qa(text: str) -> list[dict]:
    sections: list[dict] = []
    parts = re.split(r"^## (\d+\.\s.+)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        num_match = re.match(r"(\d+)", title)
        sections.append({
            "num": num_match.group(1) if num_match else str(len(sections) + 1),
            "title": title, "body": body,
        })
    return sections


def parse_prep_qa(text: str) -> list[dict]:
    sections: list[dict] = []
    parts = re.split(r"^### (Q[\d\-]+[\.、]\s*.+)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        num_match = re.match(r"Q([\d\-]+)", title)
        sections.append({
            "num": num_match.group(1) if num_match else str(len(sections) + 1),
            "title": title, "body": body,
        })
    return sections


def parse_existing_drilldown(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    dd_parts = re.split(r"^## (\S+)\s*$", text, flags=re.MULTILINE)
    for i in range(1, len(dd_parts), 2):
        result[dd_parts[i].strip()] = dd_parts[i + 1].strip() if i + 1 < len(dd_parts) else ""
    return result


def build_sections_block(sections: list[dict]) -> str:
    parts = []
    for s in sections:
        parts.append(f"### [{s['num']}] {s['title']}\n\n{s['body']}")
    return "\n\n---\n\n".join(parts)


def generate_batch(sections: list[dict], profile_text: str,
                   no_llm: bool = False, on_progress=None) -> dict[str, str]:
    """批次生成 drilldown，回傳 {section_num: raw_md}。"""
    n_batches = 1 if len(sections) <= 8 else math.ceil(len(sections) / BATCH_SIZE)
    batch_size = len(sections) if n_batches == 1 else BATCH_SIZE
    results: dict[str, str] = {}

    for b in range(n_batches):
        batch = sections[b * batch_size:(b + 1) * batch_size]
        sections_block = build_sections_block(batch)
        prompt = BATCH_PROMPT_TEMPLATE.format(
            profile=profile_text, sections_block=sections_block)

        if no_llm:
            print(f"\n{'='*60}\n[PROMPT batch {b+1}/{n_batches}]\n{'='*60}")
            print(prompt[:500] + "\n...\n")
            continue

        label = f"batch {b+1}/{n_batches} ({len(batch)} sections)"
        print(f"  LLM {label}...", end="", flush=True)
        start = time.time()
        raw = llm_call(prompt, timeout=600, model=MODEL)
        elapsed = time.time() - start
        print(f" {elapsed:.0f}s")

        dd_parts = re.split(r"^## (\S+)\s*$", raw, flags=re.MULTILINE)
        for j in range(1, len(dd_parts), 2):
            num = dd_parts[j].strip()
            body = dd_parts[j + 1].strip() if j + 1 < len(dd_parts) else ""
            results[num] = body

        if on_progress:
            on_progress(b + 1, n_batches, [s["num"] for s in batch])

    return results


def write_drilldown(out_path: Path, results: dict[str, str], stem: str) -> None:
    header = f"# {stem} — 深掘り反問 5 選（HR・プロダクト視点）\n\n"
    header += "自動生成 by tools/drilldown_gen.py\n\n---\n"
    body_parts: list[str] = []
    for num in sorted(results.keys(), key=lambda x: (len(x), x)):
        body_parts.append(f"\n## {num}\n\n{results[num]}")
    out_path.write_text(header + "\n".join(body_parts) + "\n", encoding="utf-8")


def run(input_path: Path, force: bool = False, section: str | None = None,
        no_llm: bool = False, on_progress=None) -> Path:
    """Core entry point — CLI と API 両方から呼ばれる。"""
    text = input_path.read_text(encoding="utf-8")
    is_prep = "### Q1" in text or "### Q1." in text
    sections = parse_prep_qa(text) if is_prep else parse_common_qa(text)

    if not sections:
        raise ValueError("找不到可解析的 section")

    out_path = input_path.parent / f"{input_path.stem}_drilldown.md"
    existing = {} if force else parse_existing_drilldown(out_path)

    if section:
        sections = [s for s in sections if s["num"] == section]
        if not sections:
            raise ValueError(f"找不到 section {section}")

    todo = [s for s in sections if s["num"] not in existing]
    if not todo and not force:
        print(f"全 {len(sections)} sections 已有 drilldown，跳過（用 --force 覆蓋）")
        return out_path

    profile_text = build_deid_profile(load_profile())
    targets = sections if force else todo

    print(f"輸入: {input_path} ({len(targets)} sections to generate)")
    print(f"去識別化 profile: {len(profile_text)} chars")

    new_results = generate_batch(targets, profile_text, no_llm=no_llm, on_progress=on_progress)

    merged = {**existing, **new_results}
    if not no_llm and merged:
        write_drilldown(out_path, merged, input_path.stem)
        print(f"完成: {out_path} ({len(merged)} sections)")

    return out_path


def main():
    parser = argparse.ArgumentParser(description="面試反問產生器")
    parser.add_argument("input", help="QA markdown 檔案路徑")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--section", type=str)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"找不到 {input_path}", file=sys.stderr)
        sys.exit(1)

    run(input_path, force=args.force, section=args.section, no_llm=args.no_llm)


if __name__ == "__main__":
    main()
