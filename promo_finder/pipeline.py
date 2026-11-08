"""Promo-finding pipeline.

  parse (code) -> propose (LLM reads episodes, picks moments by block ID)
  -> enforce editing rules + validate (code) -> dedupe + score (code)
  -> editor ranking (LLM, one call) -> reports (code)

The model only makes creative judgements. Everything checkable (timestamps,
durations, cut points, quotes) is done or verified in code, so the model
cannot invent a timestamp.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import heuristics as H
from .llm import complete_json
from .parser import Block, Episode, fmt, render

PROPOSE_SYSTEM = """You are a senior promo editor for vertical microdrama apps (ReelShort, DramaBox).
You pick clips from a show that will run as paid/organic promos on TikTok, Instagram Reels and YouTube Shorts.
Their only job: make a stranger who is scrolling stop, watch, and need to know what happens next.

A good promo:
- is 20-60 seconds in total (sweet spot 30-50 s)
- OPENS ON A HOOK: within the first 3 seconds something makes a scroller stop - a slap, a reveal, a shocking line, a humiliation, a threat. Never open on an establishing shot, small talk, or a line that needs the previous line to make sense.
- ENDS ON A CLIFFHANGER: cut at an unresolved moment (a question hangs, a threat lands, a reveal is about to happen, someone is about to answer). Never end after the conflict is resolved.
- MAKES SENSE ON ITS OWN to someone who has never seen the show: the conflict and who is wronging whom must be clear inside the clip.
- is usually one continuous stretch; you may join 2-3 segments (from one episode or adjacent episodes) only if it makes the promo clearly better, e.g. hook moment + the escalation it leads to, skipping filler.

A payoff is NOT a cliffhanger. If the clip ends after the villain is beaten, the offer is accepted, the question is answered or the hero has won, cut earlier, at the moment just BEFORE that happens.

Scoring rubric (use the whole scale; scores are compared across the whole show):
  10 = would stop almost anyone, top 1-2 moments of the entire show
  8-9 = clearly strong, among the best few in these episodes
  6-7 = solid but ordinary for this genre
  4-5 = works only for existing fans / needs context
  1-3 = weak
In each batch, give 9 or 10 to at most two promos.

You get transcripts where every block has an ID like [E03#41 01:12.40-01:14.60]. Choose clips ONLY by block IDs from the transcripts given. Quote hook and cliffhanger text exactly as written, one line only, without the speaker name.
Return JSON only."""

PROPOSE_FORMAT = """Return JSON:
{"promos": [
  {"title": "3-6 word label",
   "segments": [{"from": "E03#12", "to": "E03#58"}],
   "hook_block": "E03#12", "hook": "exact quote, or a short description if the hook is an action",
   "cliffhanger_block": "E03#58", "cliffhanger": "exact quote or action",
   "why": "one line on why it works for a stranger",
   "scores": {"hook": 1-10, "cliffhanger": 1-10, "standalone": 1-10},
   "needs_context": "what a new viewer would not understand, or \\"\\"",
   "spoiler_risk": "low|medium|high"}
]}
Rules: each segment runs from the START of block "from" to the END of block "to", inside one episode. Total length of all segments 20-60 s.
hook_block must start within the first 3 seconds of the first segment. cliffhanger_block must be the last spoken line or action of the last segment."""


def _chunk_prompt(show: str, eps: list[Episode], nxt: Episode | None, per_ep: int) -> str:
    parts = [f"SHOW: {show}", ""]
    for ep in eps:
        parts.append(f"=== EPISODE {ep.number} ({fmt(ep.duration)} long) ===")
        parts.append(f"Logline: {ep.logline}")
        parts.append(render(ep.blocks))
        parts.append("")
    if nxt:
        opening = [b for b in nxt.blocks if b.start < 45]
        parts.append(f"=== OPENING OF EPISODE {nxt.number} (only for multi-segment promos that continue into it) ===")
        parts.append(render(opening))
        parts.append("")
    nums = ", ".join(str(e.number) for e in eps)
    parts.append(f"Find the best promos in episodes {nums}: up to {per_ep} per episode, "
                 f"only moments that would genuinely work. Different promos should use different moments.")
    parts.append(PROPOSE_FORMAT)
    return "\n".join(parts)


_SPEAKER = re.compile(r"\b[A-Z][A-Z' .]+(?:\([^)]*\))*\s*:\s*")
_WORD = re.compile(r"[a-z']+")


def _piece_found(q: str, blocks: list[Block]) -> bool:
    q = q.lower().strip().strip('"“” ').strip()
    if len(q) < 4:
        return True
    qw = set(_WORD.findall(q))
    for b in blocks:
        t = b.text.lower()
        if q in t or t in q or difflib.SequenceMatcher(None, q, t).ratio() >= 0.75:
            return True
        # paraphrased actions: most of the quote's words appear in one block
        if len(qw) >= 5 and len(qw & set(_WORD.findall(t))) / len(qw) >= 0.6:
            return True
    return False


def _fuzzy_in(quote: str, blocks: list[Block]) -> bool:
    """True if every line of the quote can be found in the clip.
    Handles 'NAME (tone): "line"' prefixes and several lines joined together."""
    text = _SPEAKER.sub("\n", quote)
    pieces = [p for p in re.split(r'\n|"\s+"|"\s*\n', text) if p.strip(' "“”')]
    return all(_piece_found(p, blocks) for p in pieces) if pieces else True


def _total(segs: list[dict]) -> float:
    return sum(s["end"] - s["start"] for s in segs)


def realize(raw: dict, by_id: dict[str, Block], eps: dict[int, Episode]) -> tuple[dict | None, str]:
    """Turn a model proposal (block IDs) into checked timestamps.
    Returns (promo, "") or (None, reason_rejected)."""
    segs, flags = [], []
    for seg in raw.get("segments") or []:
        a, b = by_id.get(str(seg.get("from", "")).strip()), by_id.get(str(seg.get("to", "")).strip())
        if not a or not b:
            return None, f"unknown block id {seg}"
        if a.episode != b.episode:
            # split a cross-episode range into two segments
            ea, eb = eps[a.episode], eps[b.episode]
            segs.append({"episode": a.episode, "start": a.start, "end": ea.duration})
            segs.append({"episode": b.episode, "start": 0.0, "end": b.end})
            continue
        if b.end <= a.start:
            return None, f"segment ends before it starts {seg}"
        segs.append({"episode": a.episode, "start": a.start, "end": b.end})
    if not segs:
        return None, "no segments"

    # editing rules: start/end on shot cuts, never mid-line
    for s in segs:
        ep = eps[s["episode"]]
        s["start"] = H.snap_start(ep, s["start"])
        s["end"] = H.snap_end(ep, s["end"])

    hook_b = by_id.get(str(raw.get("hook_block", "")).strip())
    cliff_b = by_id.get(str(raw.get("cliffhanger_block", "")).strip())

    # 3-second hook rule: if the hook lands late, trim the dead air in front of it
    first = segs[0]
    if hook_b and hook_b.episode == first["episode"] and first["start"] <= hook_b.start <= first["end"]:
        if hook_b.start - first["start"] > 3.0:
            new_start = H.snap_start(eps[first["episode"]], hook_b.start)
            if _total(segs) - (new_start - first["start"]) >= 20:
                first["start"] = new_start
                flags.append("trimmed_to_hook")
            else:
                flags.append("hook_late")
    elif hook_b:
        flags.append("hook_outside_clip")
    else:
        flags.append("hook_block_missing")

    # never open mid-sentence: if the first line is the second half of a sentence
    # (a line starting with "..."), start on the line it continues instead
    ep0 = eps[first["episode"]]
    lead = next((b for b in H.blocks_in(ep0, first["start"], first["end"]) if b.kind == "dialogue"), None)
    if lead and (lead.text.startswith(("...", "…")) or lead.text[:1].islower()):
        prev = next((b for b in reversed(ep0.blocks[:lead.idx]) if b.kind == "dialogue"), None)
        if prev:
            new_start = H.snap_start(ep0, prev.start)
            if _total(segs) + (first["start"] - new_start) <= 60:
                first["start"] = new_start
                flags.append("extended_to_full_sentence")
            else:
                flags.append("opens_mid_sentence")

    # cliffhanger rule: cut right after the unresolved moment, not after the answer
    last = segs[-1]
    # if the quoted cliffhanger is an earlier line than the block the model named,
    # trust the quote (the words are what the model judged)
    q = (raw.get("cliffhanger") or "").strip()
    if q and cliff_b:
        cand = [b for b in H.blocks_in(eps[last["episode"]], last["start"], last["end"])
                if b.kind != "camera" and b.end < cliff_b.start and _piece_found(q, [b])]
        if cand:
            cliff_b = cand[-1]
            flags.append("cliffhanger_block_mismatch")
    if cliff_b and cliff_b.episode == last["episode"] and last["start"] <= cliff_b.end <= last["end"] + 0.1:
        if last["end"] - cliff_b.end > 2.5:
            new_end = H.snap_end(eps[last["episode"]], cliff_b.end)
            if _total(segs) - (last["end"] - new_end) >= 20:
                last["end"] = new_end
                flags.append("trimmed_to_cliffhanger")
    elif cliff_b:
        flags.append("cliffhanger_outside_clip")

    dur = _total(segs)
    if dur < 19.5:
        return None, f"too short ({dur:.1f}s)"
    if dur > 60.5:
        return None, f"too long ({dur:.1f}s)"

    clip_blocks = [b for s in segs for b in H.blocks_in(eps[s["episode"]], s["start"], s["end"])]
    if raw.get("hook") and not _fuzzy_in(raw["hook"], clip_blocks):
        flags.append("hook_quote_not_found")
    if raw.get("cliffhanger") and not _fuzzy_in(raw["cliffhanger"], clip_blocks):
        flags.append("cliffhanger_quote_not_found")

    first_ep, last_ep = eps[segs[0]["episode"]], eps[segs[-1]["episode"]]
    h_rule, _ = H.hook_score(first_ep, segs[0]["start"])
    c_rule, _ = H.cliff_score(last_ep, segs[-1]["end"])
    sc = raw.get("scores") or {}

    def num(x, d=5.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    scores = {"hook": num(sc.get("hook")), "cliffhanger": num(sc.get("cliffhanger")),
              "standalone": num(sc.get("standalone"))}
    model_score = 0.4 * scores["hook"] + 0.35 * scores["cliffhanger"] + 0.25 * scores["standalone"]
    penalty = 0.75 * sum(f in ("hook_late", "hook_outside_clip", "hook_quote_not_found",
                               "cliffhanger_quote_not_found", "cliffhanger_outside_clip") for f in flags)
    penalty += {"high": 0.5, "medium": 0.2}.get(str(raw.get("spoiler_risk", "")).lower(), 0)
    return {
        "title": raw.get("title", ""),
        "segments": [{"episode": s["episode"], "start": round(s["start"], 2), "end": round(s["end"], 2),
                      "start_ts": fmt(s["start"]), "end_ts": fmt(s["end"])} for s in segs],
        "duration": round(dur, 1),
        "hook": raw.get("hook", ""), "cliffhanger": raw.get("cliffhanger", ""), "why": raw.get("why", ""),
        "needs_context": raw.get("needs_context", ""), "spoiler_risk": raw.get("spoiler_risk", ""),
        "scores": scores,
        "rule_scores": {"hook": round(h_rule, 1), "cliffhanger": round(c_rule, 1)},
        "score": round(model_score - penalty, 2),
        "flags": flags,
        "transcript": render(clip_blocks, with_camera=False),
    }, ""


def _overlap(a: dict, b: dict) -> float:
    ov = 0.0
    for x in a["segments"]:
        for y in b["segments"]:
            if x["episode"] == y["episode"]:
                ov += max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
    return ov / min(a["duration"], b["duration"])


def dedupe(promos: list[dict], thresh: float = 0.5) -> tuple[list[dict], list[dict]]:
    keep, dropped = [], []
    for p in sorted(promos, key=lambda p: -p["score"]):
        if any(_overlap(p, k) > thresh for k in keep):
            dropped.append(p)
        else:
            keep.append(p)
    return keep, dropped


EDITOR_SYSTEM = """You are the head of growth marketing for a microdrama app. You decide which promo clips to post first.
Consider: stopping power of the first 3 seconds, how badly the ending makes people want episode 1, whether a total stranger understands it, variety (don't post three clips of the same beat), and funnel fit (a new viewer who clicks starts at episode 1, so moments from early episodes convert better unless a later moment is far stronger; avoid clips that spoil the show's big twists).
Return JSON only."""


def editor_rank(model: str, promos: list[dict], top: int, rpm: float, raw_dir: Path) -> list[dict]:
    lines = []
    for p in promos:
        segs = " + ".join(f"E{s['episode']} {s['start_ts']}-{s['end_ts']}" for s in p["segments"])
        lines.append(json.dumps({"id": p["id"], "title": p["title"], "where": segs, "duration": p["duration"],
                                 "hook": p["hook"], "cliffhanger": p["cliffhanger"], "why": p["why"],
                                 "needs_context": p["needs_context"], "scores": p["scores"]}, ensure_ascii=False))
    prompt = ("Candidate promos:\n" + "\n".join(lines) +
              f"\n\nPick the {top} to post first, in posting order. Return JSON: "
              '{"ranking": [{"id": "P07", "reason": "one line: why this slot"}], '
              '"notes": "one or two lines on the overall strategy"}')
    data, raw, _, used = complete_json(model, EDITOR_SYSTEM, prompt, rpm=rpm)
    (raw_dir / "editor_ranking.json").write_text(raw, encoding="utf-8")
    if isinstance(data, list):
        data = {"ranking": data}
    data["_model"] = used
    return data


def run(eps: list[Episode], show: str, model: str, out: Path, per_ep: int = 3,
        chunk: int = 5, top: int = 15, rpm: float = 8, log=print,
        rank_model: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw_model_outputs"
    raw_dir.mkdir(exist_ok=True)
    epmap = {e.number: e for e in eps}
    by_id = {b.bid: b for e in eps for b in e.blocks}
    proposals, rejected = [], []
    used_by: dict[str, str] = {}

    if model == "heuristic":
        for ep in eps:
            for c in H.heuristic_candidates(ep, per_ep):
                s = c["segments"][0]
                c.update(title=f"E{ep.number} rule-based pick", duration=round(s["end"] - s["start"], 1),
                         needs_context="", spoiler_risk="", flags=[], score=c["heuristic"],
                         rule_scores=c["scores"])
                s.update(start_ts=fmt(s["start"]), end_ts=fmt(s["end"]))
                c["transcript"] = render(H.blocks_in(ep, s["start"], s["end"]), with_camera=False)
                proposals.append(c)
    else:
        for i in range(0, len(eps), chunk):
            group = eps[i:i + chunk]
            nxt = epmap.get(group[-1].number + 1)
            prompt = _chunk_prompt(show, group, nxt, per_ep)
            name = f"episodes_{group[0].number:02d}-{group[-1].number:02d}"
            log(f"  model call: {name}")
            data, raw, cached, used = complete_json(model, PROPOSE_SYSTEM, prompt, rpm=rpm)
            used_by[name] = used
            log(f"    -> {used}{' (cached)' if cached else ''}")
            (raw_dir / f"{name}.json").write_text(raw, encoding="utf-8")
            items = data if isinstance(data, list) else data.get("promos", [])
            for r in items:
                if not isinstance(r, dict):
                    rejected.append({"chunk": name, "reason": "malformed item", "proposal": {"title": str(r)[:60]}})
                    continue
                p, why = realize(r, by_id, epmap)
                if p:
                    p["model"] = used
                    proposals.append(p)
                else:
                    rejected.append({"chunk": name, "reason": why, "proposal": r})

    kept, dupes = dedupe(proposals)
    kept.sort(key=lambda p: -p["score"])
    for n, p in enumerate(kept, 1):
        p["id"] = f"P{n:02d}"

    ranking, notes = [], ""
    if model != "heuristic" and kept:
        try:
            ed = editor_rank(rank_model or model, kept[: max(top * 2, 20)], top, rpm, raw_dir)
            ids = {p["id"]: p for p in kept}
            for r in ed.get("ranking", []):
                if r.get("id") in ids and r["id"] not in [x["id"] for x in ranking]:
                    ranking.append({"id": r["id"], "reason": r.get("reason", "")})
            notes = ed.get("notes", "")
            used_by["ranking"] = ed.get("_model", "")
        except Exception as e:  # ranking falls back to score order
            log(f"  editor ranking failed ({e}); using score order")
    if not ranking:
        ranking = [{"id": p["id"], "reason": "score order"} for p in kept[:top]]

    result = {"show": show, "model": model, "rank_model": rank_model or model, "models_used": used_by, "episodes": len(eps), "promos": kept,
              "ranking": ranking, "ranking_notes": notes,
              "rejected": rejected, "duplicates_removed": len(dupes)}
    (out / "promos.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
