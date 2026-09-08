"""知識庫載入與檢索 — growth pipeline 的事實錨（Gate B 白名單來源）。

三個 YAML：
  growth_cases.yaml      真實增長案例（唯一可指名道姓引用數字的清單）
  metric_frameworks.yaml 指標框架骨架
  report_catalog.yaml    報告目錄（頻度/讀者/決策用途）

檢索純規則、零 LLM：從 JD 文字打標籤 → 匹配案例的 business_model / lever。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# JD 關鍵字 → business_model 標籤（日英中混合，JD 多為日文）
_MODEL_PATTERNS: dict[str, list[str]] = {
    "ai_product": [r"生成 ?AI", r"LLM", r"AI ?エージェント", r"機械学習", r"ML", r"AI ?SaaS",
                   r"プロンプト", r"RAG", r"推論", r"AI ?活用", r"人工知能"],
    "b2b_saas": [r"SaaS", r"BtoB", r"B2B", r"法人向け", r"企業向け", r"サブスクリプション",
                 r"エンタープライズ", r"業務システム"],
    "b2c": [r"BtoC", r"B2C", r"消費者向け", r"一般ユーザー", r"toC", r"コンシューマ"],
    "plg": [r"PLG", r"プロダクトレッド", r"フリーミアム", r"無料プラン", r"セルフサーブ",
            r"トライアル"],
    "marketplace": [r"マーケットプレイス", r"マッチング", r"両面", r"出品", r"プラットフォーム事業"],
    "fintech": [r"フィンテック", r"FinTech", r"決済", r"金融", r"証券", r"保険", r"銀行",
                r"資産運用"],
    "enterprise": [r"大企業", r"エンタープライズ", r"稟議", r"導入支援", r"オンプレ", r"SIer"],
}

# JD 關鍵字 → 增長槓桿標籤
_LEVER_PATTERNS: dict[str, list[str]] = {
    "acquisition": [r"獲得", r"集客", r"リード", r"マーケティング", r"新規顧客", r"認知",
                    r"CAC", r"チャネル"],
    "activation": [r"オンボーディング", r"初回", r"立ち上げ", r"活性化", r"アクティベーション",
                   r"利用開始", r"定着"],
    "retention": [r"継続", r"リテンション", r"解約", r"チャーン", r"定着", r"エンゲージメント",
                  r"LTV"],
    "revenue": [r"収益", r"売上", r"マネタイズ", r"価格", r"プライシング", r"ARR", r"MRR",
                r"課金"],
    "referral": [r"紹介", r"口コミ", r"バイラル", r"招待", r"レコメンド"],
    "expansion": [r"アップセル", r"クロスセル", r"拡大", r"エクスパンション", r"NRR", r"追加契約"],
    "efficiency": [r"効率", r"コスト", r"生産性", r"自動化", r"工数削減", r"粗利"],
}

# JD 關鍵字 → 成長階段
_STAGE_PATTERNS: dict[str, list[str]] = {
    "pre_pmf": [r"立ち上げ", r"新規事業", r"0→1", r"ゼロイチ", r"PMF", r"立上げ", r"新規プロダクト"],
    "pmf_to_scale": [r"グロース", r"拡大", r"1→10", r"スケール", r"成長期"],
    "scale": [r"大規模", r"既存プロダクト", r"改善", r"10→100"],
}

_JP_MARKET_PATTERNS = [r"日本", r"国内", r"日系", r"稟議", r"商習慣"]


def _read(filename: str) -> dict:
    path = KNOWLEDGE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"知識庫が見つかりません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def load_cases() -> list[dict]:
    return _read("growth_cases.yaml").get("cases", []) or []


@lru_cache(maxsize=1)
def load_frameworks() -> dict:
    return _read("metric_frameworks.yaml").get("frameworks", {}) or {}


@lru_cache(maxsize=1)
def load_reports() -> list[dict]:
    return _read("report_catalog.yaml").get("reports", []) or []


def _match_tags(text: str, patterns: dict[str, list[str]]) -> list[str]:
    hits: list[tuple[str, int]] = []
    for tag, pats in patterns.items():
        n = sum(len(re.findall(p, text, flags=re.I)) for p in pats)
        if n:
            hits.append((tag, n))
    return [t for t, _ in sorted(hits, key=lambda x: -x[1])]


def classify_jd(jd_text: str) -> dict:
    """純規則的 JD 標籤化 — 零 LLM，用於檢索與 prompt 的先驗提示。"""
    text = jd_text or ""
    models = _match_tags(text, _MODEL_PATTERNS)
    levers = _match_tags(text, _LEVER_PATTERNS)
    stages = _match_tags(text, _STAGE_PATTERNS)
    return {
        "business_model": models or ["b2b_saas"],
        "lever": levers or ["acquisition", "retention"],
        "growth_stage": stages or ["pmf_to_scale"],
        "japan_market": any(re.search(p, text) for p in _JP_MARKET_PATTERNS),
    }


def select_cases(tags: dict, limit: int = 8) -> list[dict]:
    """依標籤挑案例。評分：商模命中 3 分、槓桿 2 分、階段 1 分、日本市場 +2。"""
    models = set(tags.get("business_model") or [])
    levers = set(tags.get("lever") or [])
    stages = set(tags.get("growth_stage") or [])
    jp = bool(tags.get("japan_market"))

    scored: list[tuple[int, dict]] = []
    for case in load_cases():
        score = 0
        score += 3 * len(models & set(case.get("business_model") or []))
        score += 2 * len(levers & set(case.get("lever") or []))
        score += 1 * len(stages & set(case.get("growth_stage") or []))
        if jp and str(case.get("id", "")).startswith("jp_"):
            score += 2
        if case.get("confidence") == "high":
            score += 1
        if score > 0:
            scored.append((score, case))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [c for _, c in scored[:limit]]


def case_names() -> set[str]:
    """Gate B 白名單：可被指名引用的公司/案例名。"""
    names: set[str] = set()
    for case in load_cases():
        for key in ("company", "name"):
            val = (case.get(key) or "").strip()
            if val:
                names.add(val)
                # 「株式会社X」「X, Inc.」等變體的核心詞
                core = re.split(r"[（(,、]", val)[0].strip()
                if core:
                    names.add(core)
    return names


def render_cases(cases: list[dict]) -> str:
    """把案例渲染成 prompt 用的 Markdown 區塊。"""
    out: list[str] = []
    for c in cases:
        out.append(
            f"### [{c['id']}] {c.get('name')}（{c.get('company')} / {c.get('era')}）\n"
            f"- 状況: {c.get('situation', '').strip()}\n"
            f"- 施策: {c.get('action', '').strip()}\n"
            f"- 結果: {c.get('result', '').strip()}\n"
            f"- なぜ効いたか: {c.get('why_it_works', '').strip()}\n"
            f"- 移植できる条件: {', '.join(c.get('transferable_when') or [])}\n"
            f"- 注意点: {c.get('caveats', '').strip()}\n"
            f"- 出典: {c.get('source')}（確度: {c.get('confidence')}）"
        )
    return "\n\n".join(out)


def render_frameworks(keys: list[str] | None = None) -> str:
    fw = load_frameworks()
    keys = keys or list(fw.keys())
    out: list[str] = []
    for k in keys:
        f = fw.get(k)
        if not f:
            continue
        layers = "\n".join(
            f"  - {l.get('layer')}: {l.get('rule') or l.get('example_shape')}"
            for l in (f.get("structure") or [])
        )
        out.append(
            f"### {f.get('name')}（{k}）\n"
            f"- 使う場面: {f.get('when')}\n{layers}\n"
            f"- アンチパターン: {f.get('anti_pattern')}\n"
            f"- 出典: {f.get('source')}"
        )
    return "\n\n".join(out)


def render_reports(phases: list[str] | None = None) -> str:
    out: list[str] = []
    for r in load_reports():
        if phases and not (set(phases) & set(r.get("phase") or [])):
            continue
        out.append(
            f"### [{r['id']}] {r.get('name')}\n"
            f"- 頻度: {r.get('cadence')}\n"
            f"- 読者: {', '.join(r.get('audience') or [])}\n"
            f"- 決定用途: {r.get('decision')}\n"
            f"- 入力: {', '.join(r.get('inputs') or [])}\n"
            f"- 骨子: {' / '.join(r.get('skeleton') or [])}\n"
            f"- アンチパターン: {r.get('anti_pattern')}"
        )
    return "\n\n".join(out)
