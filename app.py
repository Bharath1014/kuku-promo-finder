"""Web app for the promo finder.

  streamlit run app.py

Upload a show's episode screenplays (.txt files or one .zip), pick a model,
and get a ranked list of promo clips with exact timestamps.
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm import LLMError
from promo_finder.parser import load_show
from promo_finder.pipeline import run
from promo_finder.report import write_reports

from run import load_dotenv

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
load_dotenv(Path(__file__).parent / ".env")  # local runs: reuse the key in .env

KEY_ENV = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
DEFAULT_CHAIN = "gemini:gemini-3.6-flash,gemini:gemini-flash-latest,gemini:gemini-3.5-flash-lite"


def secret(name: str) -> str:
    try:
        return st.secrets.get(name, "") or os.environ.get(name, "")
    except Exception:
        return os.environ.get(name, "")


def save_uploads(files, dest: Path) -> Path:
    """Write uploaded .txt files (or the .txt files inside a .zip) into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        data = f.getvalue()
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = Path(info.filename).name
                    if info.is_dir() or not name.lower().endswith(".txt"):
                        continue
                    (dest / name).write_bytes(z.read(info))
        elif f.name.lower().endswith(".txt"):
            (dest / Path(f.name).name).write_bytes(data)
    return dest


def where(p: dict) -> str:
    return " + ".join(f"Ep {s['episode']} {s['start_ts']}–{s['end_ts']}" for s in p["segments"])


# ---------------- sidebar: settings ----------------
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Engine", ["AI model", "Rules only (no API key)"], index=0)
    if mode == "AI model":
        model = st.text_input("Model (provider:model, comma = fallback chain)", DEFAULT_CHAIN)
        provider = model.split(",")[0].split(":")[0].strip()
        env = KEY_ENV.get(provider)
        has_server_key = bool(env and secret(env))
        key = st.text_input(
            f"{env or 'API key'}" + (" (optional: server key is set)" if has_server_key else ""),
            type="password",
            help="Used only for this run and never stored. Free Gemini key: https://aistudio.google.com/apikey")
    else:
        model, env, key, has_server_key = "heuristic", None, "", True
    per_ep = st.slider("Max promos per episode", 1, 5, 3)
    top = st.slider("How many to rank for posting", 5, 25, 15)
    chunk = 5
    if mode == "AI model":
        chunk = st.slider("Episodes per model call", 1, 10, 5,
                          help="Fewer calls save free-tier quota (about 20 requests per model per day).")
    ep_range = st.text_input("Episode range (optional, e.g. 1-10)", "")

# ---------------- main ----------------
st.title("🎬 Promo Finder")
st.write(
    "Finds 20–60 s promo clips in a microdrama's screenplays. Each clip opens on a **hook** in the "
    "first 3 seconds, ends on a **cliffhanger**, and makes sense to someone who has never seen the show. "
    "The AI picks moments by screenplay block; code turns them into exact timestamps and enforces the "
    "editing rules, so the model never invents a timestamp.")

with st.expander("Input format"):
    st.markdown(
        "One `.txt` file per episode, named like `episode-001-....txt`, with blocks such as:\n"
        "```\n**ANNA (Furious):**\nStart: (00:46.80) | End: (00:50.00)\n\"Line of dialogue\"\n```\n"
        "Upload the files directly, or one `.zip` of the show folder. Files are processed for this run only "
        "and are not stored.")

files = st.file_uploader("Episode screenplays (.txt or .zip)", type=["txt", "zip"], accept_multiple_files=True)
demo_dir = os.environ.get("PROMO_DEMO_DIR", "")  # local testing only
use_demo = bool(demo_dir) and st.checkbox(f"Use local folder: {Path(demo_dir).name}", value=not files)

go = st.button("Find promos", type="primary", disabled=not (files or use_demo))

if go:
    if mode == "AI model" and not (key or has_server_key):
        st.error(f"Enter a {env or 'API'} key in the sidebar, or switch to Rules only.")
        st.stop()
    if key and env:
        os.environ[env] = key
    work = Path(tempfile.mkdtemp(prefix="promo_"))
    src = Path(demo_dir) if use_demo else save_uploads(files, work / "input")
    eps = load_show(src)
    if ep_range.strip():
        lo, _, hi = ep_range.strip().partition("-")
        try:
            eps = [e for e in eps if int(lo) <= e.number <= int(hi or lo)]
        except ValueError:
            st.error("Episode range should look like 1-10.")
            st.stop()
    if not eps or not any(e.blocks for e in eps):
        st.error("No episodes with timestamped blocks found. Check the input format.")
        st.stop()
    show = src.name if use_demo else (Path(files[0].name).stem if len(files) == 1 and files[0].name.endswith(".zip")
                                      else "Uploaded show")
    with st.status(f"Reading {len(eps)} episodes, {sum(len(e.blocks) for e in eps)} blocks…",
                   expanded=True) as status:
        try:
            res = run(eps, show, model, work / "out", per_ep=per_ep, chunk=chunk, top=top,
                      log=lambda m: st.write(m.strip()))
            write_reports(res, work / "out")
        except LLMError as e:
            status.update(label="Model error", state="error")
            st.error(str(e))
            st.stop()
        status.update(label=f"Done: {len(res['promos'])} promos", state="complete", expanded=False)
    st.session_state["res"] = res
    st.session_state["out"] = str(work / "out")

res = st.session_state.get("res")
if res:
    out = Path(st.session_state["out"])
    promos = {p["id"]: p for p in res["promos"]}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Promos found", len(res["promos"]))
    c2.metric("Episodes covered", len({s["episode"] for p in res["promos"] for s in p["segments"]}))
    c3.metric("Rejected by checks", len(res["rejected"]))
    c4.metric("Auto-fixed", sum(bool(p["flags"]) for p in res["promos"]))
    if res.get("models_used"):
        st.caption("Models used: " + ", ".join(f"{k} → {v}" for k, v in res["models_used"].items()))

    tab1, tab2, tab3 = st.tabs(["Post these first", "All promos", "Quality report"])
    with tab1:
        if res.get("ranking_notes"):
            st.info(res["ranking_notes"])
        for i, r in enumerate(res["ranking"], 1):
            p = promos[r["id"]]
            with st.container(border=True):
                st.markdown(f"**{i}. {p['title']}** · {where(p)} · {p['duration']} s")
                a, b = st.columns(2)
                a.markdown(f"**Hook:** {p['hook']}")
                b.markdown(f"**Cliffhanger:** {p['cliffhanger']}")
                st.markdown(f"**Why it works:** {p['why']}")
                if r.get("reason") and r["reason"] != "score order":
                    st.caption(f"Why this slot: {r['reason']}")
                sc = p["scores"]
                st.caption(f"Scores hook/cliffhanger/standalone: {sc.get('hook')}/{sc.get('cliffhanger')}/"
                           f"{sc.get('standalone')}" + (f" · checks: {', '.join(p['flags'])}" if p["flags"] else ""))
                with st.expander("Script of this clip"):
                    st.text(p["transcript"])
    with tab2:
        df = pd.DataFrame([{
            "id": p["id"], "where": where(p), "sec": p["duration"], "hook": p["hook"],
            "cliffhanger": p["cliffhanger"], "score": p["score"], "flags": ", ".join(p["flags"]),
        } for p in res["promos"]])
        st.dataframe(df, use_container_width=True, hide_index=True)
    with tab3:
        q = (out / "quality_report.md").read_text(encoding="utf-8")
        st.markdown(re.sub(r"^(#+) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", q, flags=re.M))

    st.subheader("Download")
    d1, d2, d3 = st.columns(3)
    d1.download_button("promos.csv (for editors)", (out / "promos.csv").read_bytes(), "promos.csv", "text/csv")
    d2.download_button("promos.md (ranked report)", (out / "promos.md").read_bytes(), "promos.md", "text/markdown")
    d3.download_button("promos.json (everything)", json.dumps(res, indent=2, ensure_ascii=False).encode("utf-8"),
                       "promos.json", "application/json")
