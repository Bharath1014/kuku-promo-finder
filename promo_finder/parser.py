"""Parse Kuku-style timestamped screenplays into structured blocks.

Pure code, no AI: the screenplay format is regular, so parsing it with rules is
cheaper, faster and fully reproducible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TS = r"\((\d+):(\d+(?:\.\d+)?)\)"
TIME_RE = re.compile(rf"^Start:\s*{TS}\s*\|\s*End:\s*{TS}\s*$")
HEAD_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
EP_RE = re.compile(r"episode-(\d+)", re.I)
SCENE_RE = re.compile(r"^SCENE\s+(\d+)")


def to_sec(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def fmt(t: float) -> str:
    m, s = divmod(round(t, 2), 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Block:
    episode: int
    idx: int            # index within episode
    kind: str           # camera | action | dialogue | sfx | other
    speaker: str        # for dialogue
    tone: str           # parenthetical, e.g. "Cold, Commanding"
    text: str
    start: float
    end: float
    scene: int

    @property
    def bid(self) -> str:
        return f"E{self.episode:02d}#{self.idx}"


@dataclass
class Episode:
    number: int
    logline: str
    continuity: str
    blocks: list[Block] = field(default_factory=list)
    path: str = ""

    @property
    def duration(self) -> float:
        return max((b.end for b in self.blocks), default=0.0)

    @property
    def cuts(self) -> list[float]:
        """Shot boundaries (camera block starts/ends): natural edit points."""
        pts = {0.0, self.duration}
        for b in self.blocks:
            if b.kind == "camera":
                pts.add(b.start)
                pts.add(b.end)
        return sorted(pts)


def _classify(head: str) -> tuple[str, str, str]:
    up = head.strip()
    key = re.sub(r"\s*\(.*$", "", up).strip().upper()
    if key == "CAMERA":
        return "camera", "", ""
    if key == "ACTION":
        return "action", "", ""
    if key in ("SOUND FX", "SFX", "SOUND", "MUSIC"):
        return "sfx", "", ""
    parens = re.findall(r"\(([^)]*)\)", up)
    tone = ", ".join(p for p in parens if p.upper() not in ("O.S.", "V.O.", "CONT'D"))
    return "dialogue", key, tone


def parse_episode(path: Path) -> Episode:
    raw = path.read_bytes()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = raw.decode("cp1252", errors="replace")
    lines = txt.splitlines()
    m = EP_RE.search(path.name)
    num = int(m.group(1)) if m else 0
    logline = continuity = ""
    blocks: list[Block] = []
    scene = 0
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("EPISODE LOGLINE:"):
            logline = ln.split(":", 1)[1].strip()
        elif ln.startswith("CONTINUITY NOTES:"):
            continuity = ln.split(":", 1)[1].strip()
        elif SCENE_RE.match(ln):
            scene = int(SCENE_RE.match(ln).group(1))
        else:
            hm = HEAD_RE.match(ln)
            if hm and i + 1 < len(lines) and TIME_RE.match(lines[i + 1].strip()):
                tm = TIME_RE.match(lines[i + 1].strip())
                kind, speaker, tone = _classify(hm.group(1))
                j = i + 2
                text_lines = []
                while j < len(lines) and lines[j].strip() and not HEAD_RE.match(lines[j].strip()):
                    text_lines.append(lines[j].strip())
                    j += 1
                text = " ".join(text_lines).strip().strip('"').strip("“”")
                blocks.append(Block(num, len(blocks), kind, speaker, tone, text,
                                    to_sec(tm.group(1), tm.group(2)),
                                    to_sec(tm.group(3), tm.group(4)), scene))
                i = j
                continue
        i += 1
    return Episode(num, logline, continuity, blocks, str(path))


def load_show(folder: str | Path) -> list[Episode]:
    eps = [parse_episode(p) for p in sorted(Path(folder).glob("*.txt"))]
    return sorted(eps, key=lambda e: e.number)


def render(blocks: list[Block], with_camera: bool = True) -> str:
    """Compact, ID-tagged transcript used in prompts and reports."""
    out = []
    for b in blocks:
        tag = f"[{b.bid} {fmt(b.start)}-{fmt(b.end)}]"
        if b.kind == "dialogue":
            tone = f" ({b.tone})" if b.tone else ""
            out.append(f'{tag} {b.speaker}{tone}: "{b.text}"')
        elif b.kind == "camera":
            if with_camera:
                out.append(f"{tag} CAMERA: {b.text}")
        else:
            out.append(f"{tag} {b.kind.upper()}: {b.text}")
    return "\n".join(out)
