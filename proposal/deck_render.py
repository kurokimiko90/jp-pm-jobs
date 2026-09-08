"""提案 deck の描画 — スライド JSON → 自己完結 HTML（零 LLM）。

設計の担当はここ。LLM が決めるのは「どの内容をどの版式に載せるか」だけで、
色・余白・階層はこの一箇所が持つ。fields JSON を手で直して再描画すれば
LLM を呼ばずに直せる。

意匠: 墨水藍（deep ink blue）× 白。面接官の前で投影する前提なので、
装飾より可読性 — 太い罫線と塗りつぶしを避け、階層は文字サイズと余白で作る。
"""

from __future__ import annotations

import html
import re
from datetime import date

LAYOUTS = ("cover", "statement", "bullets", "cards", "table", "timeline",
           "qa", "closing", "arch", "tree", "flow", "swimlane")

_CSS = """\
:root{
  --ink:#12325B; --ink-deep:#0B2140; --ink-mid:#2C5A96; --ink-soft:#5C7BA3;
  --line:#DCE4EF; --line-soft:#EDF1F7; --panel:#F5F8FC; --paper:#FFFFFF;
  --hypo:#7A8CA6;
  --serif:"Hiragino Mincho ProN","Yu Mincho",serif;
  --sans:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic","Noto Sans JP",
         system-ui,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{background:#E9EDF3;color:var(--ink-deep);font-family:var(--sans);
  -webkit-font-smoothing:antialiased;font-feature-settings:"palt" 1}

/* ---- スライド枠: 16:9 を viewport に合わせて縮める ---- */
#stage{position:fixed;inset:0;display:grid;place-items:center}
.slide{
  position:relative;width:1280px;height:720px;background:var(--paper);
  padding:64px 88px 72px;display:none;flex-direction:column;
  box-shadow:0 24px 60px rgba(12,33,64,.18);
  transform:scale(var(--fit,1));transform-origin:center center;
}
.slide.active{display:flex}

/* ---- 共通の頭 ---- */
.chip{font-size:15px;letter-spacing:.14em;color:var(--ink-soft);
  font-weight:600;margin-bottom:14px;display:flex;align-items:center;gap:12px}
.chip::before{content:"";width:26px;height:2px;background:var(--ink-mid)}
h1{font-size:36px;line-height:1.32;letter-spacing:.01em;font-weight:700;
  color:var(--ink);max-width:1100px}
/* 構造線: このページの論理骨格を 1 行で。「A → B → C」の形で読み手に
   先に地図を渡す — これが無いと、密度の高いページは断片の羅列に見える */
.structure{margin-top:11px;font-size:17px;line-height:1.5;color:var(--ink-mid);
  font-weight:600;letter-spacing:.02em;display:flex;flex-wrap:wrap;
  align-items:center;gap:10px}
.structure .arw{color:var(--ink-soft);font-weight:400}
.structure .seg{background:var(--panel);padding:5px 13px;
  border-left:2px solid var(--ink-mid)}
/* 導入文: 構造線が「何が並ぶか」なら、こちらは「なぜその並びか」 */
.leadin{margin-top:9px;font-size:16.5px;line-height:1.62;color:var(--ink-deep);
  max-width:1100px}
.rule{height:1px;background:var(--line);margin:15px 0 16px}
/* 本文は残り高さの中で上下中央 — 4 項目のスライドで下に空白が溜まるのを防ぐ。
   `safe` が要る: 素の center は内容が高さを超えたとき上下**両方**へ溢れ、
   overflow:hidden と組み合わさって先頭（工程帯の番号と見出し）が黙って
   切り落とされる。safe は溢れたときだけ start へ退避する */
.body{flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column;
  justify-content:safe center;padding-bottom:16px;gap:12px}
/* 脚注帯: 本編に載らない補足（対象外・別途扱い・次版の予定など） */
.footnote{flex:none;margin-bottom:14px;background:var(--panel);
  border-left:3px solid var(--ink-mid);
  padding:11px 18px;font-size:14.5px;line-height:1.6;color:var(--ink-deep);
  display:flex;gap:16px;align-items:baseline}
.footnote .fnl{font-weight:700;color:var(--ink);white-space:nowrap;font-size:14px}

/* ---- cover ---- */
.cover{justify-content:center;padding-left:104px}
.cover .kicker{font-size:17px;letter-spacing:.18em;color:var(--ink-soft);
  font-weight:600;margin-bottom:26px}
.cover h1{font-size:52px;line-height:1.28;max-width:940px}
.cover .sub{margin-top:26px;font-size:21px;line-height:1.7;color:var(--ink-mid);
  max-width:860px;font-weight:500}
.cover .meta{margin-top:44px;padding-top:22px;border-top:1px solid var(--line);
  font-size:15px;color:var(--ink-soft);letter-spacing:.04em}
.cover::after{content:"";position:absolute;left:0;top:0;bottom:0;width:10px;
  background:linear-gradient(180deg,var(--ink) 0%,var(--ink-mid) 100%)}

/* ---- statement: 一文を大きく + 根拠 ---- */
.lead{font-family:var(--serif);font-size:34px;line-height:1.62;color:var(--ink);
  font-weight:600;letter-spacing:.01em;max-width:1020px}
.support{margin-top:34px;display:grid;gap:16px}
.support li{list-style:none;font-size:19px;line-height:1.68;color:var(--ink-deep);
  padding-left:26px;position:relative}
.support li::before{content:"";position:absolute;left:0;top:.62em;width:12px;
  height:1px;background:var(--ink-mid)}

/* ---- bullets ---- */
ul.points{display:grid;gap:20px}
/* バッジ列は固定幅 — 「確認済み」と「仮説」で幅が違うと本文の左端が揃わない */
ul.points>li{list-style:none;display:grid;grid-template-columns:82px 1fr;
  gap:18px;align-items:baseline}
ul.points>li.nobadge{grid-template-columns:82px 1fr}
.pt{font-size:21px;line-height:1.62;color:var(--ink-deep)}
.pt .sub{display:block;margin-top:7px;font-size:16px;color:var(--ink-soft);
  line-height:1.6}
.badge{font-size:13px;font-weight:700;letter-spacing:.06em;padding:4px 0;
  border-radius:2px;white-space:nowrap;transform:translateY(-2px);
  display:block;text-align:center}
.badge.fact{background:var(--ink);color:#fff}
.badge.hypo{border:1px solid var(--hypo);color:var(--hypo)}
.badge.none{visibility:hidden}

/* ---- cards ---- */
.cards{display:grid;gap:18px}
.cards.c2{grid-template-columns:repeat(2,1fr)}
.cards.c3{grid-template-columns:repeat(3,1fr)}
.card{background:var(--panel);border-top:3px solid var(--ink-mid);
  padding:22px 24px 24px;display:flex;flex-direction:column}
.card h3{font-size:19px;line-height:1.45;color:var(--ink);font-weight:700;
  margin-bottom:12px}
.card p{font-size:16px;line-height:1.66;color:var(--ink-deep)}
.card .meta{margin-top:auto;padding-top:14px;font-size:14px;color:var(--ink-soft);
  line-height:1.55}

/* ---- table ---- */
table{width:100%;border-collapse:collapse}
thead th{font-size:15px;letter-spacing:.06em;color:var(--ink-soft);
  text-align:left;font-weight:700;padding:0 16px 12px;
  border-bottom:2px solid var(--ink-mid)}
tbody td{font-size:17px;line-height:1.62;color:var(--ink-deep);
  padding:16px;border-bottom:1px solid var(--line-soft);vertical-align:top}
tbody tr:nth-child(even){background:var(--panel)}
tbody td:first-child{font-weight:700;color:var(--ink);white-space:nowrap}

/* ---- timeline ---- */
.phases{display:grid;grid-template-columns:repeat(3,1fr);gap:0;
  border-top:2px solid var(--ink-mid)}
/* 各期は同じ高さで、完了の判断を下端に揃える（点線が横一列に並ぶ） */
.phase{padding:24px 26px 8px;border-right:1px solid var(--line);
  display:flex;flex-direction:column}
.phase ul{flex:1}
.phase .done{margin-top:auto}
.phase:last-child{border-right:none}
.phase .per{font-size:15px;letter-spacing:.1em;color:var(--ink-mid);
  font-weight:700;margin-bottom:10px}
.phase h3{font-size:20px;line-height:1.5;color:var(--ink);margin-bottom:14px}
.phase li{list-style:none;font-size:16px;line-height:1.62;color:var(--ink-deep);
  margin-bottom:9px;padding-left:16px;position:relative}
.phase li::before{content:"";position:absolute;left:0;top:.68em;width:7px;
  height:1px;background:var(--ink-soft)}
.phase .done{padding-top:12px;border-top:1px dashed var(--line);
  font-size:14px;color:var(--ink-soft);line-height:1.55}

/* ---- arch: プロダクト構造の層図 ---- */
/* 層は 3 段でも収まる密度に詰める。ゆったり組むと 3 層目が本文領域から
   はみ出し、overflow:hidden に飲まれて「データ層が無い構造図」になる */
.arch{display:grid;gap:6px}
.layer{display:grid;grid-template-columns:160px 1fr;align-items:stretch;
  border:1px solid var(--line);background:var(--paper)}
.layer .lname{background:var(--panel);border-right:2px solid var(--ink-mid);
  padding:10px 16px;font-size:15.5px;font-weight:700;color:var(--ink);
  display:flex;align-items:center;line-height:1.45}
.layer .litems{display:flex;flex-wrap:wrap;gap:8px;padding:9px 14px;
  align-items:center}
.layer .box{border:1px solid var(--ink-mid);color:var(--ink);
  padding:6px 12px;font-size:14.5px;line-height:1.4;background:var(--paper);
  display:flex;flex-direction:column;gap:2px}
.layer .box .bdesc{font-size:12px;color:var(--ink-soft);line-height:1.4;
  font-weight:400}
.layer .lnote{grid-column:1/-1;padding:5px 14px 7px;font-size:13px;
  color:var(--ink-soft);line-height:1.5;border-top:1px dashed var(--line-soft)}
/* 層間の矢印は行を占有させない（1 段につき 27px 取られていた） */
.arch .flow{font-size:12px;color:var(--ink-soft);text-align:center;
  letter-spacing:.2em;line-height:1;height:12px}

/* ---- flow: 工程の帯（① → ② → ③ → ④） ---- */
.flow-band{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;
  align-items:stretch;gap:0}
.fstep{position:relative;padding:0 20px 0 0;display:flex;flex-direction:column}
.fstep:last-child{padding-right:0}
.fstep .fhead{display:flex;align-items:center;gap:11px;margin-bottom:10px}
.fstep .fnum{width:27px;height:27px;border-radius:50%;background:var(--ink);
  color:#fff;font-size:14px;font-weight:700;display:grid;place-items:center;
  flex-shrink:0}
.fstep .flabel{font-size:17.5px;font-weight:700;color:var(--ink);line-height:1.4}
.fstep .fbar{height:2px;background:var(--ink-mid);margin-bottom:11px}
.fstep .fcap{font-size:15px;line-height:1.62;color:var(--ink-deep)}
.fstep .fsub{margin-top:7px;font-size:13.5px;line-height:1.55;color:var(--ink-soft)}
/* 矢印は段の間に置く（枠線ではなく記号 — 印刷でも消えない） */
.fstep::after{content:"→";position:absolute;right:2px;top:3px;font-size:17px;
  color:var(--ink-soft)}
.fstep:last-child::after{content:none}

/* ---- swimlane: 左に層の名前、右にその層の構成要素 ---- */
.lanes{display:grid;gap:12px}
.lane{display:grid;grid-template-columns:210px 1fr;border:1px solid var(--line);
  background:var(--paper)}
.lane .lhead{background:var(--panel);border-right:2px solid var(--ink-mid);
  padding:15px 17px;display:flex;flex-direction:column;justify-content:center}
.lane .lhead h3{font-size:16.5px;font-weight:700;color:var(--ink);line-height:1.45}
.lane .lhead p{margin-top:7px;font-size:13px;line-height:1.55;color:var(--ink-soft)}
.lane .lcells{display:grid;grid-auto-flow:column;grid-auto-columns:1fr}
.lane .cell{padding:14px 16px;border-right:1px solid var(--line-soft);
  display:flex;flex-direction:column}
.lane .cell:last-child{border-right:none}
.lane .cell h4{font-size:15px;font-weight:700;color:var(--ink);line-height:1.45;
  margin-bottom:7px}
.lane .cell p{font-size:13.5px;line-height:1.6;color:var(--ink-deep)}
.lane .cell .cmeta{margin-top:auto;padding-top:8px;font-size:12.5px;
  color:var(--ink-soft);line-height:1.5}

/* ---- tree: KGI/KPI ツリー ---- */
.tree{display:grid;gap:20px}
.tree .kgi{justify-self:center;background:var(--ink);color:#fff;
  padding:14px 30px;font-size:20px;font-weight:700;line-height:1.5;
  position:relative}
.tree .drivers{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:16px;
  border-top:1px solid var(--line);padding-top:20px}
.tree .driver{border:1px solid var(--ink-mid);padding:16px 18px;
  display:flex;flex-direction:column;background:var(--paper)}
.tree .driver h3{font-size:17px;line-height:1.5;color:var(--ink);
  font-weight:700;margin-bottom:10px}
.tree .driver .lead-kpi{margin-top:auto;padding-top:10px;
  border-top:1px dashed var(--line);font-size:14px;color:var(--ink-mid);
  line-height:1.55}
.tree .driver .dnote{font-size:13.5px;color:var(--ink-soft);line-height:1.55}
.tree .driver .badge{align-self:flex-start;margin-bottom:8px;padding:3px 8px}

/* ---- qa ---- */
.qa{display:grid;gap:24px}
.qa .q{font-size:20px;line-height:1.6;color:var(--ink);font-weight:700;
  padding-left:34px;position:relative}
.qa .q::before{content:"Q";position:absolute;left:0;top:0;font-size:15px;
  color:#fff;background:var(--ink);width:23px;height:23px;border-radius:50%;
  display:grid;place-items:center;font-weight:700}
.qa .a{margin-top:10px;font-size:17px;line-height:1.7;color:var(--ink-deep);
  padding-left:34px;border-left:1px solid var(--line);margin-left:11px}

/* ---- closing ---- */
.closing{justify-content:center}
.closing .lead{font-size:30px}
.closing ul.points{margin-top:32px}

/* ---- 足 ---- */
.foot{position:absolute;left:88px;right:88px;bottom:30px;display:flex;
  justify-content:space-between;align-items:center;font-size:13px;
  color:var(--ink-soft);letter-spacing:.05em;
  border-top:1px solid var(--line-soft);padding-top:12px}
.note{display:none;position:fixed;left:0;right:0;bottom:0;background:var(--ink-deep);
  color:#E8EEF7;font-size:15px;line-height:1.7;padding:18px 32px;z-index:20}
body.notes .slide.active .note{display:block}
#hud{position:fixed;right:18px;bottom:14px;font-size:12px;color:#7C8CA3;z-index:30}

@media print{
  @page{size:1280px 720px;margin:0}
  /* 塗りつぶしを印刷へ持ち込む。これが無いと Chrome の「背景のグラフィック」は
     既定で OFF なので、白抜き文字の要素（KGI の箱・確認済みバッジ・工程の番号）が
     白地に白で消える。投影用に作ったページが、PDF にした瞬間に一番見せたい
     一文だけ読めなくなる（実測: KGI の主張が淡いグレーの文字だけになった） */
  *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  html,body{background:#fff}
  #stage{position:static;display:block}
  .slide{display:flex!important;transform:none;box-shadow:none;
    page-break-after:always;break-after:page}
  #hud,.note{display:none!important}
}
"""

_JS = """\
const slides=[...document.querySelectorAll('.slide')];let i=0;
function fit(){const s=Math.min(innerWidth/1280,innerHeight/720)*0.94;
  document.documentElement.style.setProperty('--fit',s.toFixed(4));}
function show(n){i=Math.max(0,Math.min(slides.length-1,n));
  slides.forEach((s,k)=>s.classList.toggle('active',k===i));
  document.getElementById('pos').textContent=(i+1)+' / '+slides.length;
  location.hash=i+1;}
addEventListener('resize',fit);
addEventListener('keydown',e=>{
  if(e.key==='ArrowRight'||e.key===' '||e.key==='PageDown')show(i+1);
  if(e.key==='ArrowLeft'||e.key==='PageUp')show(i-1);
  if(e.key.toLowerCase()==='n')document.body.classList.toggle('notes');
  if(e.key.toLowerCase()==='p')print();});
addEventListener('click',e=>{if(!e.target.closest('.note'))show(i+1)});
fit();show((parseInt(location.hash.slice(1))||1)-1);
"""


def _e(text) -> str:
    return html.escape(str(text or ""))


_ARROW = re.compile(r"\s*(?:→|->|⇒)\s*")


def _structure(text: str) -> str:
    """構造線 — 「A → B → C」を段に割って描く。

    このページに何がどの順で並ぶのかを先に見せる。矢印を含まない 1 語のときは
    そのまま 1 段（分割を強制すると意味の切れ目でないところで割れる）。
    """
    if not text:
        return ""
    segs = [s for s in _ARROW.split(str(text)) if s.strip()]
    if len(segs) == 1:
        return f'<div class="structure"><span class="seg">{_e(segs[0])}</span></div>'
    parts = []
    for i, s in enumerate(segs):
        if i:
            parts.append('<span class="arw">→</span>')
        parts.append(f'<span class="seg">{_e(s)}</span>')
    return f'<div class="structure">{"".join(parts)}</div>'


def _head(slide: dict, *, big_lead: bool = False) -> str:
    """見出し部 — ラベル / 主張の見出し / 構造線 / 導入文 / 罫線。

    `big_lead` の版式（statement・closing）は lead を本文側で大きく出すので、
    ここでは導入文として二度出さない。
    """
    label = _e(slide.get("label"))
    title = _e(slide.get("title"))
    chip = f'<div class="chip">{label}</div>' if label else ""
    if not title:
        return chip
    struct = _structure(slide.get("structure"))
    lead = slide.get("lead")
    leadin = (f'<div class="leadin">{_e(lead)}</div>'
              if lead and not big_lead else "")
    return f'{chip}<h1>{title}</h1>{struct}{leadin}<div class="rule"></div>'


def _footnote(fn) -> str:
    """脚注帯 — 文字列でも {label, text} でも受ける。"""
    if not fn:
        return ""
    if isinstance(fn, dict):
        label = (f'<span class="fnl">{_e(fn.get("label"))}</span>'
                 if fn.get("label") else "")
        text = _e(fn.get("text"))
    else:
        label, text = "", _e(fn)
    return f'<div class="footnote">{label}<span>{text}</span></div>' if text else ""


def _flow(steps: list[dict]) -> str:
    """工程の帯 — 番号つきの段を横に並べ、間を矢印でつなぐ。"""
    if not steps:
        return ""
    out = []
    for i, s in enumerate(steps[:5], 1):
        sub = f'<div class="fsub">{_e(s["sub"])}</div>' if s.get("sub") else ""
        out.append(
            f'<div class="fstep"><div class="fhead">'
            f'<span class="fnum">{i}</span>'
            f'<span class="flabel">{_e(s.get("label"))}</span></div>'
            f'<div class="fbar"></div>'
            f'<div class="fcap">{_e(s.get("caption"))}</div>{sub}</div>')
    return f'<div class="flow-band">{"".join(out)}</div>'


def _lanes(lanes: list[dict]) -> str:
    """泳道 — 左に観点の名前と説明、右にその観点での構成要素を横並び。

    「打ち手 × 層」「工程 × 担保するもの」のような二次元を 1 枚で見せるための版式。
    箇条書きに潰すと失われる「どの列とどの行の交点か」がここでは残る。
    """
    if not lanes:
        return ""
    out = []
    for lane in lanes[:3]:
        note = f"<p>{_e(lane['note'])}</p>" if lane.get("note") else ""
        cells = []
        for c in (lane.get("cells") or [])[:4]:
            meta = (f'<div class="cmeta">{_e(c["meta"])}</div>'
                    if c.get("meta") else "")
            cells.append(f'<div class="cell"><h4>{_e(c.get("heading"))}</h4>'
                         f'<p>{_e(c.get("body"))}</p>{meta}</div>')
        out.append(f'<div class="lane"><div class="lhead">'
                   f'<h3>{_e(lane.get("name"))}</h3>{note}</div>'
                   f'<div class="lcells">{"".join(cells)}</div></div>')
    return f'<div class="lanes">{"".join(out)}</div>'


def _bullets(items: list[dict]) -> str:
    out = []
    for b in items or []:
        badge = str(b.get("badge") or "none")
        badge = badge if badge in ("fact", "hypo", "none") else "none"
        text = _e(b.get("text"))
        sub = f'<span class="sub">{_e(b["sub"])}</span>' if b.get("sub") else ""
        label = {"fact": "確認済み", "hypo": "仮説"}.get(badge, "")
        out.append(f'<li><span class="badge {badge}">{label}</span>'
                   f'<span class="pt">{text}{sub}</span></li>')
    return f'<ul class="points">{"".join(out)}</ul>' if out else ""


def _cards(items: list[dict]) -> str:
    if not items:
        return ""
    cols = "c3" if len(items) >= 3 and len(items) % 3 == 0 else "c2"
    if len(items) == 3:
        cols = "c3"
    out = []
    for c in items:
        meta = f'<div class="meta">{_e(c["meta"])}</div>' if c.get("meta") else ""
        out.append(f'<div class="card"><h3>{_e(c.get("heading"))}</h3>'
                   f'<p>{_e(c.get("body"))}</p>{meta}</div>')
    return f'<div class="cards {cols}">{"".join(out)}</div>'


def _table(tbl: dict) -> str:
    cols = tbl.get("columns") or []
    rows = tbl.get("rows") or []
    if not cols or not rows:
        return ""
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>"
        for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _phases(items: list[dict]) -> str:
    if not items:
        return ""
    out = []
    for p in items[:3]:
        lis = "".join(f"<li>{_e(x)}</li>" for x in (p.get("items") or []))
        done = (f'<div class="done">完了の判断: {_e(p["done"])}</div>'
                if p.get("done") else "")
        out.append(f'<div class="phase"><div class="per">{_e(p.get("period"))}</div>'
                   f'<h3>{_e(p.get("title"))}</h3><ul>{lis}</ul>{done}</div>')
    return f'<div class="phases">{"".join(out)}</div>'


def _arch(layers: list[dict]) -> str:
    """プロダクト構造 — 層の帯 × 構成要素の箱。上が利用者に近い層。

    構成要素は文字列でも `{name, desc}` でも受ける。名前だけの箱を並べた図は
    「それが何をするのか」が読み取れず、結局その場で口頭補足することになる。
    """
    if not layers:
        return ""
    out = []
    for i, layer in enumerate(layers[:4]):
        boxes = []
        for x in (layer.get("items") or [])[:5]:
            if isinstance(x, dict):
                desc = (f'<span class="bdesc">{_e(x["desc"])}</span>'
                        if x.get("desc") else "")
                boxes.append(f'<span class="box">{_e(x.get("name"))}{desc}</span>')
            else:
                boxes.append(f'<span class="box">{_e(x)}</span>')
        note = (f'<div class="lnote">{_e(layer["note"])}</div>'
                if layer.get("note") else "")
        out.append(f'<div class="layer"><div class="lname">{_e(layer.get("name"))}'
                   f'</div><div class="litems">{"".join(boxes)}</div>{note}</div>')
        if i < len(layers[:4]) - 1:
            out.append('<div class="flow">▼</div>')
    return f'<div class="arch">{"".join(out)}</div>'


def _tree(slide: dict) -> str:
    """KGI/KPI ツリー — KGI 1 本 → ドライバー 3〜4 本（各先行指標つき）。"""
    kgi = slide.get("kgi")
    drivers = slide.get("drivers") or []
    if not kgi or not drivers:
        return ""
    out = []
    for d in drivers[:4]:
        badge = ('<span class="badge hypo">仮説</span>'
                 if d.get("hypo", True) else "")
        note = (f'<div class="dnote">{_e(d["note"])}</div>'
                if d.get("note") else "")
        lead_kpi = (f'<div class="lead-kpi">先行指標: {_e(d["leading"])}</div>'
                    if d.get("leading") else "")
        out.append(f'<div class="driver">{badge}<h3>{_e(d.get("name"))}</h3>'
                   f'{note}{lead_kpi}</div>')
    return (f'<div class="tree"><div class="kgi">{_e(kgi)}</div>'
            f'<div class="drivers">{"".join(out)}</div></div>')


def _qa(items: list[dict]) -> str:
    out = "".join(f'<div><div class="q">{_e(x.get("q"))}</div>'
                  f'<div class="a">{_e(x.get("a"))}</div></div>'
                  for x in items or [])
    return f'<div class="qa">{out}</div>' if out else ""


def _slide(slide: dict, n: int, total: int, footer: str) -> str:
    layout = slide.get("layout") if slide.get("layout") in LAYOUTS else "bullets"
    note = (f'<div class="note">{_e(slide["note"])}</div>'
            if slide.get("note") else "")
    foot = (f'<div class="foot"><span>{_e(footer)}</span>'
            f'<span>{n} / {total}</span></div>')

    if layout == "cover":
        sub = f'<div class="sub">{_e(slide.get("lead"))}</div>' if slide.get("lead") else ""
        meta = f'<div class="meta">{_e(slide.get("meta"))}</div>' if slide.get("meta") else ""
        kicker = (f'<div class="kicker">{_e(slide.get("label"))}</div>'
                  if slide.get("label") else "")
        return (f'<section class="slide cover">{kicker}'
                f'<h1>{_e(slide.get("title"))}</h1>{sub}{meta}{note}</section>')

    big_lead = layout in ("statement", "closing")
    inner = ""
    if big_lead and slide.get("lead"):
        inner += f'<div class="lead">{_e(slide["lead"])}</div>'
    if big_lead and slide.get("bullets"):
        items = "".join(f'<li>{_e(b.get("text") if isinstance(b, dict) else b)}</li>'
                        for b in slide["bullets"])
        inner += f'<ul class="support">{items}</ul>'
    elif slide.get("bullets"):
        inner += _bullets(slide["bullets"])
    # ブロックは宣言された分だけ順に積む。1 枚に工程帯 ＋ 泳道、のような
    # 組み合わせを許す（参考にした構成資料はこの積み方で密度を出している）
    inner += _flow(slide.get("steps"))
    inner += _arch(slide.get("layers"))
    inner += _lanes(slide.get("lanes"))
    inner += _cards(slide.get("cards"))
    inner += _table(slide.get("table") or {})
    inner += _phases(slide.get("phases"))
    inner += _qa(slide.get("qa"))
    if layout == "tree":
        inner += _tree(slide)

    # 脚注は body の外に置く。中に入れると overflow:hidden に飲まれて、
    # 「対象外」の断りだけが黙って消える（本編より消えてはいけない情報）
    fn = _footnote(slide.get("footnote"))
    cls = "slide closing" if layout == "closing" else "slide"
    return (f'<section class="{cls}">{_head(slide, big_lead=big_lead)}'
            f'<div class="body">{inner}</div>{fn}{foot}{note}</section>')


def render(fields: dict) -> str:
    deck = fields.get("deck") or {}
    slides = fields.get("slides") or []
    footer = deck.get("footer") or ""
    body = "".join(_slide(s, i + 1, len(slides), footer)
                   for i, s in enumerate(slides))
    title = _e(deck.get("title") or "提案")
    return (
        "<!DOCTYPE html>\n<html lang=\"ja\"><head><meta charset=\"UTF-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        f'<div id="stage">{body}</div>'
        f'<div id="hud"><span id="pos"></span>　←→ 移動　N 原稿　P 印刷　'
        f'{date.today().isoformat()}</div>'
        f"<script>{_JS}</script></body></html>\n")


def plain_text(fields: dict) -> str:
    """閘門検査用 — HTML タグを含まない本文だけ。"""
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    walk(fields)
    return re.sub(r"\s+", " ", " ".join(parts))
