"""Find promos in a microdrama screenplay pack.

  python run.py --input "path/to/show folder" --model gemini:gemini-3.6-flash
  python run.py --input "path/to/show folder" --model heuristic      # no API key needed
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from promo_finder.llm import LLMError
from promo_finder.parser import load_show
from promo_finder.pipeline import run
from promo_finder.report import write_reports


def load_dotenv(path: Path = Path(".env")) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="folder of episode .txt screenplays")
    ap.add_argument("--model", default=os.environ.get("PROMO_MODEL", "gemini:gemini-3.6-flash"),
                    help="provider:model, or 'heuristic' (see promo_finder/llm.py)")
    ap.add_argument("--out", default=None, help="output folder (default outputs/<model>)")
    ap.add_argument("--episodes", default=None, help="range like 1-10")
    ap.add_argument("--per-episode", type=int, default=3)
    ap.add_argument("--chunk", type=int, default=5, help="episodes per model call")
    ap.add_argument("--top", type=int, default=15, help="how many to rank for posting")
    ap.add_argument("--rank-model", default=None, help="model for the final ranking call (default: --model)")
    ap.add_argument("--rpm", type=float, default=8, help="max model requests per minute")
    a = ap.parse_args()
    load_dotenv()

    eps = load_show(a.input)
    if not eps:
        print("No episode .txt files found in", a.input)
        return 1
    if a.episodes:
        lo, _, hi = a.episodes.partition("-")
        eps = [e for e in eps if int(lo) <= e.number <= int(hi or lo)]
    show = Path(a.input).name
    out = Path(a.out or Path("outputs") / re.sub(r"[^\w.-]+", "_", a.model))
    print(f"{show}: {len(eps)} episodes, {sum(len(e.blocks) for e in eps)} blocks -> {out}")
    t0 = time.time()
    try:
        res = run(eps, show, a.model, out, per_ep=a.per_episode, chunk=a.chunk, top=a.top, rpm=a.rpm,
                  rank_model=a.rank_model)
    except LLMError as e:
        print(f"Model error: {e}")
        return 2
    write_reports(res, out)
    print(f"done in {time.time() - t0:.0f}s: {len(res['promos'])} promos, "
          f"{len(res['rejected'])} rejected. See {out / 'promos.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
