# jp-pm-jobs Dashboard

求職戰情室：漏斗 / 今日行動 / 職缺列表 + 評分 Drawer（含選考ステージ管理）/ 評分基準 + 互動調權 / 面接包 / めんせつ道場（題卡・模擬面接・弱點清單）/ 元氣站（check-in・小勝利・努力之牆）/ 履歷。
UI 沿用 btrain design tokens（暖白米底 + 流金 accent + Noto Sans JP）。

## 啟動

```bash
./run.sh                 # build 前端 + 啟動 → http://localhost:8000
./run.sh --skip-build    # 前端沒改時跳過 build
```

開發模式（前端熱更新）：

```bash
cd backend && uvicorn main:app --port 8000 &
cd frontend && npm run dev    # :5173，/api proxy 到 :8000
```

## 結構

- `backend/` FastAPI，SQLite 唯讀（`data/jobs.sqlite` mode=ro）
  - `paths.py` 路徑常數 + PROJECT_ROOT 白名單校驗
  - `scoring_meta.py` 從 `analyzer/jd_scorer.py` 取權重常數
  - `main.py` 全部 API；唯一副作用是 `open-folder/open-file`（macOS `open`，限項目內路徑）
- `frontend/` Vite + React + TS，inline style，tokens 在 `src/theme.ts`

## 資料寫入邊界

- `data/jobs.sqlite`：jobs 表唯讀（mode=ro）；**applications 表例外**，經 `applications.py` 的 rw 連線管理選考ステージ
- `data/practice.sqlite`：道場/元氣專用（題卡、練習紀錄、check-in、小勝利），可寫

## 安全

- `open-folder` / `open-file` / `file`：`Path.resolve()` 後必須在 PROJECT_ROOT 內，否則 403
- `/api/rescore` 只做記憶體內預覽，不寫 DB（處罰係數以 score/base 比值近似還原）
