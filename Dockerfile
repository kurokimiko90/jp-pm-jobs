# jp-pm-jobs dashboard 容器化 — 只跑「網站」（分析/追蹤/文件產出）。
#
# 注意：LinkedIn / BizReach 等需登入站點的爬蟲用 CDP 連你本機已登入的 Chrome，
# 本質上需要互動登入，不適合塞進容器。爬蟲請在 host 上跑（見 README），
# 容器與 host 共用同一份 data/jobs.sqlite（volume mount）即可互通。

# ── Stage 1: 前端 build ──
FROM node:20-slim AS frontend
WORKDIR /app/dashboard/frontend
COPY dashboard/frontend/package*.json ./
RUN npm ci --silent
COPY dashboard/frontend/ ./
RUN npm run build

# ── Stage 2: runtime ──
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
# playwright/playwright-stealth 僅為 import 相容保留；容器內不裝瀏覽器二進位
# （爬蟲不在容器內跑，見上方說明）。
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /app/dashboard/frontend/dist ./dashboard/frontend/dist

ENV DASHBOARD_PORT=8000
EXPOSE 8000
WORKDIR /app/dashboard/backend
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${DASHBOARD_PORT}"]
