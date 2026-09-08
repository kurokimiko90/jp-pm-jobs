"""機械閘門 — 生成物の品質を人手を介さず判定する。

theater_script.py の Gate A/B/C 方式を踏襲。閘門を通らない stage は
エラー内容を prompt に添えて再生成（pipeline 側で最大 2 回）、
それでも通らなければ degraded として review レポートに記録する。

  Gate A 構造    必須セクション / 最低ボリューム / 表の有無
  Gate B 事実錨定 案例庫にない社名＋数字の断定を禁止、会社固有数字は facts 由来か {{要確認}}
  Gate C 実行可能 手順に「担当・期間・成果物」、報告に「頻度・読者・用途」
  Gate D 安全     本名などの PII 残存、取引先ブランド名の残存
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tools.pii_gate import scrub_for_external
from tools.redact import scan as redact_scan

from . import context, knowledge

# 数字を伴う断定表現（Gate B の検査対象）
_NUM_RE = re.compile(r"\d[\d,\.]*\s*(%|％|倍|万|億|人|件|社|ヶ月|か月|日|週|年|pt|ポイント)")
# 社名らしき固有名詞（カタカナ 3+ / 英大文字始まり英字 3+ / 株式会社X）
_ORG_RE = re.compile(r"株式会社[\w一-鿿ぁ-ヿ]{1,12}|[A-Z][A-Za-z0-9\.]{2,20}|[ァ-ヴー]{3,15}")
_UNVERIFIED_MARKS = ("{{要確認}}", "{{要出典確認}}", "要出典", "出典未確認")

# 社名判定から除外する一般語（カタカナ／英字の業界用語）
_ORG_STOPWORDS = {
    "プロダクト", "マネージャー", "マネジメント", "ユーザー", "サービス", "ビジネス", "データ",
    "チーム", "メンバー", "エンジニア", "デザイナー", "マーケティング", "セールス", "カスタマー",
    "サクセス", "オンボーディング", "リテンション", "アクティベーション", "エンゲージメント",
    "コホート", "ファネル", "ロードマップ", "スコープ", "リリース", "テスト", "レビュー",
    "ドキュメント", "ステークホルダー", "インタビュー", "アンケート", "セグメント", "ターゲット",
    "コンバージョン", "トライアル", "サブスクリプション", "プラットフォーム", "アーキテクチャ",
    "パフォーマンス", "モニタリング", "ダッシュボード", "ガードレール", "ベースライン",
    "プロジェクト", "スプリント", "バックログ", "ヒアリング", "フィードバック", "ワークショップ",
    "ガバナンス", "コンプライアンス", "セキュリティ", "アカウント", "ライセンス", "サポート",
    "トレーニング", "ナレッジ", "オペレーション", "プロセス", "フレームワーク", "メトリクス",
    "インパクト", "コスト", "リソース", "スケジュール", "マイルストーン", "アウトカム",
    "アウトプット", "インプット", "ロジック", "モデル", "プロンプト", "トークン", "キャッシュ",
    "エラー", "ログ", "アラート", "リスク", "ケース", "パターン", "ポイント", "ステップ",
    "フェーズ", "サイクル", "ペルソナ", "ジャーニー", "シナリオ", "ソリューション", "ニーズ",
    "バリュー", "ミッション", "ビジョン", "カルチャー", "チャネル", "パイプライン", "リード",
    "デモ", "トップ", "ボトム", "ミドル", "シニア", "ジュニア", "レイヤー", "モジュール",
    "コンポーネント", "インターフェース", "アップデート", "バージョン", "デプロイ", "ロールバック",
    "スケール", "グロース", "ハック", "ドリブン", "ファースト", "ラスト", "ベスト", "ベター",
    "ゴールデン", "サンプル", "スコア", "ランキング", "レポート", "サマリ", "サマリー",
    "アジェンダ", "アクション", "タスク", "イシュー", "チケット", "コメント", "メール",
    "ミーティング", "レビュアー", "オーナー", "リーダー", "ロール", "スキル", "キャリア",
    "ナレッジベース", "ワークフロー", "パイロット", "プロトタイプ", "ローンチ", "キャンペーン",
    "コンテンツ", "メディア", "ブランド", "プライシング", "ディスカウント", "アップセル",
    "クロスセル", "チャーン", "リテイン", "ファネル分析", "セッション", "クリック", "インプレッション",
    "AI", "API", "SaaS", "LLM", "RAG", "PoC", "POC", "KPI", "OKR", "ROI", "PMF", "MVP",
    "CAC", "LTV", "ARR", "MRR", "NRR", "GRR", "ARPU", "ARPA", "NPS", "CSAT", "RICE",
    "AARRR", "HEART", "PLG", "QBR", "SLA", "SLO", "IT", "UX", "UI", "PM", "PdM", "PjM",
    "PRD", "FAQ", "PR", "CS", "CRM", "SQL", "BI", "ML", "GPU", "P95", "P50", "TODO",
    "Gate", "Day", "Week", "Month", "Q1", "Q2", "Q3", "Q4", "OK", "NG", "Yes", "No",
}


@dataclass
class GateSpec:
    """stage ごとの閘門設定。"""
    required_sections: list[str] = field(default_factory=list)
    min_chars: int = 800
    min_lines: int = 20
    require_table: bool = False
    # Gate C: 各見出しブロックに必須のキーワード群（いずれか 1 つ含めば OK の集合を並べる）
    block_marker: str = ""            # 例 "###" — このレベルの見出しごとに検査
    block_required: list[list[str]] = field(default_factory=list)
    check_org_numbers: bool = True    # Gate B
    max_unverified_ratio: float = 0.35  # {{要確認}} だらけの薄い出力を弾く


@dataclass
class GateResult:
    ok: bool
    errors: list[str]
    warnings: list[str]

    def as_feedback(self) -> str:
        lines = [f"- {e}" for e in self.errors]
        return "\n".join(lines)


def _known_orgs(job: dict) -> set[str]:
    names = knowledge.case_names()
    for key in ("company", "company_norm"):
        v = (job.get(key) or "").strip()
        if v:
            names.add(v)
            names.add(re.sub(r"株式会社|有限会社|\s", "", v))
    return {n for n in names if n}


def _gate_a(text: str, spec: GateSpec) -> list[str]:
    errs: list[str] = []
    for sec in spec.required_sections:
        if sec not in text:
            errs.append(f"[Gate A] 必須セクションが無い: 「{sec}」を見出しとして必ず含めること")
    if len(text) < spec.min_chars:
        errs.append(f"[Gate A] 内容が薄い: {len(text)} 文字 < 必要 {spec.min_chars} 文字")
    n_lines = len([l for l in text.splitlines() if l.strip()])
    if n_lines < spec.min_lines:
        errs.append(f"[Gate A] 行数不足: {n_lines} 行 < 必要 {spec.min_lines} 行")
    if spec.require_table and "|" not in text:
        errs.append("[Gate A] 表が無い: Markdown の表形式で整理すること")
    return errs


def _gate_b(text: str, spec: GateSpec, known: set[str],
            jd: str = "") -> tuple[list[str], list[str]]:
    """案例庫にない社名で数字を断定していないか。

    JD 本文に出てくる固有名詞（＝対象企業自身の呼称・製品名・業界語）は
    捏造引用ではないので警告どまり。JD にも案例庫にも無い名前だけが
    「他社の数字を作った」疑いとしてブロック対象になる。
    """
    if not spec.check_org_numbers:
        return [], []
    errs: list[str] = []
    warns: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if not _NUM_RE.search(line):
            continue
        if any(m in line for m in _UNVERIFIED_MARKS):
            continue
        orgs = {o for o in _ORG_RE.findall(line) if o not in _ORG_STOPWORDS}
        unknown = {o for o in orgs if not any(k in o or o in k for k in known)}
        from_jd = {o for o in unknown if jd and o in jd}
        unknown -= from_jd
        if unknown:
            errs.append(
                f"[Gate B] {i} 行目: 案例庫に無い固有名詞 {sorted(unknown)[:3]} と数字を"
                "同じ行で断定している。案例庫の事例だけ数字つきで引用し、"
                "それ以外は {{要出典確認}} を付けるか固有名詞を外すこと"
            )
        elif from_jd:
            warns.append(
                f"[Gate B 参考] {i} 行目: JD 由来の固有名詞 {sorted(from_jd)[:3]} と数字が"
                "同じ行にある。JD に書かれていない数字を足していないか目視確認"
            )
    # {{要確認}} 過多 = 中身が無い
    marks = sum(text.count(m) for m in _UNVERIFIED_MARKS)
    n_lines = max(len([l for l in text.splitlines() if l.strip()]), 1)
    if marks / n_lines > spec.max_unverified_ratio:
        errs.append(
            f"[Gate B] {{{{要確認}}}} が多すぎる（{marks} 箇所 / {n_lines} 行）。"
            "JD とプロフィールから判断できることは断定して書くこと"
        )
    return errs, warns


def _gate_c(text: str, spec: GateSpec) -> list[str]:
    if not spec.block_marker or not spec.block_required:
        return []
    errs: list[str] = []
    blocks = re.split(rf"^{re.escape(spec.block_marker)}\s+", text, flags=re.M)[1:]
    if not blocks:
        errs.append(f"[Gate C] 「{spec.block_marker}」レベルの見出しが 1 つも無い")
        return errs
    for blk in blocks:
        title = blk.splitlines()[0].strip()[:40] if blk.strip() else "(無題)"
        for group in spec.block_required:
            if not any(kw in blk for kw in group):
                errs.append(
                    f"[Gate C] 「{title}」に {group} のいずれも書かれていない。"
                    "各項目に必ず含めること"
                )
    return errs


def _gate_d(text: str) -> list[str]:
    errs: list[str] = []
    _, pii_hits = scrub_for_external(text)
    if pii_hits:
        errs.append(f"[Gate D] PII 残存: {sorted(set(pii_hits))[:3]}")
    brand_hits = redact_scan(text)
    if brand_hits:
        errs.append(f"[Gate D] 取引先ブランド名残存: {sorted(set(brand_hits))[:3]}")
    return errs


def check(text: str, spec: GateSpec, job: dict) -> GateResult:
    known = _known_orgs(job)
    try:
        jd = context.jd_text(job)
    except Exception:
        jd = ""
    errors = _gate_a(text, spec)
    b_err, b_warn = _gate_b(text, spec, known, jd)
    errors += b_err
    errors += _gate_c(text, spec)
    errors += _gate_d(text)
    # Gate B のエラーは最大 5 件までフィードバック（prompt 肥大化防止）
    trimmed: list[str] = []
    b_count = 0
    for e in errors:
        if e.startswith("[Gate B] "):
            b_count += 1
            if b_count > 5:
                continue
        trimmed.append(e)
    return GateResult(ok=not trimmed, errors=trimmed, warnings=b_warn)
