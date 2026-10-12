"""LinkedIn Messaging 未讀對話抓取（CDP）。

⚠ DOM 選擇器基於 LinkedIn 2026-08 時點的一般結構推測，未經實機驗證（改版頻率高，
selector 常失效）。**第一次跑務必用 --dry-run 人工核對輸出**，抓不到東西時比照
scrapers/linkedin_jp.py 的做法調整這裡的 selector，不要無條件信任本模組的抓取結果。

連線沿用既有 linkedin_cdp 設定（config/scraping.yaml 的 linkedin_cdp，預設 port 9253，
profile ~/.chrome-linkedin，與 scrapers/linkedin_jp.py 職缺爬蟲共用同一個已登入 Chrome）。
"""
from __future__ import annotations

import random
import time

from playwright.sync_api import Page

MESSAGING_URL = "https://www.linkedin.com/messaging/"
_LOGIN_INDICATORS = ("/login", "/uas/login", "/checkpoint")

_CONV_ITEM_SELECTOR = "li.msg-conversation-listitem, div.msg-conversations-container li"
_CONV_LIST_SELECTOR = f"ul.msg-conversations-container__conversations-list, {_CONV_ITEM_SELECTOR}"


def _polite_sleep(min_s: float = 1.5, max_s: float = 3.5) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _is_login_page(page: Page) -> bool:
    return any(ind in page.url for ind in _LOGIN_INDICATORS)


def fetch_unread(page: Page, max_conversations: int = 20) -> list[dict]:
    """開 LinkedIn Messaging，抓取未讀對話（含對方最新訊息全文）。

    回傳 [{conversation_id, sender_name, sender_headline, profile_url,
           last_message_at, body_raw}]。未登入或抓不到清單時回傳 []（呼叫端不中斷）。
    """
    try:
        page.goto(MESSAGING_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  [linkedin_inbox] goto 失敗 {type(e).__name__}")
        return []
    _polite_sleep(2, 4)
    if _is_login_page(page):
        print("  [linkedin_inbox] 未登入 — 跳轉到登入頁，請手動登入 CDP profile 後重試")
        return []

    try:
        page.wait_for_selector(_CONV_LIST_SELECTOR, timeout=15000)
    except Exception:
        print("  [linkedin_inbox] 找不到對話清單（DOM 可能已改版，需人工核對 selector）")
        return []
    _polite_sleep(1, 2)

    # 列表頁只抓輕量資訊（index + 姓名 + 是否未讀），逐一點開才抓正文，
    # 避免一次性把整頁未展開的訊息都當成「最新訊息」。
    cards = page.evaluate(
        """
        () => {
            const items = document.querySelectorAll(
                'li.msg-conversation-listitem, div.msg-conversations-container li'
            );
            const out = [];
            items.forEach((el, idx) => {
                const badge = el.querySelector(
                    '.notification-badge, [data-unread="true"]'
                );
                const unreadClass = el.classList.contains('msg-conversation-listitem--unread');
                if (!badge && !unreadClass) return;
                const nameEl = el.querySelector(
                    '.msg-conversation-listitem__participant-names, .truncate'
                );
                out.push({ idx, sender_name: nameEl ? nameEl.innerText.trim() : '' });
            });
            return out;
        }
        """
    )
    if not cards:
        print("  [linkedin_inbox] 未讀對話：0")
        return []

    results: list[dict] = []
    for c in cards[:max_conversations]:
        try:
            elements = page.query_selector_all(_CONV_ITEM_SELECTOR)
            if c["idx"] >= len(elements):
                continue
            elements[c["idx"]].click()
        except Exception as e:
            print(f"  [linkedin_inbox] 點擊對話失敗 {type(e).__name__}: {c.get('sender_name', '')}")
            continue
        _polite_sleep(1.5, 3)

        conversation_id = ""
        if "/thread/" in page.url:
            conversation_id = page.url.split("/thread/")[-1].split("/")[0].split("?")[0]
        if not conversation_id:
            # thread id が URL に出ない版のフォールバック（姓名ベースの擬似 ID）
            conversation_id = f"name-{c['sender_name']}"

        detail = page.evaluate(
            """
            () => {
                const headlineEl = document.querySelector(
                    '.msg-thread__link-to-profile-headline, .artdeco-entity-lockup__subtitle'
                );
                const profileEl = document.querySelector('a.msg-thread__link-to-profile');
                const bubbles = document.querySelectorAll(
                    '.msg-s-event-listitem__body, .msg-s-message-list__event .msg-s-event-listitem__body'
                );
                const last = bubbles.length ? bubbles[bubbles.length - 1].innerText.trim() : '';
                return {
                    sender_headline: headlineEl ? headlineEl.innerText.trim() : '',
                    profile_url: profileEl ? profileEl.href : '',
                    body_raw: last,
                };
            }
            """
        )
        results.append({
            "conversation_id": conversation_id,
            "sender_name": c["sender_name"],
            "sender_headline": detail.get("sender_headline", ""),
            "profile_url": detail.get("profile_url", ""),
            "last_message_at": "",  # LinkedIn DOM は相対時刻表記のみ・構造化して取れないため空
            "body_raw": detail.get("body_raw", ""),
        })
        _polite_sleep(1, 2)

    print(f"  [linkedin_inbox] 未讀對話抓取完成：{len(results)} 筆")
    return results
