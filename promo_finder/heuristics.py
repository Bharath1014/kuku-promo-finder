"""Rule-based signals and editing rules (no AI).

Used three ways:
  1. offline baseline when no model is available (--model heuristic)
  2. editing rules that every promo must obey (snap to cuts, never cut a line
     in half, hook inside the first 3 s, end right after the cliffhanger)
  3. a second, independent score to sanity-check the model's ranking
"""
from __future__ import annotations

import re

from .parser import Block, Episode

HOOK_WORDS = re.compile(
    r"\b(slap\w*|strik\w*|struck|hit|punch\w*|grab\w*|shov\w*|throw\w*|threw|burn\w*|fire|flame\w*|"
    r"blood\w*|knife|blade|sword|dagger|gun|chain\w*|crop|iron|kill\w*|dead|die|death|murder\w*|"
    r"poison\w*|witch\w*|execut\w*|hang\w*|guilty|arrest\w*|liar|lie|lies|betray\w*|traitor|"
    r"marry|married|marrying|wedding|divorce|bride|husband|wife|duke|duchess|heir|"
    r"pregnant|kiss\w*|scream\w*|collaps\w*|explo\w*|shatter\w*|crash\w*|kneel|beg\w*|"
    r"never|how dare|get out|enough)\b", re.I)
STRONG_TONES = re.compile(
    r"(furious|outraged|shout|scream|yell|cry|crying|sob|gasp|shock|horrif|menac|threat|"
    r"cold|cruel|venom|gloat|mock|sneer|desperate|enraged|livid|seeth|defiant|commanding|"
    r"accus|vicious|hiss|snarl|roar|terrif)", re.I)
CLIFF_SFX = re.compile(r"(cliffhanger|ominous|sting|swell|drone|thud|boom|gasp|silence|freeze)", re.I)
WEAK_OPEN = re.compile(r"^(and|but|so|then|yes|no|okay|well|it|that|this|they|he|she)\b", re.I)
ESTABLISHING = re.compile(r"(establishing|wide exterior|aerial|exterior of|drone shot)", re.I)

EPS = 0.05


def clean_point(ep: Episode, t: float) -> bool:
    """True if cutting at t does not chop a line of dialogue in half."""
    return not any(b.kind == "dialogue" and b.start < t - EPS and b.end > t + EPS for b in ep.blocks)


def snap_start(ep: Episode, t: float) -> float:
    # an earlier full line at the start only adds context (often the line the hook
    # answers), so here we only forbid cutting a line in half
    cands = [c for c in ep.cuts if t - 2.5 <= c <= t + 0.3 and clean_point(ep, c)]
    if cands:
        before = [c for c in cands if c <= t + EPS]
        return max(before) if before else min(cands)
    for b in ep.blocks:
        if b.kind == "dialogue" and b.start < t < b.end:
            return b.start
    return t


def snap_end(ep: Episode, t: float) -> float:
    # never stretch far enough to pull in the start of the next line
    next_start = min((b.start for b in ep.blocks if b.kind == "dialogue" and b.start >= t - EPS),
                     default=ep.duration)
    cands = [c for c in ep.cuts if t - 0.3 <= c <= min(t + 2.5, next_start + EPS) and clean_point(ep, c)]
    if cands:
        after = [c for c in cands if c >= t - EPS]
        return min(after) if after else max(cands)
    for b in ep.blocks:
        if b.kind == "dialogue" and b.start < t < b.end:
            return b.end
    return min(t, ep.duration)


def blocks_in(ep: Episode, s: float, e: float) -> list[Block]:
    return [b for b in ep.blocks if b.start >= s - EPS and b.end <= e + EPS]


def line_strength(b: Block) -> float:
    if b.kind == "camera":
        return 0.0
    score = 0.0
    text = b.text
    score += 1.0 * min(2, len(HOOK_WORDS.findall(text)))
    if b.kind == "dialogue":
        if STRONG_TONES.search(b.tone):
            score += 1.0
        if "!" in text:
            score += 0.5
        if "?" in text:
            score += 0.3
    if b.kind == "action":
        score += 0.5
    if b.kind == "sfx":
        score += 0.7
    return score


def hook_score(ep: Episode, s: float) -> tuple[float, Block | None]:
    """Strength of what happens in the first 3 seconds after s (0-10)."""
    first = [b for b in ep.blocks if s - EPS <= b.start < s + 3.0 and b.kind != "camera"]
    cams = [b for b in ep.blocks if s - EPS <= b.start < s + 3.0 and b.kind == "camera"]
    if not first:
        return 0.0, None
    best = max(first, key=line_strength)
    sc = sum(line_strength(b) for b in first)
    lead_dialogue = next((b for b in first if b.kind == "dialogue"), None)
    if lead_dialogue and WEAK_OPEN.match(lead_dialogue.text):
        sc -= 1.0
    if any(ESTABLISHING.search(c.text) for c in cams):
        sc -= 1.0
    return max(0.0, min(10.0, sc * 1.25)), best


def cliff_score(ep: Episode, e: float) -> tuple[float, Block | None]:
    """How unresolved the last ~4 seconds before e are (0-10)."""
    tail = [b for b in ep.blocks if e - 4.5 <= b.end <= e + EPS and b.kind != "camera"]
    if not tail:
        return 0.0, None
    sc = 0.0
    last_line = next((b for b in reversed(tail) if b.kind == "dialogue"), None)
    if last_line:
        t = last_line.text.rstrip()
        if t.endswith("?"):
            sc += 2.5
        if t.endswith(("...", "…", "-", "—")):
            sc += 2.0
        if t.endswith("!"):
            sc += 1.0
        sc += 0.6 * min(3, len(HOOK_WORDS.findall(t)))
    if any(b.kind == "sfx" and CLIFF_SFX.search(b.text) for b in tail):
        sc += 2.5
    if e >= ep.duration - 1.0:
        sc += 3.0   # episodes are written to end on a cliffhanger
    nxt = next((b for b in ep.blocks if b.start >= e - EPS and b.kind == "dialogue"), None)
    if nxt and last_line and nxt.speaker != last_line.speaker:
        sc += 0.5   # someone is about to answer: we cut before they do
    return max(0.0, min(10.0, sc)), last_line or tail[-1]


def intensity(ep: Episode, s: float, e: float) -> float:
    bl = [b for b in blocks_in(ep, s, e) if b.kind != "camera"]
    if not bl or e <= s:
        return 0.0
    return min(10.0, sum(line_strength(b) for b in bl) / (e - s) * 12)


def duration_fit(d: float) -> float:
    if d < 20 or d > 60:
        return 0.0
    if 28 <= d <= 50:
        return 10.0
    return 7.0


def heuristic_candidates(ep: Episode, k: int = 3) -> list[dict]:
    """Search every clean (start cut, end cut) pair of 20-60 s; keep the k best
    that do not overlap."""
    cuts = [c for c in ep.cuts if clean_point(ep, c)]
    hooks = {c: hook_score(ep, c) for c in cuts}
    cliffs = {c: cliff_score(ep, c) for c in cuts}
    scored = []
    for s in cuts:
        hs, hb = hooks[s]
        if hs < 2:
            continue
        for e in cuts:
            d = e - s
            if d < 20 or d > 60:
                continue
            cs, cb = cliffs[e]
            total = 0.4 * hs + 0.35 * cs + 0.15 * intensity(ep, s, e) + 0.1 * duration_fit(d)
            scored.append((total, s, e, hb, cb, hs, cs))
    scored.sort(key=lambda x: -x[0])
    chosen = []
    for total, s, e, hb, cb, hs, cs in scored:
        if any(min(e, c["segments"][0]["end"]) - max(s, c["segments"][0]["start"]) > 5 for c in chosen):
            continue
        chosen.append({
            "segments": [{"episode": ep.number, "start": s, "end": e}],
            "hook": hb.text if hb else "",
            "cliffhanger": cb.text if cb else "",
            "why": "Rule-based pick: strong opening line/action and an unresolved ending.",
            "scores": {"hook": round(hs, 1), "cliffhanger": round(cs, 1), "standalone": None},
            "heuristic": round(total, 2),
        })
        if len(chosen) >= k:
            break
    return chosen
