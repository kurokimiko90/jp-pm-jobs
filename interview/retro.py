"""真實面試後 retro — LLM 分析改進點，寫 Obsidian。

用法:
    1. 面試後立即手寫 retros/{company}-{YYYY-MM-DD}.md (用 _retro_template.md)
    2. python3 -m interview.retro retros/mercari-2026-06-15.md
    3. LLM 讀 raw retro → 結構化分析 → append 到同檔末尾「## LLM 分析」段

模板會自動建立。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import _llm

RETROS_DIR = Path(__file__).parent / "retros"
TEMPLATE_PATH = RETROS_DIR / "_retro_template.md"

DEFAULT_TEMPLATE = """\
# {company} 面試 retro — {date}

## 基本情報
- **会社**: {company}
- **ポジション**: {{応募ポジション}}
- **面接ラウンド**: {{N 次面接}} / {{HR / Hiring Manager / Cross-functional / Exec}}
- **時間**: {{何分間}}
- **面接官**: {{氏名 / 役職}}
- **言語**: {{日本語 / 英語 / 混合}}

## 面接官について
{{面接官の経歴で印象に残った点、話し方の特徴}}

## 聞かれた質問（全部、聞いた順に）
1. ...
2. ...
3. ...
...

## 私の回答（重要なものだけ、要約）
- Q{{N}}: {{要約 + 反省点}}
- ...

## 私からの逆質問 + 反応
1. {{質問}} → {{相手の反応・どう答えたか}}

## 雰囲気 / 体感
{{どう感じたか。雰囲気が硬かった、噛み合わなかった、深く突っ込まれた、等}}

## 不明な動作
- {{面接官が眉をひそめた瞬間、急に話題を変えた瞬間、メモを取った瞬間など}}

## 自己評価（1-5）
- Clarity: {{X}}
- Structure: {{X}}
- Specifics: {{X}}
- Depth: {{X}}
- Japan etiquette: {{X}}
- 一言：{{合格しそう / 微妙 / 落ちたと思う}}

## メモ
{{次のラウンドに向けて準備したい質問、補足が必要な領域}}

---

## LLM 分析
（python3 -m interview.retro {{this file}} で自動追加）
"""

ANALYSIS_PROMPT = """\
以下は実際の PM 面接後のセルフ retro です。客観的に分析し、次回までに改善すべき点を具体的に指摘してください。

要件：
1. 「聞かれた質問」と「私の回答」から、各質問でどの能力が試されていたかを推測
2. 自己評価と実際の答えの内容を照合し、ギャップがあれば指摘
3. 不明な動作（面接官の反応）の解釈仮説を 2-3 個提示
4. 次の面接ラウンド（あれば）または次の応募までに、3 つの具体的なアクションを推奨

辛口で OK。励まし不要。改善点を明確に。

# Retro 全文
{retro}
"""


def ensure_template() -> None:
    RETROS_DIR.mkdir(exist_ok=True)
    if not TEMPLATE_PATH.exists():
        TEMPLATE_PATH.write_text(
            "# 使い方\n\n面接直後に以下をコピーして "
            "`{company}-{YYYY-MM-DD}.md` として保存し、生々しい記憶を全部書き出してください。"
            "完璧でなくて良い。後で `python3 -m interview.retro <file>` で LLM 分析を追加します。\n\n"
            "```markdown\n" + DEFAULT_TEMPLATE + "```\n",
            encoding="utf-8",
        )
        print(f"テンプレート作成: {TEMPLATE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("retro_file", nargs="?", help="path to filled retro markdown")
    parser.add_argument("--init", action="store_true", help="just create template")
    args = parser.parse_args()

    ensure_template()
    if args.init:
        return 0

    if not args.retro_file:
        print("usage: python3 -m interview.retro <retro_file.md>", file=sys.stderr)
        print("  or: python3 -m interview.retro --init  (just write template)", file=sys.stderr)
        return 1

    retro_path = Path(args.retro_file).expanduser().resolve()
    if not retro_path.exists():
        print(f"見つかりません: {retro_path}", file=sys.stderr)
        return 1

    content = retro_path.read_text(encoding="utf-8")
    if "## LLM 分析" in content and content.split("## LLM 分析", 1)[1].strip():
        print("既に LLM 分析が含まれています。--update で再実行（実装予定）。")
        return 0

    print("LLM 分析中…（60-120 秒）")
    try:
        analysis = _llm.call(ANALYSIS_PROMPT.format(retro=content), timeout=300)
    except RuntimeError as e:
        print(f"失敗: {e}", file=sys.stderr)
        return 2

    if "## LLM 分析" in content:
        new_content = content.split("## LLM 分析")[0].rstrip() + "\n\n## LLM 分析\n\n" + analysis + "\n"
    else:
        new_content = content.rstrip() + "\n\n---\n\n## LLM 分析\n\n" + analysis + "\n"

    retro_path.write_text(new_content, encoding="utf-8")
    print(f"\n✓ 更新: {retro_path}")
    print("\n次のステップ：")
    print("  1. 分析を読んで、特に「次のアクション」3 つを scheduler / Obsidian に入れる")
    print("  2. 実際に聞かれた質問は question-bank/asked-frequently.md に追記")
    print("  3. 重要な学びは question-bank/ の該当題に逆フィードバック")
    return 0


if __name__ == "__main__":
    sys.exit(main())
