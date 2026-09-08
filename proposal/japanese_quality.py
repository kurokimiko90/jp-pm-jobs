"""提案 deck の日本語品質 — 中国語直訳を止め、IT 現場の表現へそろえる。

LLM 校閲だけに任せると、再生成や ``--from-fields`` で品質が後戻りする。そのため、
最終校閲の prompt と、明確に不自然な語を止める決定論的 lint を同じ場所で管理する。
lint は日本語の巧拙を採点するものではない。機械で確実に判断できる直訳語・硬い
書面語だけをブロックし、文脈を要する推敲は最終 LLM 校閲が担当する。
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
import re
from typing import Any

AUDIT_FILE = "_deck.ja_review.json"
REVIEW_VERSION = "ja-it-v1"

# 日本の IT / PdM 文書では別の定着表現を使う語。substring で見るため、誤検知しない
# 長さ・特異性のある語だけに絞る。「寄与」のように文脈次第で自然な語は含めない。
REPLACEMENTS: dict[str, str] = {
    "資深": "シニア",
    "職位": "ポジション",
    "版式": "レイアウト",
    "会社原文": "公式サイトの原文",
    "実項目": "具体的な実績",
    "投影片": "スライド",
    "収銀端末": "決済端末",
    "対接": "連携",
    "落地": "実装／具体化",
    "賦能": "支援／実現",
    "閉環": "改善サイクル",
    "颗粒度": "粒度／具体性",
    "取得不能": "取得できない",
    "結合不能": "紐付けできない",
    "判別不能": "判断できない",
    "責任分界": "責任範囲",
    "精緻化条件": "具体化する条件",
    "何をする箱": "役割／機能",
}

# 文脈に関係なく置換して意味と文法を保てるものだけ。``結合不能`` のように
# 「〜や」で続くと単純置換では文法が壊れる語は、LLM 校閲に残す。
SAFE_REPLACEMENTS: dict[str, str] = {
    "資深": "シニア",
    "職位": "ポジション",
    "版式": "レイアウト",
    "会社原文": "公式サイトの原文",
    "実項目": "具体的な実績",
    "投影片": "スライド",
    "収銀端末": "決済端末",
    "取得不能な": "取得できない",
    "取得不能": "取得できない",
    "責任分界": "責任範囲",
    "精緻化条件": "具体化する条件",
    "何をする箱": "役割",
}

_CONTROL_KEYS = {"layout", "role", "badge", "hypo"}
_NUMBER_RE = re.compile(r"\d+(?:[,.]\d+)*(?:\.\d+)?%?")


def _strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """JSON の全文字列を ``slides[2].title`` 形式の path つきで列挙する。"""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _strings(child, child_path)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _strings(child, f"{path}[{i}]")


def lint(fields: dict) -> list[str]:
    """明確な中国語直訳・非標準語を Deck E エラーとして返す。"""
    issues: list[str] = []
    for path, text in _strings(fields):
        for term, preferred in REPLACEMENTS.items():
            if term not in text:
                continue
            location = path or "deck"
            issues.append(
                f"[Deck E] {location}: 「{term}」は日本の IT 提案では不自然。"
                f"「{preferred}」など文脈に合う表現へ直すこと"
            )
    return issues[:12]


def normalize_safe(fields: dict) -> tuple[dict, list[dict]]:
    """意味を変えない一対一置換だけを行い、変更履歴とともに返す。"""
    result = deepcopy(fields)
    changes: list[dict] = []

    def walk(value: Any, path: str = "") -> Any:
        if isinstance(value, str):
            revised = value
            for term, preferred in SAFE_REPLACEMENTS.items():
                revised = revised.replace(term, preferred)
            if revised != value:
                changes.append({
                    "field": path or "deck",
                    "before": value,
                    "after": revised,
                    "reason": "明確な中国語直訳を日本のIT資料で一般的な表現へ統一",
                })
            return revised
        if isinstance(value, dict):
            return {key: walk(child, f"{path}.{key}" if path else str(key))
                    for key, child in value.items()}
        if isinstance(value, list):
            return [walk(child, f"{path}[{i}]") for i, child in enumerate(value)]
        return value

    result = walk(result)
    return result, changes


def contract_issues(before: Any, after: Any, path: str = "fields") -> list[str]:
    """日本語校閲が JSON 構造・制御値・数字を変えていないか検査する。"""
    issues: list[str] = []

    def walk(left: Any, right: Any, current: str, key: str = "") -> None:
        if len(issues) >= 12:
            return
        if type(left) is not type(right):
            issues.append(f"[Deck E] {current}: 日本語校閲でデータ型が変わった")
            return
        if isinstance(left, dict):
            if set(left) != set(right):
                missing = sorted(set(left) - set(right))
                added = sorted(set(right) - set(left))
                issues.append(
                    f"[Deck E] {current}: 日本語校閲で JSON キーが変わった"
                    f"（削除={missing}, 追加={added}）")
                return
            for child_key in left:
                walk(left[child_key], right[child_key],
                     f"{current}.{child_key}", child_key)
            return
        if isinstance(left, list):
            if len(left) != len(right):
                issues.append(
                    f"[Deck E] {current}: 日本語校閲で要素数が "
                    f"{len(left)} → {len(right)} に変わった")
                return
            for i, (l_item, r_item) in enumerate(zip(left, right)):
                walk(l_item, r_item, f"{current}[{i}]", key)
            return
        if isinstance(left, str):
            if key in _CONTROL_KEYS and left != right:
                issues.append(
                    f"[Deck E] {current}: 制御値を「{left}」から「{right}」へ変更した")
                return
            before_numbers = _NUMBER_RE.findall(left)
            after_numbers = _NUMBER_RE.findall(right)
            if before_numbers != after_numbers:
                issues.append(
                    f"[Deck E] {current}: 日本語校閲で数字が変わった"
                    f"（{before_numbers} → {after_numbers}）")
            return
        if left != right:
            issues.append(
                f"[Deck E] {current}: 日本語以外の値を {left!r} から {right!r} へ変更した")

    walk(before, after, path)
    return issues[:12]


REVIEW_PROMPT = """\
あなたは日本の IT 企業で、プロダクト戦略資料と経営会議資料を校閲している
シニアテクニカルライターです。以下の提案 deck JSON を、**日本人のPdMが日本企業の
プロダクト責任者とエンジニアへ提示して自然に聞こえる日本語**へ推敲してください。

# この工程の責任範囲

これは内容レビューではなく、日本語だけの最終校閲です。次を厳守してください。

1. JSON のキー、スライド枚数、順序、layout、role、badge、構造は一切変えない。
2. 事実、仮説、主張、数字、期間、固有名詞、製品名、実績の意味を変えない。
3. 情報を追加・削除しない。表の行列数、cards / steps / phases / drivers の数も維持する。
4. 見出し、本文、表、図のラベル、脚注、発表者ノートの**日本語表現だけ**を直す。
5. 中国語直訳、翻訳調の語順、硬い官僚語、抽象名詞の連続、過剰なコンサル用語を避ける。
6. 日本の IT 現場で定着した語を使う。例：ポジション、連携、要件定義、実装、検証、
   ロードマップ、優先順位、KPI、データ基盤、ワークフロー、責任範囲。
7. コンポーネントを「箱」と呼ばず、その実体に応じて機能、画面、データ基盤、
   ワークフロー、ルールなどと書く。
8. 見出しは短く具体的にし、全ページを機械的な「〜です」でそろえない。
9. 発表者ノートは面接でそのまま話せる、簡潔なです・ます調にする。
10. 元の表現がすでに自然なら変更しない。言い換えること自体を目的にしない。

# 校閲対象

{fields_json}

# 出力

JSON のみ。コードフェンスや説明文は付けない。

{{"verdict":"PASS",
  "changes":[{{"slide":1,"field":"title","before":"修正前","after":"修正後",
                "reason":"直訳調を日本のIT資料で一般的な表現へ修正"}}],
  "fields":{{"deck":{{...}},"slides":[...]}}}}

校閲後の ``fields`` 全体を必ず返してください。修正が無い場合は ``changes`` を空配列にし、
``verdict`` は ``PASS`` とします。
"""
