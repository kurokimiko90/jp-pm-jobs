"""Auto-generate company brief draft.

用法:
    python3 -m interview.company_brief mercari
    python3 -m interview.company_brief dmm --update  # 強制覆蓋既有檔案

流程：讀 _template.md → LLM 填空 → 寫到 companies/{name}.md（如已存在則加 .draft 後綴）
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import _llm

COMPANIES_DIR = Path(__file__).parent / "companies"
TEMPLATE_PATH = COMPANIES_DIR / "_template.md"

PROMPT_TEMPLATE = """\
あなたは日本の IT/SaaS 業界に詳しいプロダクトリサーチャーです。
以下のテンプレートを {company_name} の情報で埋めて、senior PM が面接前に読む 1 ページの brief を作成してください。

要件：
- 公開情報のみを使う（IR、Tech Blog、Note 記事、Speaker Deck、OpenWork、Glassdoor 等）
- 不明確な数字は {{XX}} のままにする（捏造しない）
- 直近 3 ヶ月の動向セクションは「要直近 IR 確認」と注記
- 「予想される質問」は本当に出そうな質問を 5 つ厳選し、回答方向を具体的に
- 「逆質問リスト」は HR / Hiring Manager / Executive 各 3 つ
- カルチャー観察は外資度・年功度・データドリブン度を中心に
- 全文日本語、敬体（です・ます）

# テンプレート
{template}

# 出力
上記テンプレートと同じ構造で、{company_name} 用の brief を Markdown で出力してください。
最終更新日は {today}。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("company", help="例: mercari, dmm, smarthr")
    parser.add_argument("--update", action="store_true", help="覆蓋既有檔案")
    args = parser.parse_args()

    if not TEMPLATE_PATH.exists():
        print(f"テンプレートが見つかりません: {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    out_path = COMPANIES_DIR / f"{args.company}.md"
    if out_path.exists() and not args.update:
        out_path = COMPANIES_DIR / f"{args.company}.draft.md"
        print(f"⚠ 既存ファイルあり、draft として保存: {out_path}")

    print(f"LLM 呼び出し中…（30-90 秒）")
    prompt = PROMPT_TEMPLATE.format(
        company_name=args.company,
        template=template,
        today=date.today().isoformat(),
    )
    # 驗收條件：必須是「填好的完整 template」而非摘要。指揮中心據此拒絕
    # 不達標的 brain 回應並自動換下一個大腦；全大腦都填不出才整體失敗。
    accept = {
        "minChars": 2000,
        "minLines": 60,
        "includesAll": ["## 1. 会社の基本情報", "## 4. 労務条件・待遇", "## 6. 直近の動向"],
    }
    try:
        response = _llm.call(prompt, timeout=300, accept=accept)
    except RuntimeError as e:
        print(f"LLM 呼び出し失敗: {e}", file=sys.stderr)
        return 2

    out_path.write_text(response, encoding="utf-8")
    print(f"✓ 出力: {out_path}")
    print(f"\n次のステップ：")
    print(f"  1. {out_path} を開いて事実確認 + 補強")
    print(f"  2. 直近 3 ヶ月の動向セクションを最新化")
    print(f"  3. 「私の applying angle」欄を手書きで埋める")
    return 0


if __name__ == "__main__":
    sys.exit(main())
