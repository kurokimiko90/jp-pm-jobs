"""tools.deid compact 模式 — 用合成 profile 驗證瘦身與 PII 去除，不碰真實資料檔。"""

from tools.deid import build_deid_profile

FAKE_PROFILE = {
    "identity": {"name_ja": "山田太郎", "name_romaji": "Taro Yamada",
                 "birth_year": 1990, "base": "東京"},
    "contact": {"email": "taro@example.com", "github": "github.com/taro"},
    "positioning": "山田太郎はAI PdM",
    "experience": [{
        "company": "X社", "title": "PdM",
        "plain_summary": "決済基盤のPdM",
        "plain_highlights": ["月間100万件処理"],
        "key_achievements": ["障害率半減"],
        "highlights": ["とても長い敘事" * 50],
    }],
    "proof_projects": {"theme": "AI", "projects": [{
        "name": "proj-a", "plain_summary": "LLM pipeline",
        "highlights": ["長い敘事" * 50],
    }]},
    "ai_engineering": {"summary": "multi-LLM", "items": [{
        "name": "item-a", "before_after": "3日→1時間",
        "bridge_to_current": "面試敘事" * 30, "team_scaling": "敘事" * 30,
    }]},
    "developer_tool_design": {
        "summary": "工具設計",
        "tools": [{"name": "tool-a", "detail": "很長的說明" * 30}],
        "langsmith_comparison": {"a": "長比較" * 50},
    },
    "differentiators": {"self_pr_jp": "自我PR敘事" * 30,
                        "career_vision": ["2年後…"]},
    "match_summary": {
        "strengths_i_bring": ["決済PdM経験"],
        "known_gaps": ["英語スピーキング（TOEIC 495）"],
        "gap_bridge_map": [{"gap_theme": "x", "counter": "面試反駁" * 30}],
        "negotiation_anchor": "900万+",
    },
}


def test_pii_removed_in_both_modes():
    for compact in (False, True):
        text = build_deid_profile(FAKE_PROFILE, compact=compact)
        assert "山田太郎" not in text
        assert "Taro Yamada" not in text
        assert "taro@example.com" not in text
        assert "1990" not in text
        assert "本人" in text  # 姓名被替換


def test_compact_drops_verbose_keeps_facts():
    full = build_deid_profile(FAKE_PROFILE)
    compact = build_deid_profile(FAKE_PROFILE, compact=True)
    assert len(compact) < len(full)
    # 冗長敘事已砍
    assert "とても長い敘事" not in compact
    assert "面試敘事" not in compact
    assert "長比較" not in compact
    assert "很長的說明" not in compact
    # 事實保留
    assert "決済基盤のPdM" in compact
    assert "月間100万件処理" in compact
    assert "障害率半減" in compact
    assert "3日→1時間" in compact
    assert "tool-a" in compact


def test_compact_does_not_mutate_source():
    build_deid_profile(FAKE_PROFILE, compact=True)
    assert "highlights" in FAKE_PROFILE["experience"][0]
    assert "langsmith_comparison" in FAKE_PROFILE["developer_tool_design"]


def test_facts_only_drops_narrative_keeps_match_facts():
    compact = build_deid_profile(FAKE_PROFILE, compact=True)
    facts = build_deid_profile(FAKE_PROFILE, compact=True, facts_only=True)
    assert len(facts) < len(compact)
    # 面試敘事/報價已砍
    assert "自我PR敘事" not in facts
    assert "面試反駁" not in facts
    assert "900万+" not in facts
    assert "月間100万件処理" not in facts  # experience plain_highlights
    # 要件匹配事實保留
    assert "決済PdM経験" in facts          # strengths_i_bring
    assert "TOEIC 495" in facts            # known_gaps（誠實 gap 素材）
    assert "障害率半減" in facts           # key_achievements
    assert "LLM pipeline" in facts         # proof_projects plain_summary


def test_facts_only_does_not_mutate_source():
    build_deid_profile(FAKE_PROFILE, compact=True, facts_only=True)
    assert "differentiators" in FAKE_PROFILE
    assert "gap_bridge_map" in FAKE_PROFILE["match_summary"]
    assert "plain_highlights" in FAKE_PROFILE["experience"][0]
