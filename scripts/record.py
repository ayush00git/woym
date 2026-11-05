"""Screen-record the WOYM room for the video (PLAN A8).

  uv run python scripts/record.py                       # run `scripts/demo.py`, record it
  uv run python scripts/record.py -- --until confirmed  # args after -- go to demo.py
  uv run python scripts/record.py --attach --seconds 90 # record only; someone else runs the demo
  uv run python scripts/record.py --headed              # show the browser window

Output: media/raw/take-<n>.webm (VP8 at 25 fps, 1920x1080), plus
media/raw/take-<n>-frames/*.jpg (one still every 10 s, for checking).

How: the installed Google Chrome, driven over the DevTools protocol with the
`websockets` package (no Playwright needed). It logs in to Mattermost as the
viewer (default `dev`), opens the active room with reply threads shown inline,
keeps it scrolled to the bottom, and pipes Page.startScreencast frames into ffmpeg.

Lock rules (contracts §8): the recorder only READS. It writes no credentials and
connects as no agent. Without --attach it refuses to start if a demo already holds
var/run/live.lock, then starts demo.py, which takes the lock itself.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from woym import config  # noqa: E402
from woym.switch import creds  # noqa: E402

OUT_DIR = config.ROOT / "media" / "raw"
MM = config.MATTERMOST_URL.rstrip("/")
CHROME = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
FPS = 25
W, H = 1920, 1080


def log(msg: str) -> None:
    print(f"[record] {msg}", flush=True)


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0
        self.pending: dict[int, asyncio.Future] = {}
        self.handlers: dict[str, list] = {}
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                fut = self.pending.pop(m["id"])
                if "error" in m:
                    fut.set_exception(RuntimeError(f"CDP error: {m['error']}"))
                else:
                    fut.set_result(m.get("result", {}))
            elif "method" in m:
                for h in self.handlers.get(m["method"], []):
                    asyncio.create_task(h(m.get("params", {})))

    async def send(self, method: str, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        return await asyncio.wait_for(fut, 30)

    def on(self, method: str, fn) -> None:
        self.handlers.setdefault(method, []).append(fn)


def encode(captured: list[tuple[float, Path]], end: float, out: Path) -> None:
    """Exact-timing encode: each captured frame lasts until the next one arrived (concat demuxer)."""
    lst = out.with_suffix(".frames.txt")
    rows = []
    for i, (t, fp) in enumerate(captured):
        nxt = captured[i + 1][0] if i + 1 < len(captured) else end
        rows.append(f"file '{fp.as_posix()}'\nduration {max(0.001, nxt - t):.4f}\n")
    rows.append(f"file '{captured[-1][1].as_posix()}'\n")  # concat demuxer needs the last file repeated
    lst.write_text("".join(rows))
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-vf", f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,format=yuv420p", "-t", f"{end:.3f}",
                        "-c:v", "libvpx", "-b:v", "6M", "-deadline", "good", "-cpu-used", "4", str(out)],
                       capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        raise SystemExit(f"encode failed: {r.stderr[-800:]}")


def next_take(prefix: str = "take") -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"{re.escape(prefix)}-(\d+)\.webm")
    nums = [int(m.group(1)) for p in OUT_DIR.glob(f"{prefix}-*.webm") if (m := pat.match(p.name))]
    return max(nums, default=0) + 1


def newest_root(tok: str, channel_id: str) -> str | None:
    c = httpx.Client(base_url=f"{MM}/api/v4", headers={"Authorization": f"Bearer {tok}"}, timeout=10)
    posts = c.get(f"/channels/{channel_id}/posts", params={"per_page": 60}).json()
    for pid in posts.get("order", []):  # newest first
        p = posts["posts"][pid]
        if not p.get("root_id") and not p.get("type"):  # a user/agent root, not a system line
            return pid
    return None


def mm_session(viewer: str, room_key: str | None = None) -> tuple[str, str, str, str]:
    """Log the viewer in to Mattermost (session token for the browser cookie).
    Returns (token, user_id, team_name, channel_name)."""
    data = creds.load()
    cam = data.get("camera") or {}
    pw = cam["password"] if viewer == cam.get("username") else data["humans"][viewer]["mm_password"]  # setup.py
    r = httpx.post(f"{MM}/api/v4/users/login", json={"login_id": viewer, "password": pw}, timeout=15)
    r.raise_for_status()
    tok, uid = r.headers["Token"], r.json()["id"]
    c = httpx.Client(base_url=f"{MM}/api/v4", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    # show replies inline in the channel, so the whole case reads top to bottom
    c.put(f"/users/{uid}/preferences", json=[
        {"user_id": uid, "category": "display_settings", "name": "collapsed_reply_threads", "value": "off"},
        {"user_id": uid, "category": "tutorial_step", "name": uid, "value": "999"},
        {"user_id": uid, "category": "onboarding_task_list", "name": "onboarding_task_list_show", "value": "false"},
        {"user_id": uid, "category": "onboarding_task_list", "name": "onboarding_task_list_open", "value": "false"},
    ])
    room = data["rooms"][room_key] if room_key else data["room"]
    ch = c.get(f"/channels/{room['channel_id']}").json()
    team = c.get(f"/teams/{ch['team_id']}").json()["name"]
    return tok, uid, team, ch["name"]


async def launch_chrome(headed: bool, zoom: float) -> tuple[subprocess.Popen, str, str]:
    # Lay out at 1920/zoom CSS px with a device scale of `zoom`, so text is larger
    # and screencast frames still come out at a full 1920x1080 device pixels.
    prof = tempfile.mkdtemp(prefix="woym-rec-")
    args = [CHROME, f"--user-data-dir={prof}", "--remote-debugging-port=0",
            f"--window-size={int(W / zoom)},{int(H / zoom)}", f"--force-device-scale-factor={zoom}",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--hide-scrollbars", "about:blank"]
    if not headed:
        args.insert(1, "--headless=new")
    p = subprocess.Popen(args, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
    port_file = Path(prof) / "DevToolsActivePort"
    for _ in range(100):
        if port_file.exists() and port_file.read_text().strip():
            break
        await asyncio.sleep(0.1)
    port = port_file.read_text().split()[0]
    pages = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=5).json()
    page = next(x for x in pages if x.get("type") == "page")
    return p, page["webSocketDebuggerUrl"], prof


LANDING_JS = """
(() => {
  const b = [...document.querySelectorAll('a,button')].find(e => /view in browser/i.test(e.textContent));
  if (b) { b.click(); return 'clicked'; }
  return document.querySelector('#post-list, #postListContent, .post-list__dynamic') ? 'app' : 'wait';
})()
"""

# Open a root post's thread in the right-hand panel (RHS), optionally expanded.
# Element ids are Mattermost's own: CENTER_commentIcon_<postId> [live, MM in 0.31.0 stack].
OPEN_RHS_JS = """
(() => {
  const id = '%(id)s';
  const rhsHas = document.querySelector('#rhsContainer [id*="' + id + '"], .ThreadViewer [id*="' + id + '"]');
  if (!rhsHas) {
    const post = document.getElementById('post_' + id);
    if (!post) return 'root not rendered';
    post.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    const btn = document.getElementById('CENTER_commentIcon_' + id);
    if (!btn) return 'no reply button';
    btn.click();
    return 'clicked';
  }
  if (%(expand)s) {
    const ex = document.querySelector('#rhsContainer .sidebar--right__expand, button[aria-label="Expand Sidebar Icon"], button[aria-label*="xpand"]');
    const rhs = document.querySelector('#sidebar-right');
    if (ex && rhs && !rhs.classList.contains('sidebar--right--expanded')) { ex.click(); return 'expanding'; }
  }
  return 'open';
})()
"""

MEMBERS_JS = """
(() => {
  const b = document.getElementById('member_rhs');
  if (!b) return 'no members button';
  b.click();
  return 'open';
})()
"""

KEEP_BOTTOM_JS = """
(() => {
  // close banners and "jump to recents" bars that cover the conversation
  document.querySelectorAll('.announcement-bar__close, .toast__dismiss, .toast .close, [aria-label="Close announcement bar"]')
    .forEach(b => b.click());
  document.querySelectorAll('.post-list__dynamic, .ThreadViewer .post-list__dynamic, .ThreadPane .post-list__dynamic, '
    + '[data-testid="virtualizedThreadViewer"] > div, .ThreadViewer .scrollbar--view')
    .forEach(el => { el.scrollTop = el.scrollHeight; });
  return true;
})()
"""


async def record(args: argparse.Namespace) -> int:
    if not CHROME:
        log("no Chrome/Chromium found")
        return 1
    if not args.attach:
        pid = creds.live_lock_holder()
        if pid:
            log(f"a demo is already running (lock pid {pid}); use --attach to record it, or wait")
            return 1
    take = args.take or next_take(args.prefix)
    out = OUT_DIR / f"{args.prefix}-{take}.webm"
    stills = OUT_DIR / f"{args.prefix}-{take}-frames"
    stills.mkdir(parents=True, exist_ok=True)
    tok, uid, team, chan = mm_session(args.viewer, args.room)
    proc, wsurl, prof = await launch_chrome(args.headed, args.zoom)
    demo = None
    try:
        async with websockets.connect(wsurl, max_size=64 * 2**20) as ws:
            cdp = CDP(ws)
            await cdp.send("Page.enable")
            await cdp.send("Network.enable")
            # headless takes window height for (invisible) browser UI; pin the viewport
            await cdp.send("Emulation.setDeviceMetricsOverride", width=int(W / args.zoom), height=int(H / args.zoom),
                           deviceScaleFactor=args.zoom, mobile=False)
            for name, value in (("MMAUTHTOKEN", tok), ("MMUSERID", uid)):
                await cdp.send("Network.setCookie", name=name, value=value, domain="localhost", path="/")
            url = f"{MM.replace('127.0.0.1', 'localhost')}/{team}/channels/{chan}"
            await cdp.send("Page.navigate", url=url)
            for _ in range(20):  # Mattermost's "View in Desktop App / Browser" landing page
                await asyncio.sleep(1)
                r = await cdp.send("Runtime.evaluate", returnByValue=True, expression=LANDING_JS)
                if r.get("result", {}).get("value") == "app":
                    break
            await asyncio.sleep(3)
            log(f"opened {url} as {args.viewer}")

            latest = {"jpg": None, "n": 0}
            # Frames are saved with their arrival time and encoded AFTER the run, so a busy CPU can
            # never squeeze the timeline (a live encode fell to ~16 fps under load: take-epi-1).
            frames_dir = Path(tempfile.mkdtemp(prefix="woym-frames-"))
            captured: list[tuple[float, Path]] = []
            clock = {"t0": None}

            async def on_frame(p):
                latest["jpg"] = base64.b64decode(p["data"])
                latest["n"] += 1
                if clock["t0"] is not None:
                    fp = frames_dir / f"{len(captured):06d}.jpg"
                    fp.write_bytes(latest["jpg"])
                    captured.append((time.monotonic() - clock["t0"], fp))
                try:
                    await cdp.send("Page.screencastFrameAck", sessionId=p["sessionId"])
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", on_frame)
            await cdp.send("Page.startScreencast", format="jpeg", quality=85, maxWidth=W, maxHeight=H, everyNthFrame=1)
            for _ in range(50):
                if latest["jpg"]:
                    break
                await asyncio.sleep(0.1)
            log(f"recording -> {out}")

            stop = asyncio.Event()

            async def writer():
                # periodic still of the current frame, for quick checks; the video is encoded later
                next_still = 0.0
                while not stop.is_set():
                    el = time.monotonic() - clock["t0"]
                    if latest["jpg"] and el >= next_still:
                        (stills / f"{int(next_still):04d}s.jpg").write_bytes(latest["jpg"])
                        next_still += 10
                    await asyncio.sleep(0.2)

            async def keep_bottom():
                while not stop.is_set():
                    try:
                        await cdp.send("Runtime.evaluate", expression=KEEP_BOTTOM_JS)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

            base = f"{MM.replace('127.0.0.1', 'localhost')}/{team}"
            d = creds.load()
            channel_id = (d["rooms"][args.room] if args.room else d["room"])["channel_id"]
            seen_root = None if args.open_latest else newest_root(tok, channel_id)

            async def follow_thread():
                # When a new case thread (root post) appears, open it full screen and stay on it.
                nonlocal seen_root
                while not stop.is_set():
                    try:
                        root = await asyncio.to_thread(newest_root, tok, channel_id)
                    except Exception:
                        root = None
                    if root and root != seen_root:
                        # click, expand and confirm in quick steps: a short case can finish within seconds
                        for _ in range(40):
                            r = await cdp.send("Runtime.evaluate", returnByValue=True,
                                               expression=OPEN_RHS_JS % {"id": root, "expand": json.dumps(args.frame == "expanded")})
                            state = r.get("result", {}).get("value")
                            if state == "open":
                                seen_root = root
                                log(f"thread {root} open in the right panel ({args.frame}) "
                                    f"at video {time.monotonic() - clock['t0']:.1f}s")
                                break
                            await asyncio.sleep(0.25)
                        else:
                            log(f"thread {root}: still '{state}' after 10 s")
                    await asyncio.sleep(0.5)

            started = time.monotonic()
            clock["t0"] = started
            first = frames_dir / "start.jpg"
            first.write_bytes(latest["jpg"])
            captured.append((0.0, first))
            tasks = [asyncio.create_task(writer()), asyncio.create_task(keep_bottom()),
                     asyncio.create_task(follow_thread())]
            if args.attach:
                try:
                    await asyncio.sleep(args.seconds)
                except asyncio.CancelledError:
                    pass
                rc = 0
            else:
                if args.members_shot > 0:
                    # beat 3: who is in the room, with owners in the display names
                    r = await cdp.send("Runtime.evaluate", returnByValue=True, expression=MEMBERS_JS)
                    log(f"members panel: {r.get('result', {}).get('value')} ({args.members_shot:.0f}s)")
                    await asyncio.sleep(args.members_shot)
                else:
                    await asyncio.sleep(args.lead_in)
                cmd = ["uv", "run", "python", "scripts/demo.py", *args.demo_args]
                log(f"running {' '.join(cmd)}")
                # unbuffered, or every line arrives at exit with the same video stamp
                demo = await asyncio.create_subprocess_exec(*cmd, cwd=config.ROOT, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.STDOUT,
                                                            env={**os.environ, "PYTHONUNBUFFERED": "1"})
                timeline = out.with_suffix(".timeline.txt")
                demo_t0 = time.monotonic() - started
                with timeline.open("w") as tl:
                    tl.write(f"# demo.py stdout for {out.name}; [video mm:ss.s] = position in the webm\n")
                    tl.write(f"# demo.py started at video {demo_t0:.1f}s: {' '.join(cmd)}\n")
                    async for raw in demo.stdout:
                        line = raw.decode(errors="replace").rstrip()
                        v = time.monotonic() - started
                        tl.write(f"[video {int(v // 60):02d}:{v % 60:04.1f}] {line}\n")
                        tl.flush()
                        print(line, flush=True)
                rc = await demo.wait()
                log(f"timeline -> {timeline}")
                log(f"demo.py exited {rc}; recording {args.tail}s more")
                await asyncio.sleep(args.tail)
            stop.set()
            end = time.monotonic() - started
            await asyncio.gather(*tasks, return_exceptions=True)
            await cdp.send("Page.stopScreencast")
            cdp.reader.cancel()
            log(f"{end:.1f}s recorded, {len(captured)} distinct frames; encoding…")
            encode(captured, end, out)
            got = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                        str(out)], capture_output=True, text=True).stdout or 0)
            log(f"wrote {out} ({out.stat().st_size // 1024} KiB, {got:.2f}s for {end:.2f}s of wall time)")
            if abs(got - end) > 0.5:
                log(f"WARNING: video length differs from wall time by {got - end:+.2f}s")
        return rc
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True) if "frames_dir" in locals() else None
        if demo and demo.returncode is None:
            demo.terminate()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(prof, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Record the WOYM room (read-only viewer)")
    ap.add_argument("--viewer", default="camera", help="Mattermost user to watch as (default camera, made by setup)")
    ap.add_argument("--take", type=int, help="take number (default: next free)")
    ap.add_argument("--room", help="watch rooms.<KEY> from credentials (e.g. acme-nepal) instead of the active room")
    ap.add_argument("--prefix", default="take", help="output name prefix (default take → take-<n>.webm)")
    ap.add_argument("--zoom", type=float, default=1.25, help="page zoom for readability (default 1.25)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--attach", action="store_true", help="don't run demo.py; just record --seconds")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--open-latest", action="store_true", help="open the newest existing thread right away (testing)")
    ap.add_argument("--frame", choices=["panel", "expanded"], default="expanded",
                    help="thread in the right panel beside the channel, or the panel expanded over it")
    ap.add_argument("--lead-in", type=float, default=3, help="seconds of idle room before the demo starts")
    ap.add_argument("--members-shot", type=float, default=10,
                    help="seconds showing the channel members panel (owners in names) before the demo; 0 = off")
    ap.add_argument("--tail", type=float, default=5, help="seconds to keep recording after demo.py exits")
    ap.add_argument("demo_args", nargs="*", help="args for demo.py (put them after --)")
    return asyncio.run(record(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
