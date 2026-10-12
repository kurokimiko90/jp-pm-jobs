"""質問見出しの頭に置く 5 文字以内のキーワード。

面接当日は 90 問超を上から読み返す。質問文を最後まで読まずに「今どの話か」が
掴めるよう、見出しを `### Q. [キーワード] 質問文` の形にする。

生成物を読む側（TTS・qa_upgrade）は必ず `split()` で剥がしてから使う —
剥がさないと読み上げに「かぎかっこ」が混ざる。見出し正規表現へ直接埋め込む
場合は `OPTIONAL_PREFIX` を使う。
"""

from __future__ import annotations

import re

MAX_LEN = 5

# 見出し正規表現へ埋め込む用（キーワード部分を捨てて質問文だけ拾う）
OPTIONAL_PREFIX = r"(?:[\[［][^\]］\n]{1,12}[\]］]\s*)?"

_PREFIX_RE = re.compile(r"^[\[［]\s*([^\]］\n]{1,12}?)\s*[\]］]\s*")

# fallback 用 — LLM がキーワードを付け忘れた問だけがここへ来る。
# 上から順に照合し、最初に当たったものを使う（特殊な話題を先に置く）。
_RULES: tuple[tuple[str, str], ...] = (
    (r"逆質問|何か質問はありますか", "逆質問"),
    (r"希望年収|年収|入社可能", "年収・時期"),
    (r"転職の軸", "転職の軸"),
    (r"転職(を考えて|理由)", "転職理由"),
    (r"志望|なぜ当社|なぜ弊社|なぜ御社", "志望動機"),
    (r"キャリアビジョン|今後のキャリア", "キャリア"),
    (r"ご経歴|これまでの経歴|自己紹介", "経歴"),
    (r"仕事内容", "現職業務"),
    (r"強みを", "強み"),
    (r"弱みを|苦手", "弱み"),
    (r"最も成果|一番成果", "最大成果"),
    (r"苦労|失敗|間違って|期待した成果が出な", "失敗経験"),
    (r"採用する理由|他の候補者", "採用理由"),
    (r"優先順位", "優先順位"),
    (r"ロードマップ", "計画策定"),
    (r"合意形成|意見が対立", "合意形成"),
    (r"指標|KPI|効果測定|成功を", "効果指標"),
    (r"セキュリティ|法規制|コンプライアンス", "安全設計"),
    (r"コスト|トークン消費", "コスト"),
    (r"多言語|訪日|海外", "多言語"),
    (r"権限", "権限設計"),
    (r"品質|正確性|誤生成|ハルシネーション", "品質担保"),
    (r"PoC", "PoC"),
    (r"アーキテクチャ|全体フロー", "設計"),
    (r"Agent|エージェント", "Agent"),
    (r"AI", "AI 活用"),
    (r"エンジニアと|開発チーム", "開発連携"),
    (r"顧客|クライアント|パートナー", "顧客折衝"),
    (r"フリーランス", "フリーランス"),
    (r"進捗|リスク管理|プロジェクト管理", "進行管理"),
    (r"市場調査|競合", "市場分析"),
    (r"ユーザー(の)?(ニーズ|声)|仮説", "仮説検証"),
    (r"課題を(発見|見つけ)", "課題発見"),
    (r"責務|役割", "PM 像"),
)


def trim(keyword: str) -> str:
    """記号・空白を落として MAX_LEN へ切り詰める。空文字なら空文字を返す。"""

    cleaned = re.sub(r"[\[\]［］\s]+", "", keyword or "").strip("　")
    return cleaned[:MAX_LEN]


def split(text: str) -> tuple[str, str]:
    """`[キーワード] 質問文` → (キーワード, 質問文)。無ければ ("", 質問文)。"""

    match = _PREFIX_RE.match(text.strip())
    if not match:
        return "", text.strip()
    return trim(match.group(1)), text[match.end():].strip()


def decorate(keyword: str, question: str) -> str:
    """描画用 — キーワードが無ければ質問文だけを返す（空の `[]` を出さない）。"""

    keyword = trim(keyword)
    return f"[{keyword}] {question}" if keyword else question


def fallback(question: str) -> str:
    """LLM がキーワードを付けなかった問の穴埋め。規則のみ・LLM は呼ばない。"""

    for pattern, keyword in _RULES:
        if re.search(pattern, question):
            return trim(keyword)
    # どの規則にも当たらない問 — 質問文の頭を切って出す（無いよりは手掛かりになる）
    head = re.sub(r"^(なぜ|どのように|どうやって|どういった)", "", question.strip())
    return trim(head)
