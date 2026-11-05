"""Lay narration onto the rough cut: media/raw/final.mp4 + media/raw/final.srt.

  uv run python scripts/video/mix_narration.py                       # uses media/raw/vo/beat-NN.wav
  uv run python scripts/video/mix_narration.py --placeholders silent # make test WAVs first (≈ words / 2.3 s)
  uv run python scripts/video/mix_narration.py --placeholders tone   # same, as quiet tones (tests the loudness pass)

- Beats, start times and narration text come from docs/drafts/video-script.md (same parser as
  build_rough_cut.py), so a script change moves the audio with it.
- Each clip starts at its beat's start. A clip longer than (beat length - 0.5 s slack) is a WARNING;
  one longer than the beat itself would run into the next beat and is an ERROR (exit 1) unless --force.
- Loudness: two-pass EBU R128 (ffmpeg loudnorm), target -16 LUFS integrated, -1.5 dBTP, LRA 11.
  All-silent audio can't be normalised; that is reported, not faked.
- Video is copied unchanged from media/raw/rough-cut.mp4 (build it first). Audio: AAC 192k, 48 kHz.
- Subtitles: each beat's narration, split into chunks of <= 2 lines x 42 chars, timed across the
  clip's real length (or words / 2.3 s when a beat has no clip).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_rough_cut import OUT as ROUGH, ROOT, clean, parse_script  # noqa: E402

VO = ROOT / "media" / "raw" / "vo"
FINAL = ROOT / "media" / "raw" / "final.mp4"
SRT = FINAL.with_suffix(".srt")
SLACK = 0.5
WPS = 2.3
RATE = 48000
TARGET = {"I": -16.0, "TP": -1.5, "LRA": 11.0}


def log(m: str) -> None:
    print(f"[mix] {m}", flush=True)


def ffprobe_dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd[:10])}…\n{r.stderr[-1500:]}")
    return r


def beats_with_starts() -> list[dict]:
    beats, t = parse_script(), 0
    for b in beats:
        b["start"] = t
        t += b["dur"]
        b["text"] = clean(b["narration"])
    return beats


def make_placeholders(beats: list[dict], kind: str) -> None:
    VO.mkdir(parents=True, exist_ok=True)
    for b in beats:
        d = round(b["words"] / WPS, 2)
        src = "anullsrc=r=48000:cl=mono" if kind == "silent" else "sine=frequency=220:sample_rate=48000,volume=0.05"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-t", f"{d}", "-i", src,
             "-ac", "1", "-ar", str(RATE), str(VO / f"beat-{b['n']:02d}.wav")])
    (VO / "PLACEHOLDERS.txt").write_text(f"These beat-NN.wav files are {kind} PLACEHOLDERS (length = words / {WPS} s), "
                                         "not narration. Replace them with the real voice clips.\n")
    log(f"wrote {len(beats)} {kind} placeholder WAVs in {VO} (length = words / {WPS})")


def check_clips(beats: list[dict]) -> tuple[list[dict], int]:
    clips, errors = [], 0
    for b in beats:
        p = VO / f"beat-{b['n']:02d}.wav"
        if not p.exists():
            log(f"beat {b['n']:2}: no clip ({p.name}); silent beat")
            continue
        d = ffprobe_dur(p)
        room = b["dur"] - SLACK
        status = "ok"
        if d > b["dur"]:
            where = f"beat {b['n'] + 1}" if b is not beats[-1] else "past the end of the video"
            status, errors = f"ERROR: {d:.2f}s runs {d - b['dur']:.2f}s into {where}", errors + 1
        elif d > room:
            status = f"WARNING: {d:.2f}s leaves only {b['dur'] - d:.2f}s slack (< {SLACK}s)"
        log(f"beat {b['n']:2}: {d:5.2f}s of {b['dur']:2}s at {b['start']:3}s  {status}")
        clips.append({**b, "path": p, "len": d})
    return clips, errors


def build_audio(clips: list[dict], total: float, out: Path) -> dict:
    inputs, chains = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", str(c["path"])]
        ms = int(round(c["start"] * 1000))
        chains.append(f"[{i}:a]aresample={RATE},aformat=channel_layouts=mono,adelay={ms}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(clips)))
    fc = ";".join(chains) + f";{mix}amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad,atrim=0:{total:.3f}[pre]"
    pre = out.with_name("vo-placed.wav")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[pre]",
         "-ar", str(RATE), "-ac", "1", str(pre)])
    # pass 1: measure
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(pre), "-af",
                        f"loudnorm=I={TARGET['I']}:TP={TARGET['TP']}:LRA={TARGET['LRA']}:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    measured = float(m["input_i"]) if m["input_i"] not in ("-inf", "inf") else float("-inf")
    if measured < -70:
        log(f"input loudness {m['input_i']} LUFS: silent audio, nothing to normalise (placeholders?); "
            "output keeps it as-is")
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(pre), "-ac", "2", str(out)])
        return {"input_i": m["input_i"], "normalised": False}
    # pass 2: apply with the measured values (linear when possible)
    af = (f"loudnorm=I={TARGET['I']}:TP={TARGET['TP']}:LRA={TARGET['LRA']}:measured_I={m['input_i']}:"
          f"measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
          f"offset={m['target_offset']}:linear=true,aresample={RATE}")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(pre), "-af", af, "-ac", "2", str(out)])
    return {"input_i": m["input_i"], "normalised": True}


def measure(p: Path) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    i = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr)
    tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", r.stderr)
    return f"integrated {i[-1] if i else '?'} LUFS, true peak {tp[-1] if tp else '?'} dBFS"


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def chunks(text: str, width: int = 42) -> list[str]:
    """Split into subtitle cards of <= 2 lines x `width` chars, breaking at sentences first."""
    out, cur = [], ""
    for sent in re.split(r"(?<=[.?!])\s+", text.strip()):
        for word in sent.split():
            cand = f"{cur} {word}".strip()
            if len(cand) > 2 * width and cur:
                out.append(cur)
                cur = word
            else:
                cur = cand
        if len(cur) > width:  # end a card at a sentence end once it's a full line
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    # fold a short leftover ("Closed.") into the card before it when both still fit on 2 lines
    merged: list[str] = []
    for c in out:
        if merged and len(c.split()) < 4 and len(f"{merged[-1]} {c}") <= 2 * width + 8:
            merged[-1] = f"{merged[-1]} {c}"
        else:
            merged.append(c)
    return [wrap2(c, width + 4) for c in merged]


def wrap2(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    words, best = s.split(), None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) <= width and len(b) <= width:
            score = abs(len(a) - len(b))
            best = (score, a, b) if best is None or score < best[0] else best
    return f"{best[1]}\n{best[2]}" if best else s


def write_srt(beats: list[dict], clip_len: dict[int, float]) -> int:
    n, lines = 0, []
    for b in beats:
        if not b["text"]:
            continue
        span = min(clip_len.get(b["n"], b["words"] / WPS), b["dur"])
        cards = chunks(b["text"])
        total_words = sum(len(c.split()) for c in cards)
        t = b["start"]
        for c in cards:
            d = span * len(c.split()) / total_words
            n += 1
            lines += [str(n), f"{srt_time(t)} --> {srt_time(min(t + d, b['start'] + b['dur']) - 0.02)}", c, ""]
            t += d
    SRT.write_text("\n".join(lines))
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--placeholders", choices=["silent", "tone"], help="first write test WAVs of length words/2.3")
    ap.add_argument("--force", action="store_true", help="build even if a clip runs past its beat")
    a = ap.parse_args()
    if not ROUGH.exists():
        raise SystemExit(f"{ROUGH} missing: run scripts/video/build_rough_cut.py first")
    beats = beats_with_starts()
    total = ffprobe_dur(ROUGH)
    if abs(total - sum(b["dur"] for b in beats)) > 0.2:
        log(f"WARNING: rough cut is {total:.1f}s but the script sums to {sum(b['dur'] for b in beats)}s; rebuild the cut")
    if a.placeholders:
        make_placeholders(beats, a.placeholders)
    clips, errors = check_clips(beats)
    if errors and not a.force:
        log(f"{errors} clip(s) run past their beat; fix or pass --force")
        return 1
    if not clips:
        raise SystemExit(f"no clips in {VO}")
    audio = FINAL.with_name("vo-mix.wav")
    info = build_audio(clips, total, audio)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ROUGH), "-i", str(audio), "-map", "0:v",
         "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE), "-shortest", str(FINAL)])
    cards = write_srt(beats, {c["n"]: c["len"] for c in clips})
    log(f"wrote {FINAL} ({ffprobe_dur(FINAL):.1f}s): {measure(FINAL)}"
        + ("" if info["normalised"] else " [not normalised: silent input]"))
    log(f"wrote {SRT} ({cards} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
