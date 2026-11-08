"""Write human-readable outputs: ranked promo list, CSV for editors, quality report."""
from __future__ import annotations

import csv
import statistics
from collections import Counter
from pathlib import Path


def _where(p: dict) -> str:
    return " + ".join(f"Ep {s['episode']} {s['start_ts']}-{s['end_ts']}" for s in p["segments"])


def write_reports(res: dict, out: Path) -> None:
    promos = {p["id"]: p for p in res["promos"]}

    # CSV: one row per promo, with an empty column for the editor's verdict
    with open(out / "promos.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "title", "segments", "duration_s", "hook", "cliffhanger", "why",
                    "score", "hook", "cliff", "standalone", "rule_hook", "rule_cliff",
                    "flags", "needs_context", "spoiler_risk", "editor_verdict"])
        rank_of = {r["id"]: i + 1 for i, r in enumerate(res["ranking"])}
        for p in res["promos"]:
            sc, rs = p["scores"], p["rule_scores"]
            w.writerow([rank_of.get(p["id"], ""), p["id"], p["title"], _where(p), p["duration"],
                        p["hook"], p["cliffhanger"], p["why"], p["score"], sc.get("hook"),
                        sc.get("cliffhanger"), sc.get("standalone"), rs.get("hook"), rs.get("cliffhanger"),
                        ";".join(p["flags"]), p["needs_context"], p["spoiler_risk"], ""])

    # Ranked list
    L = [f"# Promos: {res['show']}", "",
         f"Model: `{res['model']}` (ranking: `{res.get('rank_model', res['model'])}`) | episodes: {res['episodes']} | promos found: {len(res['promos'])}", ""]
    if res.get("models_used"):
        L += ["Models actually used: " + ", ".join(f"{k}: `{v}`" for k, v in res["models_used"].items()), ""]
    if res.get("ranking_notes"):
        L += [f"**Posting strategy:** {res['ranking_notes']}", ""]
    L += ["## Post these first", ""]
    for i, r in enumerate(res["ranking"], 1):
        p = promos[r["id"]]
        L += [f"### {i}. {p['title']} ({p['id']}, {p['duration']} s)",
              f"- **Cut:** {_where(p)}",
              f"- **Hook:** {p['hook']}",
              f"- **Cliffhanger:** {p['cliffhanger']}",
              f"- **Why it works:** {p['why']}",
              f"- **Why this slot:** {r['reason']}",
              f"- Scores (hook/cliff/standalone): {p['scores']['hook']}/{p['scores']['cliffhanger']}/"
              f"{p['scores']['standalone']} | rule check: {p['rule_scores']['hook']}/{p['rule_scores']['cliffhanger']}"
              + (f" | flags: {', '.join(p['flags'])}" if p["flags"] else ""), ""]
    L += ["## All promos (score order)", "",
          "| id | where | sec | hook | cliffhanger | score | flags |", "|---|---|---|---|---|---|---|"]
    for p in res["promos"]:
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")[:90]
        L.append(f"| {p['id']} | {_where(p)} | {p['duration']} | {cell(p['hook'])} | "
                 f"{cell(p['cliffhanger'])} | {p['score']} | {', '.join(p['flags'])} |")
    (out / "promos.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # Quality report: automatic checks only. Human review goes in the CSV.
    ps = res["promos"]
    flags = Counter(f for p in ps for f in p["flags"])
    durs = [p["duration"] for p in ps]
    eps_cov = {s["episode"] for p in ps for s in p["segments"]}
    multi = sum(len(p["segments"]) > 1 for p in ps)
    Q = [f"# Quality report: {res['show']}", "", f"Model: `{res['model']}`", "",
         "## Hard rules (enforced in code, so they hold for every promo)",
         "- every promo is 20-60 s in total",
         "- every cut lands on a shot boundary and never cuts a line of dialogue in half",
         "- every timestamp comes from the screenplay (the model picks block IDs; code converts them)", "",
         "## Counts",
         f"- promos kept: {len(ps)} | rejected by validation: {len(res['rejected'])} | "
         f"near-duplicates removed: {res['duplicates_removed']}",
         f"- multi-segment promos: {multi}",
         f"- episodes with at least one promo: {len(eps_cov)}/{res['episodes']}"]
    if durs:
        Q.append(f"- duration: min {min(durs)} s, median {statistics.median(durs)} s, max {max(durs)} s")
    Q += ["", "## Soft checks (flags)"]
    expl = {
        "trimmed_to_hook": "start moved forward so the hook lands in the first 3 s (auto-fixed)",
        "trimmed_to_cliffhanger": "end moved back so the clip stops on the unresolved moment (auto-fixed)",
        "extended_to_full_sentence": "clip opened on the second half of a sentence; start moved back to the full line (auto-fixed)",
        "opens_mid_sentence": "clip opens mid-sentence and could not be extended without going over 60 s",
        "cliffhanger_block_mismatch": "model's cliffhanger quote is an earlier line than the block it named; cut at the quote",
        "hook_late": "hook lands after 3 s and could not be trimmed without going under 20 s",
        "hook_outside_clip": "model named a hook block that is not in the clip",
        "hook_block_missing": "model gave no valid hook block",
        "cliffhanger_outside_clip": "model named a cliffhanger block that is not in the clip",
        "hook_quote_not_found": "hook text not found in the clip: possible invented quote",
        "cliffhanger_quote_not_found": "cliffhanger text not found in the clip: possible invented quote",
    }
    if flags:
        for f, n in flags.most_common():
            Q.append(f"- `{f}` x{n}: {expl.get(f, '')}")
    else:
        Q.append("- none")
    Q += ["", "## Where model and rules disagree (review these by hand)", ""]
    def _disagree(p):
        m, r = p["scores"].get("hook"), p["rule_scores"].get("hook")
        if m is None or r is None:
            return False
        m, r = float(m), float(r)
        return (m >= 8 and r < 1.5) or (r >= 7 and m <= 5)
    dis = [p for p in ps if _disagree(p)]
    if dis:
        for p in dis[:15]:
            Q.append(f"- {p['id']} ({_where(p)}): model hook {p['scores']['hook']} vs rule hook "
                     f"{p['rule_scores']['hook']}. Hook: \"{p['hook'][:80]}\"")
    else:
        Q.append("- none above threshold")
    if res["rejected"]:
        Q += ["", "## Rejected proposals", ""]
        for r in res["rejected"]:
            Q.append(f"- [{r['chunk']}] {r['reason']}: {r['proposal'].get('title', '')}")
    (out / "quality_report.md").write_text("\n".join(Q) + "\n", encoding="utf-8")
