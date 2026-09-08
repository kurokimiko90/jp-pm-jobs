# dashboard/frontend/ — Dashboard Frontend

14 個頁面，hash 路由（無路由庫，自製 ~100 行 hook）：頁面/hub tab 進 hash path（`#/jobs/recommend`、`#/applications/direct`），列表頁篩選進 hash query（`#/jobs?min=80`）——重整/書籤/分享皆可還原，見 `src/filters/useHashFilters.ts`。頁面整合（14→8 模組）已規劃但尚未執行，見下輪待辦。Design tokens 在 `src/theme.ts`（暖白米底 #FFF8F0 + 流金 #F5C842 + Noto Sans JP）。共用元件在 `src/components/ui.tsx`。`JobDrawer` 是全局側邊抽屜。

**列表頁篩選架構（2026-07 統一，各頁禁止自建 options 陣列/手刻 chip）：**
- `src/filters/dict.ts` — 篩選欄位字典：分數檔位、地域、時間、語言、應募狀態（統一 5 選項，後端 `_APPLIED_SQL` 同名）、各頁狀態 tabs 的 key/i18n key/顏色/固定順序
- `src/components/StatusTabs.tsx` — 狀態層 tabs（帶計數），獨立一行在篩選列之上；統計卡（`ReportStat`）一律純展示不可點
- `src/components/FilterBar.tsx` — 具名 slot 強制順序：搜尋 → 分數 → 範圍(地域/時間/語言/類型) → 來源刊登/管道 → 應募狀態 → 更多篩選 → 清除；操作按鈕（批次開JD 等）禁入篩選列，用同檔案的 `ActionBar` 放表格右上
