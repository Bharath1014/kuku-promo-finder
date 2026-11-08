"""Look and feel for the web app, inspired by the Kuku FM web player:
pure black canvas, #1A1A1A cards, Kuku red (#EB0000) accent, Figtree type,
pill buttons and chips, and the 'Top Shows' row with big gold rank numerals.

Every piece of text from the model or the screenplay goes through esc()
before it is placed in HTML.
"""
from __future__ import annotations

import html

RED = "#EB0000"
RED_2 = "#EF4030"
BG = "#000000"
CARD = "#1A1A1A"
LINE = "#333333"
MUTED = "#9CA3AF"
GOLD_1, GOLD_2 = "#FFE08A", "#C8871A"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700;800;900&display=swap');

html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select {{
  font-family: 'Figtree', system-ui, sans-serif !important;
}}
.stApp {{ background: {BG}; }}
[data-testid="stHeader"] {{ background: rgba(0,0,0,.85); backdrop-filter: blur(8px); }}
.block-container {{ padding-top: 2.2rem; max-width: 1180px; }}

/* sidebar */
[data-testid="stSidebar"] {{ background: #0B0B0B; border-right: 1px solid #1F1F1F; }}
[data-testid="stSidebar"] h2 {{ font-weight: 800; letter-spacing: -.01em; }}

/* headings & text */
h1, h2, h3, h4 {{ font-weight: 700 !important; letter-spacing: -.01em; color: #fff; }}
p, li, label, .stCaption, [data-testid="stCaptionContainer"] {{ color: #E5E7EB; }}
[data-testid="stCaptionContainer"] p {{ color: {MUTED} !important; }}
a {{ color: {RED_2} !important; }}

/* buttons: red pills like 'Start Free Trial' / 'Play Now' */
.stButton > button, .stDownloadButton > button {{
  border-radius: 9999px !important; font-weight: 700 !important; padding: .55rem 1.4rem !important;
  border: 1px solid rgba(255,255,255,.3) !important; background: rgba(255,255,255,.12) !important; color: #fff !important;
  transition: transform .08s ease, background .15s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{ background: rgba(255,255,255,.18) !important; }}
.stButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {{
  background: linear-gradient(180deg, {RED_2}, {RED}) !important; border: none !important;
  box-shadow: 0 6px 24px rgba(235,0,0,.35);
}}
.stButton > button[kind="primary"]:hover {{ transform: translateY(-1px); filter: brightness(1.08); }}
.stButton > button:disabled {{ opacity: .45; box-shadow: none; }}

/* inputs */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{
  background: {CARD} !important; border-radius: 8px !important; border-color: {LINE} !important;
}}
[data-testid="stFileUploaderDropzone"] {{
  background: {CARD} !important; border: 1.5px dashed #3A3A3A !important; border-radius: 12px !important;
}}
[data-testid="stExpander"] details {{ background: {CARD}; border: 1px solid #262626 !important; border-radius: 8px !important; }}
[data-testid="stSlider"] [role="slider"] {{ background: {RED} !important; box-shadow: 0 0 0 4px rgba(235,0,0,.2); }}

/* tabs: like the Popular / Audiobooks / Originals nav */
[data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid #1F1F1F; }}
[data-baseweb="tab"] {{ background: transparent !important; padding: .6rem 0 !important; }}
[data-baseweb="tab"] p {{ font-weight: 600 !important; font-size: 1.02rem !important; color: #D1D5DB; }}
[data-baseweb="tab"][aria-selected="true"] p, button[role="tab"][aria-selected="true"] *,
button[role="tab"][aria-selected="true"] {{ color: {RED} !important; }}
[data-baseweb="tab-highlight"] {{ background: {RED} !important; height: 3px !important; border-radius: 3px; }}

/* metric cards */
[data-testid="stMetric"] {{ background: {CARD}; border-radius: 8px; padding: 14px 18px; border: 1px solid #222; }}
[data-testid="stMetricLabel"] p {{ color: {MUTED} !important; font-weight: 600; }}
[data-testid="stMetricValue"] {{ font-weight: 800 !important; }}

/* status box, alerts, dataframe */
[data-testid="stStatusWidget"], [data-testid="stExpander"] {{ border-radius: 8px; }}
[data-testid="stAlert"] {{ border-radius: 8px; }}
[data-testid="stDataFrame"] {{ border-radius: 8px; overflow: hidden; border: 1px solid #222; }}

/* ---------- custom pieces ---------- */
.pf-brand {{ display:flex; align-items:center; gap:.55rem; font-weight:800; font-size:1.15rem; color:#fff; margin-bottom:.4rem; }}
.pf-brand .dot {{ width:10px; height:10px; border-radius:50%; background:{RED}; box-shadow:0 0 12px rgba(235,0,0,.8); }}

.pf-hero {{
  position:relative; overflow:hidden; border-radius:16px; padding:38px 36px 30px; margin:.4rem 0 1.4rem;
  background:
    radial-gradient(900px 340px at 85% -10%, rgba(235,0,0,.55), transparent 60%),
    radial-gradient(600px 300px at 0% 120%, rgba(200,135,26,.25), transparent 60%),
    linear-gradient(180deg, #160606 0%, #0A0A0A 100%);
  border:1px solid #2A1414;
}}
.pf-eyebrow {{ display:inline-block; font-size:.74rem; font-weight:700; letter-spacing:.14em; color:#fff;
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18); padding:5px 12px; border-radius:9999px; }}
.pf-h1 {{ font-size:2.9rem; line-height:1.05; margin:.7rem 0 .6rem; font-weight:900; color:#fff; letter-spacing:-.02em; }}
.pf-h1 .accent {{ color:{RED}; }}
@media (max-width: 760px) {{ .pf-h1 {{ font-size:2.1rem; }} .pf-hero {{ padding:26px 20px 22px; }} }}
.pf-hero p {{ color:#D1D5DB; font-size:1.05rem; max-width:720px; margin:0; }}
.pf-steps {{ display:flex; flex-wrap:wrap; gap:.5rem; margin-top:1.2rem; }}
.pf-step {{ display:flex; align-items:center; gap:.5rem; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14);
  border-radius:9999px; padding:6px 14px 6px 6px; font-weight:600; font-size:.92rem; }}
.pf-step b {{ display:inline-grid; place-items:center; width:24px; height:24px; border-radius:50%; background:{RED}; font-size:.8rem; }}

.pf-section {{ display:flex; align-items:baseline; justify-content:space-between; margin:1.6rem 0 .8rem; }}
.pf-section .t {{ margin:0; font-size:1.4rem; font-weight:700; color:#fff; letter-spacing:-.01em; }}
.pf-section span {{ color:{MUTED}; font-size:.9rem; }}

.pf-note {{ background:{CARD}; border-left:3px solid {RED}; border-radius:8px; padding:12px 16px; color:#E5E7EB; margin-bottom:1rem; }}

.pf-card {{ position:relative; display:flex; gap:0; background:{CARD}; border-radius:8px; border:1px solid #232323;
  overflow:hidden; margin-bottom:.35rem; transition:border-color .15s ease, transform .15s ease; }}
.pf-card:hover {{ border-color:#3a3a3a; transform:translateY(-1px); }}
.pf-rankcol {{ flex:0 0 96px; display:flex; align-items:flex-end; justify-content:center;
  background:linear-gradient(180deg, #121212, #0A0A0A); border-right:1px solid #222; padding-bottom:8px; }}
.pf-rank {{ font-size:4.6rem; line-height:1; font-weight:900; letter-spacing:-.04em;
  background:linear-gradient(180deg, {GOLD_1} 0%, {GOLD_2} 100%); -webkit-background-clip:text; background-clip:text;
  color:transparent; -webkit-text-stroke:1.5px rgba(255,255,255,.35); text-shadow:0 6px 18px rgba(200,135,26,.25); }}
.pf-rank.silver {{ background:linear-gradient(180deg,#FFFFFF,#9CA3AF); -webkit-background-clip:text; background-clip:text; }}
.pf-body {{ flex:1; padding:14px 18px 14px; min-width:0; }}
.pf-title {{ font-weight:700; font-size:1.12rem; color:#fff; margin-bottom:.45rem; }}
.pf-chips {{ display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:.7rem; }}
.pf-chip {{ font-size:.8rem; font-weight:600; color:#fff; background:rgba(255,255,255,.12); border-radius:9999px; padding:3px 10px; }}
.pf-chip.red {{ background:rgba(235,0,0,.18); color:#FF6B6B; }}
.pf-chip.gold {{ background:rgba(200,135,26,.18); color:{GOLD_1}; }}
.pf-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:.9rem; }}
@media (max-width: 760px) {{ .pf-grid {{ grid-template-columns:1fr; }} .pf-rankcol {{ flex-basis:64px; }} .pf-rank {{ font-size:3.2rem; }} }}
.pf-label {{ font-size:.7rem; font-weight:800; letter-spacing:.12em; color:{MUTED}; margin-bottom:.2rem; }}
.pf-label.hook {{ color:#FF6B6B; }}
.pf-label.cliff {{ color:{GOLD_1}; }}
.pf-quote {{ color:#fff; font-weight:500; line-height:1.4; }}
.pf-why {{ color:#D1D5DB; margin-top:.75rem; font-size:.95rem; }}
.pf-slot {{ color:{MUTED}; margin-top:.35rem; font-size:.85rem; }}
.pf-bars {{ display:flex; gap:1rem; margin-top:.75rem; flex-wrap:wrap; }}
.pf-bar {{ flex:1; min-width:120px; }}
.pf-bar small {{ color:{MUTED}; font-weight:600; font-size:.74rem; }}
.pf-bar .track {{ height:5px; border-radius:9999px; background:#2A2A2A; margin-top:4px; overflow:hidden; }}
.pf-bar .fill {{ height:100%; border-radius:9999px; background:linear-gradient(90deg,{RED},{RED_2}); }}

.pf-empty {{ text-align:center; color:{MUTED}; padding:28px; background:{CARD}; border-radius:12px; border:1px solid #1F1F1F; }}
.pf-footer {{ color:#6B7280; font-size:.8rem; text-align:center; margin-top:2.4rem; padding-top:1rem; border-top:1px solid #1A1A1A; }}
</style>
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def brand() -> str:
    return '<div class="pf-brand"><span class="dot"></span>Promo Finder</div>'


def hero() -> str:
    return (
        '<div class="pf-hero">'
        '<span class="pf-eyebrow">AI GENERALIST ASSIGNMENT · PROBLEM 1</span>'
        '<div class="pf-h1">Find the <span class="accent">promos</span> in any microdrama</div>'
        '<p>Reads timestamped screenplays and picks 20–60 s clips that open on a hook in the first 3 seconds, '
        'end on a cliffhanger, and make sense to someone who has never seen the show. The AI judges the drama; '
        'code sets every timestamp and enforces the editing rules.</p>'
        '<div class="pf-steps">'
        '<div class="pf-step"><b>1</b>Upload episode scripts</div>'
        '<div class="pf-step"><b>2</b>Paste any API key, or use rules only</div>'
        '<div class="pf-step"><b>3</b>Get ranked, timestamped promos</div>'
        '</div></div>')


def section(title: str, sub: str = "") -> str:
    return f'<div class="pf-section"><div class="t">{esc(title)}</div><span>{esc(sub)}</span></div>'


def _bar(label: str, v) -> str:
    try:
        pct = max(0.0, min(100.0, float(v) * 10))
        shown = f"{float(v):g}"
    except (TypeError, ValueError):
        return ""
    return (f'<div class="pf-bar"><small>{esc(label)} · {shown}/10</small>'
            f'<div class="track"><div class="fill" style="width:{pct:.0f}%"></div></div></div>')


def promo_card(rank: int, p: dict, slot_reason: str = "") -> str:
    segs = " + ".join(f"Ep {s['episode']} · {s['start_ts']}–{s['end_ts']}" for s in p["segments"])
    chips = [f'<span class="pf-chip red">{esc(segs)}</span>',
             f'<span class="pf-chip">{esc(p["duration"])} s</span>']
    if len(p["segments"]) > 1:
        chips.append('<span class="pf-chip gold">multi-cut</span>')
    for f in p.get("flags", []):
        chips.append(f'<span class="pf-chip">{esc(f.replace("_", " "))}</span>')
    sc = p.get("scores", {})
    bars = "".join(_bar(k, sc.get(key)) for k, key in
                   (("Hook", "hook"), ("Cliffhanger", "cliffhanger"), ("Standalone", "standalone")))
    slot = (f'<div class="pf-slot">Why this slot: {esc(slot_reason)}</div>'
            if slot_reason and slot_reason != "score order" else "")
    rank_cls = "pf-rank" if rank <= 3 else "pf-rank silver"
    return (
        f'<div class="pf-card"><div class="pf-rankcol"><div class="{rank_cls}">{rank}</div></div>'
        f'<div class="pf-body"><div class="pf-title">{esc(p.get("title") or "Promo")}</div>'
        f'<div class="pf-chips">{"".join(chips)}</div>'
        f'<div class="pf-grid">'
        f'<div><div class="pf-label hook">HOOK · FIRST 3 S</div><div class="pf-quote">{esc(p.get("hook"))}</div></div>'
        f'<div><div class="pf-label cliff">CLIFFHANGER · LAST BEAT</div><div class="pf-quote">{esc(p.get("cliffhanger"))}</div></div>'
        f'</div>'
        f'<div class="pf-why">{esc(p.get("why"))}</div>{slot}'
        f'<div class="pf-bars">{bars}</div>'
        f'</div></div>')


def note(text: str) -> str:
    return f'<div class="pf-note">{esc(text)}</div>'


def empty(text: str) -> str:
    return f'<div class="pf-empty">{esc(text)}</div>'


def footer() -> str:
    return ('<div class="pf-footer">Prototype for the Kuku AI Generalist assignment · '
            'Visual style inspired by the Kuku FM web player · Not an official Kuku product</div>')
