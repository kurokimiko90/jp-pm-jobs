"""PII 去識別化 — 所有 LLM 腳本統一使用。

白名單機制：只有職業相關 key 會送進 LLM，identity / contact 絕不外送。
姓名→「本人」、聯絡方式→「***」、住所/生年/現年收一律移除。
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PROFILE_PATH = ROOT / "data" / "candidate_profile.yaml"
RESUME_DATA_PATH = ROOT / "resume" / "jp" / "data.yaml"

DEID_KEYS = [
    "positioning", "domains", "skills", "experience",
    "proof_projects", "ai_engineering", "differentiators",
    "match_summary", "education", "certifications", "languages",
    "sier_experience", "developer_tool_design",
]

REMOVE_IDENTITY_KEYS = {"birth_year", "base", "nationality"}


def load_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise SystemExit(f"找不到 {PROFILE_PATH}")
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}


def load_resume_contact() -> dict:
    """姓名/Email/電話/LinkedIn/GitHub 的正確來源 — candidate_profile.yaml 刻意不放 PII，

    這些欄位本來就在 resume/jp/data.yaml（履歴書/職務経歴書渲染用），
    投遞信簽名、附件檔名等本地字串替換一律從這裡讀，絕不送進 LLM。
    """
    if not RESUME_DATA_PATH.exists():
        return {}
    data = yaml.safe_load(RESUME_DATA_PATH.read_text(encoding="utf-8")) or {}
    p = data.get("profile", {}) or {}
    c = data.get("contact", {}) or {}
    name_ja = f"{p.get('family_name', '')} {p.get('given_name', '')}".strip()
    return {
        "name_ja": name_ja,
        "name_romaji": p.get("name_romaji", ""),
        "email": c.get("email", ""),
        "phone": c.get("phone", ""),
        "linkedin": c.get("linkedin", ""),
        "github": c.get("github", ""),
    }


def _compact(subset: dict) -> dict:
    """砍冗長敘事欄位以縮短 prompt（gap 批次等長 prompt 場景用）。

    只刪有 plain_*/summary 對應的重複敘事與面試深掘材料，不丟任何事實：
    - experience[].highlights（保留 plain_summary / plain_highlights / key_achievements）
    - proof_projects.projects[].highlights（保留 plain_summary）
    - ai_engineering.items[].bridge_to_current / team_scaling（面試敘事）
    - developer_tool_design.langsmith_comparison（深掘比較表）、tools 只留名稱
    """
    for e in subset.get("experience") or []:
        if isinstance(e, dict):
            e.pop("highlights", None)
    for pr in (subset.get("proof_projects") or {}).get("projects") or []:
        if isinstance(pr, dict):
            pr.pop("highlights", None)
    for it in (subset.get("ai_engineering") or {}).get("items") or []:
        if isinstance(it, dict):
            it.pop("bridge_to_current", None)
            it.pop("team_scaling", None)
    dt = subset.get("developer_tool_design")
    if isinstance(dt, dict):
        dt.pop("langsmith_comparison", None)
        if isinstance(dt.get("tools"), list):
            dt["tools"] = [t.get("name", "") if isinstance(t, dict) else t
                           for t in dt["tools"]]
    return subset


# facts_only 裁剪時 proof_projects.projects[] 保留的 key（匹配判定夠用）
_PROJECT_FACT_KEYS = ("name", "plain_summary", "stack")


def _facts_only(subset: dict) -> dict:
    """只留「可驗證的匹配事實」— gap 分析等要件判定場景用（在 _compact 之上再砍）。

    砍的全是面試敘事/報價/元標籤，不丟任何可用於要件匹配的事實：
    - differentiators 整塊（認知指標/職涯願景/自我PR/言行一致 — 強項證據已由
      match_summary.strengths_i_bring 覆蓋）
    - match_summary.gap_bridge_map（面試反駁話術）/ negotiation_anchor（報價錨點）
    - ai_engineering.items[].capabilities / deliverables（深層實作細節，一行級事實已留）
    - proof_projects.projects[] 只留 name / plain_summary / stack
    - experience[].plain_highlights（與 key_achievements / plain_summary 重複的展開敘事）
    """
    subset.pop("differentiators", None)
    ms = subset.get("match_summary")
    if isinstance(ms, dict):
        ms.pop("gap_bridge_map", None)
        ms.pop("negotiation_anchor", None)
    for it in (subset.get("ai_engineering") or {}).get("items") or []:
        if isinstance(it, dict):
            it.pop("capabilities", None)
            it.pop("deliverables", None)
    pp = subset.get("proof_projects")
    if isinstance(pp, dict) and isinstance(pp.get("projects"), list):
        pp["projects"] = [
            {k: pr[k] for k in _PROJECT_FACT_KEYS if k in pr}
            if isinstance(pr, dict) else pr
            for pr in pp["projects"]]
    for e in subset.get("experience") or []:
        if isinstance(e, dict):
            e.pop("plain_highlights", None)
    return subset


def build_deid_profile(profile: dict | None = None, compact: bool = False,
                       facts_only: bool = False) -> str:
    """白名單挑職業內容，去除所有 PII，回傳可安全送往 LLM 的 YAML 字串。

    compact=True 時另砍冗長敘事欄位（約 -38% 字元），見 _compact()。
    facts_only=True 時再砍面試敘事/元標籤，只留要件匹配事實（gap 分析用），
    見 _facts_only()。
    """
    if profile is None:
        profile = load_profile()

    subset = {k: copy.deepcopy(profile[k]) for k in DEID_KEYS if k in profile}
    if compact:
        subset = _compact(subset)
    if facts_only:
        subset = _facts_only(subset)
    text = yaml.safe_dump(subset, allow_unicode=True, sort_keys=False)

    identity = profile.get("identity", {}) or {}
    contact = profile.get("contact", {}) or {}

    for v in (identity.get("name_ja"), identity.get("name_romaji")):
        if v:
            text = text.replace(str(v), "本人")

    for v in contact.values():
        if isinstance(v, str) and v:
            text = text.replace(v, "***")

    # 取引先・接続ブランド名（NDA 相当）は PII とは別枠でここ一箇所で遮蔽する。
    # build_deid_profile が全 LLM 呼び出しの入口なので、ここを通せば漏れない。
    from tools.redact import redact
    text, _ = redact(text)
    return text
