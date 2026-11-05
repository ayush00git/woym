"""Build the WOYM rough cut (no narration): media/raw/rough-cut.mp4, ~3:00, 1920x1080, 25 fps.

  uv run python scripts/video/build_rough_cut.py            # full build
  uv run python scripts/video/build_rough_cut.py --beats 3,9  # rebuild some beats, then re-join

Reproducible from:
  docs/drafts/video-script.md   beat table (Time + Caption columns are read live on every run)
  media/cold-open.html          recorded in REAL time (CSS transitions)
  media/raw/take-905.webm       final take; video position = 10.1 s + demo clock
  docs/trust-boundary.svg       rendered at 1920x1080, with timed highlights
  README.md                     "How Switch is used" table (beat 13)
  media/raw/take-905.timeline.txt  the take's real demo.py stdout (beat 14 terminal replay)
  var/remotes/bizclock.git      the fix diff b6a243e (beat 9 insert)

Rules from the script's edit notes:
  - take beats play at REAL speed and then hold the last frame; nothing plays faster,
    except beat 9, which is sped up and says so on screen ("⏩ SPED UP · real time …");
  - beat 11 freeze-frames the FIXED card;
  - the run clock shows the take's demo clock and stops while a frame is held;
  - every beat keeps >= 0.5 s of slack after its narration (checked, printed).
Chrome is driven with scripts/record.py's DevTools helpers; ffmpeg does the rest
(libopenh264, since this ffmpeg has no libx264).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import record as R  # noqa: E402  (CDP + Chrome launcher)
import websockets  # noqa: E402

SCRIPT_MD = ROOT / "docs" / "drafts" / "video-script.md"
TAKE = ROOT / "media" / "raw" / "take-905.webm"
TIMELINE = ROOT / "media" / "raw" / "take-905.timeline.txt"
COLD_OPEN = ROOT / "media" / "cold-open.html"
SVG = ROOT / "docs" / "trust-boundary.svg"
README = ROOT / "README.md"
REMOTE = ROOT / "var" / "remotes" / "bizclock.git"
FIX_SHA = "b6a243e"
WORK = ROOT / "media" / "raw" / "cut-work"
OUT = ROOT / "media" / "raw" / "rough-cut.mp4"
W, H, FPS = 1920, 1080, 25
DEMO0 = 10.1  # video position of demo clock 0 in take-905
BG = "#0d1117"
ENC = ["-c:v", "libopenh264", "-b:v", "8M", "-pix_fmt", "yuv420p", "-r", str(FPS)]
MONO = "/usr/share/fonts/jetbrains-mono-fonts/JetBrainsMono-Regular.otf"

# Thread pane in take-905 frames (expanded RHS), from frame checks: x 770..1915, y 56..1076.
PANE = (1145, 1020, 770, 56)
PANE_POS = ((W - PANE[0]) // 2, 30)

# Take beats: (video start, play seconds, speed). Positions from the script's cue sheet.
TAKE_BEATS = {
    4: (11.5, 5.8, 1.0),    # report -> NOT REPRODUCED
    5: (17.3, 2.7, 1.0),    # consent card -> yes -> approved
    6: (20.0, 7.0, 1.0),    # lineup -> bisect card, culprit tz
    7: (27.0, 1.5, 1.0),    # confirm -> CONFIRMED ON BOTH MACHINES
    8: (28.5, 1.1, 1.0),    # dev's direct ask -> Switch refusal
    9: (29.6, 30.2, 30.2 / 16.0),  # patch it -> Claude working -> fix pushed (SPED UP), 16 s + 4 s diff
    10: (59.8, 5.7, 1.0),   # matrix 4/4 green
    11: (65.5, 2.5, 1.0),   # install consent -> FIXED, then freeze
}


def log(m: str) -> None:
    print(f"[cut] {m}", flush=True)


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd[:12])}…\n{r.stderr[-2000:]}")


# ------------------------------------------------------------------ script

def parse_script() -> list[dict]:
    beats = []
    for line in SCRIPT_MD.read_text().splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*(\d+):(\d+)[–-](\d+):(\d+)\s*\|(.*)$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(6).split("|")]
        n = int(m.group(1))
        start = int(m.group(2)) * 60 + int(m.group(3))
        end = int(m.group(4)) * 60 + int(m.group(5))
        beats.append({"n": n, "dur": end - start, "screen": cells[0], "narration": cells[1],
                      "words": int(re.sub(r"\D", "", cells[2]) or 0), "caption": cells[3]})
    if not beats:
        raise SystemExit(f"no beats parsed from {SCRIPT_MD}")
    return beats


def clean(md: str) -> str:
    md = md.replace("`", "").replace("**", "")
    return md.strip().strip('"')


# ------------------------------------------------------------------ chrome

async def _page():
    proc, wsurl, prof = await R.launch_chrome(False, 1.0)
    ws = await websockets.connect(wsurl, max_size=256 * 2**20)
    cdp = R.CDP(ws)
    await cdp.send("Page.enable")
    await cdp.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
    return proc, ws, cdp, prof


async def _close(proc, ws, cdp, prof):
    cdp.reader.cancel()
    await ws.close()
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(prof, ignore_errors=True)


async def shot(url: str, out: Path, transparent: bool = False, wait: float = 0.8) -> Path:
    proc, ws, cdp, prof = await _page()
    try:
        if transparent:
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
        await cdp.send("Page.navigate", url=url)
        await asyncio.sleep(wait)
        r = await cdp.send("Page.captureScreenshot", format="png")
        out.write_bytes(base64.b64decode(r["data"]))
    finally:
        await _close(proc, ws, cdp, prof)
    return out


async def record_page(url: str, seconds: float, out: Path) -> Path:
    """Real-time screencast of a page (CSS animations run in real time)."""
    proc, ws, cdp, prof = await _page()
    latest = {"jpg": None}

    async def on_frame(p):
        latest["jpg"] = base64.b64decode(p["data"])
        try:
            await cdp.send("Page.screencastFrameAck", sessionId=p["sessionId"])
        except Exception:
            pass

    cdp.on("Page.screencastFrame", on_frame)
    ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe",
                           "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                           "-vf", f"scale={W}:{H},format=yuv420p", *ENC, str(out)], stdin=subprocess.PIPE)
    try:
        await cdp.send("Page.startScreencast", format="jpeg", quality=92, maxWidth=W, maxHeight=H, everyNthFrame=1)
        await cdp.send("Page.navigate", url=url)
        while latest["jpg"] is None:
            await asyncio.sleep(0.01)
        t0 = time.monotonic()
        k = 0
        while k < seconds * FPS:
            ff.stdin.write(latest["jpg"])
            k += 1
            await asyncio.sleep(max(0.0, t0 + k / FPS - time.monotonic()))
        await cdp.send("Page.stopScreencast")
    finally:
        ff.stdin.close()
        ff.wait()
        await _close(proc, ws, cdp, prof)
    return out


def page_file(name: str, body: str, css: str = "") -> str:
    p = WORK / f"{name}.html"
    p.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;font-family:'Noto Sans',sans-serif;color:#e6edf3}}
.card{{width:{W}px;height:{H}px;background:{BG};display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center}}
code,pre,.mono{{font-family:'JetBrains Mono',monospace}}
{css}</style></head><body>{body}</body></html>""")
    return p.as_uri()


# ------------------------------------------------------------------ overlays

def caption_png(n: int, text: str, badge: str | None = None) -> Path:
    """Transparent 1920x1080 overlay: caption band at the bottom (+ optional top-right badge)."""
    text = clean(text)
    small = ""
    if "(small)" in text:
        text, small = [s.strip(" ·") for s in text.split("(small)", 1)]
    parts = [html.escape(s.strip().strip('"“”')) for s in text.split(" · ") if s.strip()]
    cap = " <span class='dot'>·</span> ".join(parts)
    body = ""
    if cap:
        body += f"<div class='cap'>{cap}{f'<div class=small>{html.escape(small)}</div>' if small else ''}</div>"
    if badge:
        body += f"<div class='badge'>{html.escape(badge)}</div>"
    url = page_file(f"cap-{n}{'-badge' if badge else ''}", body, """
html,body{background:transparent}
.cap{position:absolute;left:50%;bottom:34px;transform:translateX(-50%);max-width:1700px;background:rgba(1,4,9,.86);
     border:1px solid #30363d;border-radius:14px;padding:16px 30px;font-size:30px;line-height:1.35;text-align:center}
.dot{color:#e3b341;font-weight:700;margin:0 .35em}.small{font-size:22px;color:#8b949e;margin-top:6px}
.badge{position:absolute;top:28px;right:36px;background:#9e6a03;color:#fff;font-weight:700;font-size:30px;
       border-radius:10px;padding:10px 22px;letter-spacing:.02em}""")
    return asyncio.run(shot(url, WORK / f"cap-{n}{'-badge' if badge else ''}.png", transparent=True))


def clock_filter(c0: float, play_out: float, speed: float) -> str:
    """drawtext showing the take's demo clock; it stops while the last frame is held."""
    c = f"({c0:.3f}+min(t\\,{play_out:.3f})*{speed:.5f})"
    txt = (f"run clock %{{eif\\:floor({c}/60)\\:d}}\\:%{{eif\\:mod(floor({c})\\,60)\\:d\\:2}}"
           f".%{{eif\\:mod(floor({c}*10)\\,10)\\:d}}")
    return (f"drawtext=fontfile={MONO}:text='{txt}':x=40:y=40:fontsize=34:fontcolor=#e6edf3:"
            f"box=1:boxcolor=0x010409@0.85:boxborderw=14")


# ------------------------------------------------------------------ segments

def from_take(n: int, dur: float, clip_out: Path) -> None:
    start, play, speed = TAKE_BEATS[n]
    play_out = play / speed
    hold = max(0.0, dur - play_out)
    cw, ch, cx, cy = PANE
    vf = (f"crop={cw}:{ch}:{cx}:{cy},setpts=(PTS-STARTPTS)/{speed:.5f},fps={FPS},"
          f"tpad=stop_mode=clone:stop_duration={hold + 1:.3f},trim=duration={dur:.3f},"
          f"pad={W}:{H}:{PANE_POS[0]}:{PANE_POS[1]}:color={BG},{clock_filter(start - DEMO0, play_out, speed)}")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{play:.3f}",
         "-i", str(TAKE), "-vf", vf, "-an", *ENC, str(clip_out)])


def still(png: Path, dur: float, out: Path) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", str(png),
         "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p", *ENC, str(out)])


def fit(src: Path, dur: float, out: Path, speed: float = 1.0, skip: float = 0.0) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{skip:.3f}", "-i", str(src), "-vf",
         f"setpts=(PTS-STARTPTS)/{speed:.4f},fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.3f},"
         f"trim=duration={dur:.3f},scale={W}:{H},format=yuv420p", "-an", *ENC, str(out)])


def with_caption(base: Path, cap: Path, out: Path, badge: Path | None = None, badge_until: float = 0) -> None:
    inputs = ["-i", str(base), "-i", str(cap)]
    fc = "[0:v][1:v]overlay=0:0[v1]"
    last = "v1"
    if badge:
        inputs += ["-i", str(badge)]
        fc += f";[v1][2:v]overlay=0:0:enable='lt(t\\,{badge_until:.3f})'[v2]"
        last = "v2"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc, "-map", f"[{last}]",
         *ENC, str(out)])


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(out)])


# ------------------------------------------------------------------ beat builders

def b_cold_open(b) -> Path:
    raw = WORK / "co-raw.mp4"
    asyncio.run(record_page(COLD_OPEN.as_uri(), 13.0, raw))
    out = WORK / "b1.mp4"
    # the page plays ~12 s; it's a labelled MOCK-UP, so fit it to the beat at a mild speed-up
    fit(raw, b["dur"], out, speed=12.5 / b["dur"], skip=0.3)
    return out


def b_stat_card(b) -> Path:
    url = page_file("b2", """<div class=card><div style="font-size:120px;font-weight:800;color:#f85149">17%</div>
<div style="font-size:52px;max-width:1500px;line-height:1.3;margin-top:20px">of bug reports are marked <b>non-reproducible</b>.<br>
They stay open <b>~3 months longer</b>.</div></div>""")
    png = asyncio.run(shot(url, WORK / "b2.png"))
    out = WORK / "b2.mp4"
    still(png, b["dur"], out)
    return out


def b_members(b) -> Path:
    """The REAL members panel from the take, unaltered (all 9 members). Setup/recording
    accounts are dimmed to ~50%, never removed: a 9-member room must not look like 6."""
    frame = WORK / "take-0005.png"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "5", "-i", str(TAKE), "-frames:v", "1", str(frame)])
    bg_url = page_file("b3bg", """<div class=card style="align-items:flex-start;padding-left:1010px;box-sizing:border-box;text-align:left">
<div style="font-size:58px;font-weight:700;line-height:1.15">One Switch room,<br>two companies</div>
<div style="font-size:30px;color:#8b949e;margin-top:22px;line-height:1.5">#acme-support-take1 · members panel<br>from the recorded take, nothing removed<br>
<span style="color:#e6edf3">dimmed: setup and recording accounts</span></div></div>""")
    bg = asyncio.run(shot(bg_url, WORK / "b3bg.png"))
    out_png = WORK / "b3.png"
    # Members panel in the take frame: x 1415..1915, y 60..840. Rows (relative to the crop):
    # admin y 185..240, camera 360..415, user 720..775 -> dimmed with a 50% dark box.
    dim = ",".join(f"drawbox=x=0:y={y}:w=500:h=56:color=0x0d1117@0.55:t=fill" for y in (185, 360, 720))
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(bg), "-i", str(frame), "-filter_complex",
         f"[1:v]crop=500:780:1415:60,{dim},scale=550:-1[m];[0:v][m]overlay=380:40", "-frames:v", "1", str(out_png)])
    out = WORK / "b3.mp4"
    still(out_png, b["dur"], out)
    return out


def b_take(b) -> Path:
    out = WORK / f"b{b['n']}.mp4"
    if b["n"] != 9:
        from_take(b["n"], b["dur"], out)
        return out
    # beat 9: 16 s sped-up take + 4 s diff insert
    part_a, part_b = WORK / "b9a.mp4", WORK / "b9b.mp4"
    from_take(9, 16.0, part_a)
    diff = subprocess.run(["git", "--git-dir", str(REMOTE), "diff", "main", FIX_SHA, "--", "src/"],
                          capture_output=True, text=True).stdout
    keep = [ln for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    rows = "".join(f"<div class='{'add' if ln[0] == '+' else 'del'}'>{html.escape(ln)}</div>" for ln in keep)
    url = page_file("b9diff", f"""<div class=card><div style="font-size:30px;color:#8b949e;margin-bottom:24px">
Claude Code's fix · commit {FIX_SHA} · src/index.js (the test file is in the same commit)</div>
<pre class=mono style="text-align:left;font-size:30px;line-height:1.55;background:#161b22;border:1px solid #30363d;
border-radius:12px;padding:28px 40px;margin:0">{rows}</pre></div>""",
                    ".add{color:#3fb950}.del{color:#f85149}")
    still(asyncio.run(shot(url, WORK / "b9diff.png")), b["dur"] - 16.0, part_b)
    concat([part_a, part_b], out)
    return out


def b_trust(b) -> Path:
    svg = SVG.read_text()
    s = min(W / 1200, (H - 150) / 760)  # leave the bottom 150 px for the caption band
    ox, oy = (W - 1200 * s) / 2, 20
    half = b["dur"] / 2

    def box(x, y, w, h, delay):
        return (f"<div class=hl style='left:{ox + x * s:.0f}px;top:{oy + y * s:.0f}px;width:{w * s:.0f}px;"
                f"height:{h * s:.0f}px;animation-delay:{delay}s'></div>")

    url = page_file("b12", f"<div class=card style='display:block;position:relative'>"
                           f"<div style='position:absolute;left:{ox:.0f}px;top:{oy:.0f}px;width:1200px;height:760px;"
                           f"transform:scale({s:.4f});transform-origin:0 0'>{svg}</div>"
                           f"{box(352, 168, 496, 210, 0.6)}{box(16, 548, 1168, 160, half)}</div>",
                    f""".hl{{position:absolute;border:5px solid #e3b341;border-radius:16px;box-shadow:0 0 40px #e3b34199;
opacity:0;animation:hl {half - 0.6:.1f}s ease forwards}}
@keyframes hl{{0%{{opacity:0}}12%{{opacity:1}}88%{{opacity:1}}100%{{opacity:0}}}}""")
    raw = WORK / "b12-raw.mp4"
    asyncio.run(record_page(url, b["dur"] + 0.5, raw))
    out = WORK / "b12.mp4"
    fit(raw, b["dur"], out, skip=0.3)
    return out


def b_features(b) -> Path:
    rows, on = [], False
    for ln in README.read_text().splitlines():
        if ln.startswith("| Switch feature"):
            on = True
            continue
        if on:
            if not ln.startswith("|"):
                break
            cells = [clean(c) for c in ln.strip("|").split("|")]
            if set(cells[0]) <= set("-: "):
                continue
            rows.append(cells)
    trs = "".join(f"<tr><td class=f>{html.escape(r[0])}</td><td>{html.escape(r[1])}</td></tr>" for r in rows)
    url = page_file("b13", f"""<div class=card><div style="font-size:48px;font-weight:700;margin-bottom:30px">How WOYM uses Switch</div>
<table>{trs}</table></div>""", """table{border-collapse:collapse;width:1700px;font-size:27px;text-align:left}
td{border-top:1px solid #30363d;padding:13px 18px;vertical-align:top;line-height:1.3}.f{color:#79c0ff;font-weight:700;width:520px}""")
    out = WORK / "b13.mp4"
    still(asyncio.run(shot(url, WORK / "b13.png")), b["dur"], out)
    return out


def b_terminal(b) -> Path:
    lines = []
    for ln in TIMELINE.read_text().splitlines():
        m = re.match(r"\[video [^\]]+\] (.*)", ln)
        if m:
            lines.append(m.group(1))
    # replay: the real output lines, revealed at an even pace across the beat (sped up; captioned)
    step = (b["dur"] - 1.5) / max(1, len(lines))
    rows = "".join(f"<div class=l style='animation-delay:{0.4 + i * step:.2f}s'>{html.escape(x)}</div>"
                   for i, x in enumerate(lines))
    url = page_file("b14", f"""<div class=term><div class=bar>~/switch — make demo</div><div class=scr id=s>
<div class=l style='animation-delay:0s'><span style='color:#3fb950'>$</span> make demo FIXER=claude</div>{rows}</div></div>
<script>const s=document.getElementById('s');setInterval(()=>{{s.scrollTop=s.scrollHeight}},50)</script>""",
                    f""".term{{width:{W}px;height:{H}px;background:{BG};padding:50px 70px;box-sizing:border-box}}
.bar{{font-size:24px;color:#8b949e;margin-bottom:16px}}
.scr{{height:800px;overflow:hidden;font-family:'JetBrains Mono',monospace;font-size:24px;line-height:1.5;white-space:pre}}
.l{{opacity:0;animation:show .01s forwards}}@keyframes show{{to{{opacity:1}}}}""")
    raw = WORK / "b14-raw.mp4"
    asyncio.run(record_page(url, b["dur"] + 0.5, raw))
    out = WORK / "b14.mp4"
    fit(raw, b["dur"], out, skip=0.3)
    return out


def b_end(b) -> Path:
    url = page_file("b15", """<div class=card><div style="font-size:96px;font-weight:800">Works On Your Machine.</div>
<div style="font-size:44px;color:#8b949e;margin-top:26px">“Works on my machine.” Now proven on yours.</div></div>""")
    out = WORK / "b15.mp4"
    still(asyncio.run(shot(url, WORK / "b15.png")), b["dur"], out)
    return out


BUILDERS = {1: b_cold_open, 2: b_stat_card, 3: b_members, 12: b_trust, 13: b_features, 14: b_terminal, 15: b_end}
# Lead's decision (2026-11-05): beat 3 caption names the dimmed accounts instead of hiding them.
CAPTION_OVERRIDE: dict[int, str] = {}  # beat 3's override was dropped once B's script matched the lead's wording
EXTRA_CAPTION = {
    14: "replay of the recorded take's real output, sped up",
}


def build_beat(b) -> Path:
    n = b["n"]
    base = (BUILDERS.get(n) or b_take)(b)
    cap_text = CAPTION_OVERRIDE.get(n, b["caption"])
    if "already in" in cap_text:  # e.g. the MOCK-UP label that the cold open already shows
        cap_text = ""
    if n in EXTRA_CAPTION:
        cap_text = f"{cap_text} · {EXTRA_CAPTION[n]}" if cap_text else EXTRA_CAPTION[n]
    cap = caption_png(n, cap_text)
    badge = None
    if n == 9:
        m = re.search(r"SPED UP · ([^·]+?\))", clean(b["caption"]))
        badge = caption_png(90, "", badge=f"⏩ SPED UP · {m.group(1) if m else 'real time 30 s'}")
    out = WORK / f"beat-{n:02d}.mp4"
    with_caption(base, cap, out, badge, badge_until=16.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", help="comma list of beats to rebuild (others reused from cut-work/)")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    beats = parse_script()
    total = sum(b["dur"] for b in beats)
    log(f"{len(beats)} beats from {SCRIPT_MD.name}, total {total // 60}:{total % 60:02d}")
    for b in beats:
        need = b["words"] / 2.3
        slack = b["dur"] - need
        log(f"beat {b['n']:2}: {b['dur']:2}s, {b['words']:2} words ≈ {need:4.1f}s narration, slack {slack:4.1f}s"
            + ("  <-- under 0.5 s!" if slack < 0.5 else ""))
    only = {int(x) for x in a.beats.split(",")} if a.beats else None
    parts = []
    for b in beats:
        out = WORK / f"beat-{b['n']:02d}.mp4"
        if only is None or b["n"] in only or not out.exists():
            log(f"building beat {b['n']}…")
            out = build_beat(b)
        parts.append(out)
    concat(parts, OUT)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(OUT)],
                         capture_output=True, text=True).stdout.strip()
    log(f"wrote {OUT} ({float(dur):.1f} s)")
    (WORK / "beats.json").write_text(json.dumps([{k: b[k] for k in ("n", "dur", "caption")} for b in beats], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
