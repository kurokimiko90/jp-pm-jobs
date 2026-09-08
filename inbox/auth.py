"""Gmail OAuth — 用既有 CDP 瀏覽器（BizReach profile，已登入使用者 Google 帳號）完成授權。

授權流程（不開系統預設瀏覽器，不必重登 Google）：
  1. 啟動 local redirect server（loopback；Desktop OAuth client 允許任意 port）
  2. CDP 連既有瀏覽器 → 在「已登入的帳號」開 Google 授權頁
  3. 使用者點「許可 / Allow」→ Google redirect 回 localhost
  4. server 收到 code → 換 token → 存 data/secrets/token.json

Scope: gmail.readonly + gmail.compose + calendar.events。
  ⚠️ gmail.compose 技術上含 send 能力，但本專案程式碼「只呼叫 drafts().create()，
  絕不呼叫 messages/drafts 的 send」。永不自動寄信靠程式紀律保證。
  calendar.events 僅能新增/修改/刪除事件，不含讀取其他行事曆內容的權限。

用法:
    python3 -m inbox.auth          # 跑授權（首次）+ smoke test（列收件匣 5 封）

前置:
    Google Cloud Console → APIs & Services → 啟用 Gmail API + Google Calendar API →
    Credentials → 建 OAuth client（類型必須是 "Desktop app"）→ 下載 JSON →
    存成 data/secrets/credentials.json。
    consent screen 為「測試中」時，需把自己的 Google 帳號加入 Test users。
    既有 token.json 若是舊 scope（無 calendar.events）需重跑本模組補授權。
"""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

from inbox._cdp import BIZREACH_PORT, BIZREACH_PROFILE, ensure_browser

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]

SECRETS_DIR = Path(__file__).parent.parent / "data" / "secrets"
CREDENTIALS_FILE = SECRETS_DIR / "credentials.json"
TOKEN_FILE = SECRETS_DIR / "token.json"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


class _CallbackHandler(BaseHTTPRequestHandler):
    """收一個 OAuth redirect，把 ?code= 存到 class attribute。"""

    code: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = (qs.get("code") or [None])[0]
        ok = _CallbackHandler.code is not None
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "認証完了。このタブを閉じてください。" if ok else "認証失敗。"
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode("utf-8"))

    def log_message(self, *args):  # 靜音 access log
        pass


def authorize() -> Credentials:
    """跑一次 OAuth，用系統預設瀏覽器開授權頁，存 token.json，回傳 Credentials。"""
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"缺少 {CREDENTIALS_FILE}\n"
            "→ Google Cloud Console 建 OAuth client（Desktop app）下載 JSON 放到此路徑。"
        )

    _CallbackHandler.code = None
    port = _free_port()
    flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    server = HTTPServer(("localhost", port), _CallbackHandler)
    waiter = threading.Thread(target=server.handle_request, daemon=True)
    waiter.start()

    ensure_browser(BIZREACH_PORT, BIZREACH_PROFILE, "https://accounts.google.com")
    print("\n→ BizReach Chrome で授権頁を開きます。『許可 / Allow』を押してください。")
    print("  （若見『このアプリは確認されていません』→ 詳細 → 安全でないページに移動）\n")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{BIZREACH_PORT}")
        page = browser.contexts[0].new_page()
        page.goto(auth_url)
        waiter.join(timeout=300)
        page.close()

    code = _CallbackHandler.code
    if not code:
        raise RuntimeError("未取得授權 code（逾時或使用者取消）。")
    flow.fetch_token(code=code)
    creds = flow.credentials
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"✅ token 已存 {TOKEN_FILE}")
    return creds


def get_credentials() -> Credentials:
    """讀 token.json；過期自動 refresh；無 token 或 scope 不足（如新增 calendar）則跑 authorize()。"""
    creds = None
    if TOKEN_FILE.exists():
        # 不傳 scopes 參數：傳了會覆蓋 creds.scopes 為傳入值，讀不到檔案裡實際核准的範圍
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        if not set(SCOPES) <= set(creds.scopes or []):
            return authorize()  # 舊 token scope 不足，即使未過期也需強制重新走一次同意畫面
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
        return creds
    return authorize()


def get_service():
    """回傳 Gmail API service（v1），供 fetch/draft 模組使用。"""
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def _smoke_test() -> None:
    """驗收：列出收件匣最新 5 封標題。"""
    svc = get_service()
    resp = svc.users().messages().list(userId="me", maxResults=5, q="in:inbox").execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    print(f"\n收件匣最新 {len(ids)} 封：")
    for mid in ids:
        msg = (
            svc.users()
            .messages()
            .get(userId="me", id=mid, format="metadata", metadataHeaders=["Subject", "From"])
            .execute()
        )
        hdrs = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        print(f"  • {hdrs.get('Subject', '(無主旨)')}  ← {hdrs.get('From', '')}")


if __name__ == "__main__":
    _smoke_test()
