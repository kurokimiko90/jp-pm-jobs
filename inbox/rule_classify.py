"""規則式郵件分類（暫代 LLM）：用寄件網域 + 標題/本文開頭關鍵詞判斷類別。

免 LLM token、免等待，用於 classify.classify_mail()（LLM 版）暫停使用期間。
回傳格式與 classify_mail() 相同，方便 reply.py 直接替換呼叫。
"""
from __future__ import annotations

CATEGORIES = [
    "application_ack", "schedule_confirmed", "interview_invite", "scheduling",
    "rejection", "initial_contact", "offer", "other",
]

# 排除清單：命中即直接判 other（不生成草稿），優先權最高。
# 「パスワード通知メール」是加密附件的密碼通知信，本身不需回覆（本體另有一封才需要）。
# 「新着おすすめ求人」「オファーが届いています」都是 r-agent 自動推薦模板信，非真人接觸。
# 「入力完了のお知らせ」是日程調整表單送出後的系統自動確認通知，動作已完成，不需回覆。
_EXCLUDE = [
    "パスワード通知メール", "新着おすすめ求人", "メールマガジン", "配信停止はこちら",
    "オファーが届いています", "お仕事情報をお届け", "スカウト", "入力完了のお知らせ",
]

# 寄件信箱命中即判 other：Indeed 職缺推播、LinkedIn 系統通知、paiza 電子報、
# Adecco 職缺廣告信等自動群發信箱，非真人接觸。
_EXCLUDE_SENDERS = [
    "noreply", "no-reply", "donotreply", "messages-noreply", "_news@", "jobinfo@",
]

# 依序比對，越前面優先權越高（先排除 rejection/offer 等明確結果，避免被其他關鍵詞蓋過）
_RULES: list[tuple[str, list[str]]] = [
    # 応募受付の自動返信（r-agent）。必須排最前：本文の注意書きに
    # 「書類選考お見送りのご連絡は…」が含まれ、後置すると rejection に誤判される。
    # この信不需回覆，由 inbox.application_ack が applications へ自動記録する。
    ("application_ack", ["応募手続きを承りました"]),
    # 日程確定通知（r-agent 日程調整システム / TimeRex）。必須排最前：
    # 「日程調整が完了しました」含「日程調整」，後置會被 scheduling 截走。
    # 此類信不需回覆，由 inbox.schedule 抽出日時寫入 applications.next_event。
    # 「選考情報変更のご連絡」は面接URL/場所が確定した通知（返信不要）。r-agentの
    # 「日程調整」を含む定型文言のせいで後置すると scheduling に截走される。
    ("schedule_confirmed", [
        "日程確定のお知らせ", "日程が確定しました", "日程調整が完了しました",
        "選考情報変更のご連絡",
    ]),
    # r-agent の実際の定型文言は「ご期待に添えない結果となりました」「残念ですが」であり、
    # 旧キーワード（ご期待に沿うことができ／残念ながら）とは字面が違うため一致しなかった
    # （実測で initial_contact に誤分類されていた）。件名そのものも最優先キーワードとして
    # 追加：「書類選考結果のご連絡」「選考結果のご連絡」は r-agent の不合格通知に固定で使われる。
    ("rejection", [
        "書類選考結果のご連絡", "選考結果のご連絡", "ご期待に添えない",
        "不採用", "見送り", "残念ながら", "ご期待に沿うことができ",
        "選考を通過することができ", "今回はご縁", "採用を見送", "見送らせて",
    ]),
    ("offer", [
        "内定のご連絡", "内定通知", "内定のお知らせ", "オファーレター",
        "労働条件通知書", "内定承諾",
    ]),
    ("scheduling", [
        "日程調整", "候補日", "ご都合の良い", "面接日時のご相談",
        "スケジュール調整", "面接可能な日", "【ご対応願】", "事前確認",
        "確認のお願い", "年収確認",
    ]),
    ("interview_invite", [
        "面接のご案内", "面接のお願い", "一次面接", "二次面接", "最終面接",
        "面談のご案内", "カジュアル面談", "面接について", "面接日程のご案内", "面談",
    ]),
    # 「キャリアアドバイザー」「気になるお仕事」不列入：行銷電子報常見版型用語，誤中率高。
    # 「スカウト」在排除清單（用戶指示：スカウト信不需回覆）。
    ("initial_contact", [
        "求人のご紹介", "ポジションのご紹介",
        "案件のご紹介", "求人票", "非公開求人",
        "経験にマッチする求人", "アンケート",
    ]),
]

# 已知招募平台/仲介網域 — 只有這些網域（或已對到已投遞職缺）才會進入關鍵詞判斷。
# r-agent.com = リクルートエージェント担当キャリアアドバイザーの實際寄件網域，非 recruit.co.jp。
# geekneer.com = ギークニア転職サポート，已確認為真實仲介往來紀錄。
_AGENT_DOMAINS = [
    "r-agent.com", "jac-recruitment.jp", "indeedemail.com", "geekneer.com",
    "bizreach.jp", "doda.jp", "type.jp", "mynavi.jp", "en-japan.com",
    "green-japan.com", "wantedly.com", "linkedin.com", "recruit.co.jp",
    "indeed.com", "levtech.jp", "timerex.net",
]


def classify_by_rules(mail: dict) -> dict:
    """用寄件網域 + 標題/本文前 500 字關鍵詞判斷類別。回傳 {category, confidence, summary}。

    網域關卡優先：寄件網域不在已知招募平台清單的一律 other，完全不看關鍵詞內容
    （避免電子報/廣告信裡的敬語片語誤觸發任何類別）。
    """
    subject = mail.get("subject", "") or ""
    body_head = (mail.get("body", "") or "")[:500]
    text = f"{subject} {body_head}"
    domain = (mail.get("sender_domain", "") or "").lower()
    sender = (mail.get("sender_email") or mail.get("sender") or "").lower()

    for kw in _EXCLUDE:
        if kw in text:
            return {"category": "other", "confidence": 0.7, "summary": f"排除清單命中「{kw}」"}

    if any(s in sender for s in _EXCLUDE_SENDERS):
        return {"category": "other", "confidence": 0.7, "summary": "自動群發信箱（noreply 系），非真人接觸"}

    known_domain = any(d in domain for d in _AGENT_DOMAINS)
    # 不用 inbox.match.match_job() 當門檻：它拿公司名正規化後做全文子字串比對，
    # 短公司名（如「coco」「SCO」）常誤中無關網域（adecco.co.jp 含"coco"、giants.co.jp 含"sco"）。
    # 只信任明確網域清單。

    if not known_domain:
        return {
            "category": "other",
            "confidence": 0.3,
            "summary": f"寄件網域非已知招募平台（{domain}），略過關鍵詞判斷",
        }

    for category, keywords in _RULES:
        for kw in keywords:
            if kw in text:
                return {
                    "category": category,
                    "confidence": 0.6,
                    "summary": f"關鍵詞命中「{kw}」",
                }

    return {
        "category": "initial_contact",
        "confidence": 0.4,
        "summary": f"寄件網域屬招募平台（{domain}），無明確關鍵詞",
    }
