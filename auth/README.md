# Cookie 匯出指引

LinkedIn / Wantedly / BizReach 三站需要登入才能精準爬取。把 Chrome 已登入的 cookie 匯出到本資料夾。

## 一次設定

1. **安裝 Chrome 擴充：[Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)**
   （比 EditThisCookie 更新更穩，介面類似）

2. 對每個站逐一執行：

   | 站 | 登入頁 | 匯出後存檔名 |
   |---|---|---|
   | LinkedIn | https://www.linkedin.com/ | `cookies/linkedin.json` |
   | Wantedly | https://www.wantedly.com/ | `cookies/wantedly.json` |
   | BizReach | https://www.bizreach.jp/ | `cookies/bizreach.json` |

   **步驟（每站重複）**：
   - 在 Chrome 開啟該站，**確認已登入**（看右上角是頭像而非「登入」鈕）
   - 點工具列的 Cookie-Editor 圖示
   - 右下角點「Export → Export as JSON」（會複製到剪貼簿）
   - 開啟終端：
     ```bash
     pbpaste > ~/Documents/project/jp-pm-jobs/auth/cookies/<site>.json
     ```

3. **驗證**：
   ```bash
   cd ~/Documents/project/jp-pm-jobs
   ls auth/cookies/    # 應有 linkedin.json / wantedly.json / bizreach.json
   python3 scrape.py --source linkedin --max-pages 1
   ```
   若出現「cookie 無效或過期 — 跳轉到 /login」就要重抓。

## 安全

- `auth/cookies/` 已在 `.gitignore`，不會進 git
- Cookie 等同於密碼，洩漏可讓他人登入你的帳號
- 約每 30 天重抓一次（LinkedIn / BizReach 的 session 通常 1-3 個月過期）

## 為什麼不用帳密自動登入？

三個站都有反 bot 機制（reCAPTCHA / 行為偵測），Playwright 直接 fill 帳密會被擋並可能觸發帳號鎖。Cookie reuse 是務實做法。
