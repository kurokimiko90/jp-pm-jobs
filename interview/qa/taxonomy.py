"""正典題庫の分類定義 — 手書き 4 ファイル 75 問 → 48 問の正典へ収斂する写像。

`sources` は移行元の参照子（`common#3` = common_qa.md の「## 3.」）。同じ質問に
複数の答えが散っていた状態を、ここで 1 問 1 答へ畳む。移行後に元ファイルを読む
必要はないが、事実の出所を辿れるよう痕跡として残す。

`jd_dependent=True` の問は、pack 生成時に末尾を JD 向けへ書き換える対象。
`keyword` は見出しの頭に置く 5 文字以内の話題ラベル（`keywords.MAX_LEN`）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreQuestion:
    qid: str
    category: str
    question: str
    sources: tuple[str, ...]
    jd_dependent: bool = False
    keyword: str = ""


CATEGORIES: dict[str, str] = {
    "career": "経歴・転職・条件",
    "strength": "強み・弱み・成功と失敗",
    "pm": "プロダクトマネジメント実務",
    "ai": "AI プロダクト",
    "agent": "AI Agent",
    "client": "顧客折衝・フリーランス",
    "closing": "逆質問",
}

CATEGORY_ORDER: tuple[str, ...] = (
    "career", "strength", "pm", "ai", "agent", "client", "closing",
)


CORE: tuple[CoreQuestion, ...] = (
    # ---------------------------------------------------------- career
    CoreQuestion("C01", "career", "これまでのご経歴を教えてください",
                 ("common#1",), jd_dependent=True, keyword="経歴"),
    CoreQuestion("C02", "career", "現在の具体的な仕事内容を教えてください",
                 ("common#2",), keyword="現職業務"),
    CoreQuestion("C03", "career", "転職を考えている理由を教えてください",
                 ("common#7",), jd_dependent=True, keyword="転職理由"),
    CoreQuestion("C04", "career", "なぜプロダクトマネージャーという職種を選んだのですか",
                 ("common#8", "pm#2-6"), jd_dependent=True, keyword="PdM志望"),
    CoreQuestion("C05", "career", "今後のキャリアビジョンを教えてください",
                 ("common#5",), jd_dependent=True, keyword="キャリア"),
    CoreQuestion("C06", "career", "希望年収と入社可能時期を教えてください",
                 ("common#6",), keyword="年収・時期"),
    CoreQuestion("C07", "career", "他の候補者ではなく、あなたを採用する理由は何ですか",
                 ("ext#13", "pm#2-11"), jd_dependent=True, keyword="採用理由"),

    # -------------------------------------------------------- strength
    CoreQuestion("C08", "strength", "ご自身の強みを教えてください",
                 ("common#3",), jd_dependent=True, keyword="強み"),
    CoreQuestion("C09", "strength", "ご自身の弱みを教えてください",
                 ("common#3",), keyword="弱み"),
    CoreQuestion("C10", "strength", "これまでで最も成果を出したプロジェクトを教えてください",
                 ("common#4", "ext#14"), jd_dependent=True, keyword="最大成果"),
    CoreQuestion("C11", "strength",
                 "期待した成果が出なかった経験、または自分の判断が間違っていた経験を教えてください",
                 ("common#4", "ext#15"), keyword="失敗経験"),

    # -------------------------------------------------------------- pm
    CoreQuestion("C12", "pm", "プロダクトマネージャーの責務をどのように理解していますか",
                 ("pm#2-6",), keyword="PdM責務"),
    CoreQuestion("C13", "pm", "プロダクトロードマップをどのように策定しますか",
                 ("ext#16", "pm#2-1", "pm#2-2"), keyword="計画策定"),
    CoreQuestion("C14", "pm", "複数の要望がある中で、優先順位をどのように決めますか",
                 ("ext#17", "pm#3-1"), keyword="優先順位"),
    CoreQuestion("C15", "pm", "複数のプロジェクトや緊急案件が重なった場合、どう対応しますか",
                 ("ext#21", "pm#3-2"), keyword="並行対応"),
    CoreQuestion("C16", "pm", "新しいプロダクトや機能を検討する際、どのように課題を発見しますか",
                 ("ext#18", "pm#2-9"), keyword="課題発見"),
    CoreQuestion("C17", "pm", "ユーザーのニーズや仮説を、どのように収集し検証していますか",
                 ("ext#19", "pm#2-10"), keyword="仮説検証"),
    CoreQuestion("C18", "pm", "市場調査や競合分析をどのように行い、プロダクト計画に落とし込みますか",
                 ("pm#2-4", "pm#2-5"), keyword="市場分析"),
    CoreQuestion("C19", "pm", "プロダクトや施策の成功を、どのような指標で判断しますか",
                 ("ext#22",), keyword="効果指標"),
    CoreQuestion("C20", "pm", "エンジニアとどのように協働し、要件や優先順位を決めていますか",
                 ("ext#23",), keyword="開発連携"),
    CoreQuestion("C21", "pm",
                 "営業・エンジニア・経営層などの意見が対立した場合、どのように合意形成しますか",
                 ("ext#20",), keyword="合意形成"),
    CoreQuestion("C22", "pm", "プロジェクトの進捗・品質・リスクをどのように管理していますか",
                 ("pm#3-3", "pm#3-9"), keyword="進行管理"),
    CoreQuestion("C23", "pm", "プロジェクト管理で最も重視していることは何ですか",
                 ("pm#1-5",), keyword="管理方針"),
    CoreQuestion("C24", "pm", "既存プロダクトの改善を進める場合、どのような観点から着手しますか",
                 ("pm#3-4",), jd_dependent=True, keyword="改善着手"),

    # -------------------------------------------------------------- ai
    CoreQuestion("C25", "ai", "実際の業務で、AI をどのように活用していますか",
                 ("ext#29", "pm#2-13", "ans#4-1"), jd_dependent=True, keyword="AI活用"),
    CoreQuestion("C26", "ai", "AI を活用したプロダクトが解決した課題と、ご自身の担当範囲を教えてください",
                 ("ans#4-2",), keyword="担当範囲"),
    CoreQuestion("C27", "ai", "AI を使うべき業務と、使うべきでない業務をどのように判断しますか",
                 ("ext#24", "ans#4-3", "ans#6-4"), keyword="適用判断"),
    CoreQuestion("C28", "ai", "AI の出力品質や正確性をどのように担保しますか",
                 ("ext#25", "ans#4-4", "ans#4-5"), keyword="品質担保"),
    CoreQuestion("C29", "ai", "AI の自動実行と人による判断の境界を、どのように設計しますか",
                 ("ans#4-9", "ans#5-5"), keyword="人とAI"),
    CoreQuestion("C30", "ai", "AI の PoC を、どのように本番導入と継続利用につなげますか",
                 ("ext#26", "ans#4-12", "ans#6-7"), keyword="PoC展開"),
    CoreQuestion("C31", "ai", "AI の正確性・コスト・速度・ユーザー体験をどうバランスさせますか",
                 ("ext#27", "ans#4-8"), keyword="バランス"),
    CoreQuestion("C32", "ai", "AI を業務に導入する際、セキュリティや責任範囲をどう設計しますか",
                 ("ext#28",), keyword="安全設計"),
    CoreQuestion("C33", "ai",
                 "AI が期待した効果を出さなかった場合、また AI が適さないと分かった場合、どうしますか",
                 ("ans#4-6", "ans#4-7"), keyword="撤退判断"),
    CoreQuestion("C34", "ai",
                 "AI の実際の業務価値を、技術デモとどう区別し、経営層にどう報告しますか",
                 ("ans#4-10", "ans#4-11"), keyword="経営報告"),
    CoreQuestion("C50", "ai",
                 "AI を使ってプロダクトをどう作っていますか。技術的にはどう実装していますか",
                 (), jd_dependent=True, keyword="技術実装"),

    # ----------------------------------------------------------- agent
    CoreQuestion("C35", "agent", "開発した AI Agent は、どのような問題を解決しましたか",
                 ("ans#5-1", "pm#1-6"), keyword="解決課題"),
    CoreQuestion("C36", "agent",
                 "なぜ通常の生成 AI 機能ではなく、Agent を使う必要があったのですか",
                 ("ans#5-2",), keyword="必然性"),
    CoreQuestion("C37", "agent", "Agent の全体フローとアーキテクチャをどのように設計しましたか",
                 ("ans#5-3",), keyword="全体設計"),
    CoreQuestion("C38", "agent", "Agent はどのようなツールやデータを利用できますか",
                 ("ans#5-4",), keyword="ツール"),
    CoreQuestion("C39", "agent", "Agent の誤生成や誤操作を、どのように防いでいますか",
                 ("ans#5-6",), keyword="誤動作防止"),
    CoreQuestion("C40", "agent",
                 "モデルの失敗や API 異常、出力品質の不安定さにはどう対処していますか",
                 ("ans#5-7",), keyword="障害対応"),
    CoreQuestion("C41", "agent", "Agent の正確性・効率・業務価値をどのように評価しましたか",
                 ("ans#5-8", "ans#5-9"), keyword="効果評価"),
    CoreQuestion("C42", "agent", "Agent の開発・運用で最も難しかった問題は何ですか",
                 ("ans#5-10",), keyword="開発難所"),

    # ---------------------------------------------------------- client
    CoreQuestion("C43", "client", "フリーランス期間は、主にどのような仕事を担当しましたか",
                 ("ans#6-1",), keyword="受託業務"),
    CoreQuestion("C44", "client",
                 "外部の顧客やパートナーと調整した経験を、進め方も含めて教えてください",
                 ("ans#6-2", "pm#3-7"), keyword="社外調整"),
    CoreQuestion("C45", "client", "顧客の要求が不明確な場合、真のニーズをどのように確認しますか",
                 ("ans#6-3", "pm#3-8"), keyword="要求把握"),
    CoreQuestion("C46", "client", "AI の能力・限界・導入リスクを、顧客にどう説明しますか",
                 ("ans#6-5",), keyword="限界説明"),
    CoreQuestion("C47", "client", "顧客の期待が高すぎる場合、どのように期待値を調整しますか",
                 ("ans#6-6",), keyword="期待調整"),
    CoreQuestion("C48", "client",
                 "PoC が成功しても顧客社内で正式導入が進まない場合、どう対応しますか",
                 ("ans#6-8",), keyword="導入推進"),

    # --------------------------------------------------------- closing
    CoreQuestion("C49", "closing", "最後に、何か質問はありますか",
                 ("ans#6-10",), jd_dependent=True, keyword="逆質問"),
)


def by_category() -> dict[str, list[CoreQuestion]]:
    grouped: dict[str, list[CoreQuestion]] = {key: [] for key in CATEGORY_ORDER}
    for item in CORE:
        grouped[item.category].append(item)
    return grouped


def get(qid: str) -> CoreQuestion:
    for item in CORE:
        if item.qid == qid:
            return item
    raise KeyError(qid)


def keyword_of(qid: str) -> str:
    """正典 qid → キーワード。未定義の qid は空文字（描画側が質問文だけ出す）。"""

    for item in CORE:
        if item.qid == qid:
            return item.keyword
    return ""
