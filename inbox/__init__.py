"""Gmail 招聘郵件回覆助手（System Layer）。

掃收件匣 → LLM 分類招聘郵件 → 對應已投職缺 → 生成日文回覆草稿 → 存成 Gmail 草稿。

核心紀律：
- **永不自動寄信**：程式只呼叫 `drafts().create()`，絕不呼叫 send API。
  （OAuth scope gmail.compose 技術上含 send 能力，靠程式紀律而非 scope 限制。）
- **去識別化**：草稿 prompt 一律經 tools.deid.build_deid_profile()。
- **零編造**：時段/薪資等具體數字留 placeholder，不編造。

CLI:
    python3 -m inbox.auth          # 一次性 OAuth 授權 + smoke test
"""
