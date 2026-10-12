"""tools/deid.build_deid_profile — PII 去識別化白名單（安全紅線，送 LLM 前必經）。"""
from tools.deid import DEID_KEYS, build_deid_profile

PROFILE = {
    "identity": {"name_ja": "山田太郎", "name_romaji": "Taro Yamada",
                 "birth_year": 1990, "base": "東京都"},
    "contact": {"email": "taro@example.com", "github": "https://github.com/taro"},
    "compensation": {"current_annual": 800, "target_range": "900-1200"},
    "positioning": {"title": "AI PM", "one_liner": "山田太郎はAI PM。連絡: taro@example.com"},
    "skills": ["roadmap", "LLM evals"],
}


class TestWhitelist:
    def test_only_whitelisted_keys_survive(self):
        out = build_deid_profile(PROFILE)
        assert "positioning:" in out
        assert "skills:" in out
        # 非白名單 key 整塊不出現
        for banned in ("identity", "contact", "compensation", "birth_year", "current_annual"):
            assert banned not in out

    def test_whitelist_matches_claude_md_contract(self):
        # CLAUDE.md「PII 去識別化規則」宣告的白名單，防止兩邊漂移
        assert set(DEID_KEYS) == {
            "positioning", "domains", "skills", "experience", "proof_projects",
            "ai_engineering", "differentiators", "match_summary", "education",
            "certifications", "languages", "sier_experience", "developer_tool_design",
        }


class TestPiiScrub:
    def test_name_replaced_with_honnin(self):
        out = build_deid_profile(PROFILE)
        assert "山田太郎" not in out
        assert "本人はAI PM" in out

    def test_contact_values_masked_inside_whitelisted_text(self):
        out = build_deid_profile(PROFILE)
        assert "taro@example.com" not in out
        assert "***" in out

    def test_address_not_leaked(self):
        out = build_deid_profile(PROFILE)
        assert "東京都" not in out
