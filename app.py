"""Web app for the promo finder.

  streamlit run app.py

Upload a show's episode screenplays (.txt files or one .zip), paste an API key
from any AI provider (or use rules only), and get ranked promo clips with exact
timestamps.

API key:
  - One "API key" field for any provider. The provider is detected from the key
    (OpenAI, Anthropic, Google Gemini, Groq, OpenRouter); any other
    OpenAI-compatible API works with its base URL.
  - A visitor's key is kept in their browser session only (memory; never written
    to disk or shared with other visitors).
  - The app owner can store a key in Streamlit secrets (Manage app > Settings >
    Secrets):   API_KEY = "..."   and optionally   MODEL = "..."   and
    API_BASE_URL = "..."   Visitors without a key of their own then use it.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm import KEY_ENV, Ctx, LLMError, detect_provider
from promo_finder.parser import load_show
from promo_finder.pipeline import run
from promo_finder.report import write_reports
from run import load_dotenv

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
load_dotenv(Path(__file__).parent / ".env")  # local runs: reuse keys in .env

# provider -> (display name, default model(s))
PROVIDERS = {
    "openai": ("OpenAI", "gpt-5-mini"),
    "anthropic": ("Anthropic (Claude)", "claude-opus-5-5"),
    "gemini": ("Google Gemini", "gemini-3.6-flash, gemini-flash-latest, gemini-3.5-flash-lite"),
    "groq": ("Groq", "llama-3.3-70b-versatile"),
    "openrouter": ("OpenRouter", "openrouter/auto"),
    "compat": ("Other OpenAI-compatible API", ""),
}
AUTO = "Auto-detect from key"


def provider_of(key: str, hint: str = "") -> str | None:
    """Provider for a key: the owner's hint, else detected (cached per key for this session only)."""
    if not key:
        return None
    if hint in PROVIDERS:
        return hint
    seen = st.session_state.setdefault("detected", {})
    h = hashlib.sha256(key.encode()).hexdigest()
    if h not in seen:
        with st.spinner("Checking which provider this key belongs to…"):
            seen[h] = detect_provider(key)
    return seen[h]


def server_secret(name: str) -> str:
    try:
        v = st.secrets.get(name, "")
    except Exception:
        v = ""
    return (str(v) if v else os.environ.get(name, "")).strip()


def owner_key() -> tuple[str, str]:
    """The owner's stored key and its provider hint: API_KEY (+ optional PROVIDER),
    or any provider-specific key that is set."""
    if server_secret("API_KEY"):
        return server_secret("API_KEY"), server_secret("PROVIDER").lower()
    for env, prov in (("OPENAI_API_KEY", "openai"), ("ANTHROPIC_API_KEY", "anthropic"),
                      ("GEMINI_API_KEY", "gemini"), ("GROQ_API_KEY", "groq"), ("OPENROUTER_API_KEY", "openrouter")):
        if server_secret(env):
            return server_secret(env), prov
    return "", ""


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


# ---------------- sidebar ----------------
mem = st.session_state.setdefault("mem", {"api_key": "", "base_url": ""})
model, provider, keys, using_owner = "heuristic", None, {}, False
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Engine", ["AI model", "Rules only (no API key)"], index=0)
    if mode == "AI model":
        o_key, o_hint = owner_key()
        if "api_key_box" not in st.session_state:  # restore after switching modes
            st.session_state["api_key_box"] = mem["api_key"]
        typed = st.text_input(
            "API key", key="api_key_box", type="password",
            placeholder="Using the app owner's key" if o_key else "Paste an API key from any provider",
            help="Any provider: OpenAI, Anthropic, Google Gemini, Groq, OpenRouter, or any OpenAI-compatible "
                 "API. Kept only in this browser session (memory), never written to disk or shared.")
        mem["api_key"] = typed.strip()
        api_key = mem["api_key"] or o_key
        using_owner = bool(api_key) and not mem["api_key"]

        with st.expander("Provider, model and base URL"):
            names = [AUTO] + [PROVIDERS[p][0] for p in PROVIDERS]
            choice = st.selectbox("Provider", names, index=0,
                                  help="Detected automatically from the key. Pick one only if detection is wrong.")
            if choice == AUTO:
                provider = provider_of(api_key, o_hint if using_owner else "")
            else:
                provider = next(p for p, (n, _) in PROVIDERS.items() if n == choice)
            if provider:
                default_model = (server_secret("MODEL") if using_owner and server_secret("MODEL")
                                 else PROVIDERS[provider][1])
                models = st.text_input("Model(s)", default_model, key=f"model_{provider}_{using_owner}",
                                       help="Any model this provider offers. Several, comma-separated, form a "
                                            "fallback chain used when one runs out of quota.")
                model = ",".join(f"{provider}:{m.strip()}" for m in models.split(",") if m.strip())
            if provider == "compat":
                base = st.text_input("API base URL", mem["base_url"] or server_secret("API_BASE_URL"),
                                     placeholder="https://api.example.com/v1",
                                     help="Base URL of any OpenAI-compatible API (Together, Mistral, DeepSeek, "
                                          "Azure, a local server, ...).")
                mem["base_url"] = base.strip()

        if not api_key:
            st.caption("No key yet. Paste one above, or switch to Rules only.")
        else:
            who = "The app owner's key" if using_owner else "Your key (saved for this session)"
            st.caption(f"✅ {who} · {PROVIDERS[provider][0] if provider else 'unknown provider'}"
                       + (f" · `{model.split(',')[0].split(':', 1)[1]}`" if model else ""))
        if mem["api_key"] and st.button("Forget my key"):
            mem.update(api_key="", base_url="")
            st.session_state.pop("api_key_box", None)
            st.session_state.pop("detected", None)
            st.rerun()

        if provider and api_key:
            keys[KEY_ENV[provider]] = api_key
            if provider == "compat":
                keys["LLM_BASE_URL"] = mem["base_url"]
    per_ep = st.slider("Max promos per episode", 1, 5, 3)
    top = st.slider("How many to rank for posting", 5, 25, 15)
    chunk = 5
    if mode == "AI model":
        chunk = st.slider("Episodes per model call", 1, 10, 5, help="Fewer calls save quota on free tiers.")
    ep_range = st.text_input("Episode range (optional, e.g. 1-10)", "")

# ---------------- main ----------------
st.title("🎬 Promo Finder")
st.write(
    "Finds 20–60 s promo clips in a microdrama's screenplays. Each clip opens on a **hook** in the "
    "first 3 seconds, ends on a **cliffhanger**, and makes sense to someone who has never seen the show. "
    "The AI picks moments by screenplay block; code turns them into exact timestamps and enforces the "
    "editing rules, so the model never invents a timestamp. Paste an API key from any AI provider in the "
    "sidebar, or use rules only.")

with st.expander("Input format"):
    st.markdown(
        "One `.txt` file per episode, named like `episode-001-....txt`, with blocks such as:\n"
        "```\n**ANNA (Furious):**\nStart: (00:46.80) | End: (00:50.00)\n\"Line of dialogue\"\n```\n"
        "Upload the files directly, or one `.zip` of the show folder. Files are used for this run only and "
        "deleted afterwards; in AI mode the script text is sent to the provider of the API key.")

files = st.file_uploader("Episode screenplays (.txt or .zip)", type=["txt", "zip"], accept_multiple_files=True)
demo_dir = os.environ.get("PROMO_DEMO_DIR", "")  # local testing only
use_demo = bool(demo_dir) and st.checkbox(f"Use local folder: {Path(demo_dir).name}", value=not files)

go = st.button("Find promos", type="primary", disabled=not (files or use_demo))

if go:
    if mode == "AI model":
        if not keys:
            st.error("Paste an API key in the sidebar, or switch to Rules only.")
            st.stop()
        if provider == "compat" and not keys.get("LLM_BASE_URL"):
            st.error("This key's provider wasn't recognised. Open 'Provider, model and base URL' in the "
                     "sidebar and pick the provider, or enter the API's base URL and model name.")
            st.stop()
        if not model:
            st.error("Enter a model name under 'Provider, model and base URL' in the sidebar.")
            st.stop()
    work = Path(tempfile.mkdtemp(prefix="promo_"))
    try:
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
        # per-visitor keys; no disk cache on the server (set PROMO_CACHE locally to reuse answers)
        ctx = Ctx(keys=keys, cache_dir=Path(os.environ["PROMO_CACHE"]) if os.environ.get("PROMO_CACHE") else None)
        out = work / "out"
        with st.status(f"Reading {len(eps)} episodes, {sum(len(e.blocks) for e in eps)} blocks…",
                       expanded=True) as status:
            try:
                res = run(eps, show, model, out, per_ep=per_ep, chunk=chunk, top=top,
                          log=lambda m: st.write(m.strip()), ctx=ctx)
                write_reports(res, out)
            except LLMError as e:
                status.update(label="Model error", state="error")
                msg = str(e)
                if " 401" in msg or " 403" in msg:
                    msg += "\n\nThe API key was rejected. Check the key, or pick the provider by hand in the sidebar."
                elif " 404" in msg:
                    msg += "\n\nThe model name may be wrong or retired: change it in the sidebar."
                st.error(msg)
                st.stop()
            status.update(label=f"Done: {len(res['promos'])} promos", state="complete", expanded=False)
        st.session_state["result"] = {
            "res": res,
            "csv": (out / "promos.csv").read_bytes(),
            "md": (out / "promos.md").read_bytes(),
            "quality": (out / "quality_report.md").read_text(encoding="utf-8"),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)  # nothing uploaded stays on the server

result = st.session_state.get("result")
if result:
    res = result["res"]
    promos = {p["id"]: p for p in res["promos"]}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Promos found", len(res["promos"]))
    c2.metric("Episodes covered", len({s["episode"] for p in res["promos"] for s in p["segments"]}))
    c3.metric("Rejected by checks", len(res["rejected"]))
    c4.metric("Auto-fixed", sum(bool(p["flags"]) for p in res["promos"]))
    if res.get("models_used"):
        used = sorted(set(res["models_used"].values()))
        st.caption("Model used: " + ", ".join(f"`{u}`" for u in used))

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
        st.dataframe(df, width="stretch", hide_index=True)
    with tab3:
        st.markdown(re.sub(r"^(#+) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", result["quality"], flags=re.M))

    st.subheader("Download")
    d1, d2, d3 = st.columns(3)
    d1.download_button("promos.csv (for editors)", result["csv"], "promos.csv", "text/csv")
    d2.download_button("promos.md (ranked report)", result["md"], "promos.md", "text/markdown")
    d3.download_button("promos.json (everything)", json.dumps(res, indent=2, ensure_ascii=False).encode("utf-8"),
                       "promos.json", "application/json")
