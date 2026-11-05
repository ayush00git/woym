# Narration recording sheet

For recording the WOYM video narration in your own voice. Generated from `docs/drafts/video-script.md`, so if a line changes there, regenerate this sheet or copy the new line.

## Setup (5 minutes)

1. **Clear the placeholders first:** `rm media/raw/vo/beat-*.wav media/raw/vo/PLACEHOLDERS.txt`. The silent test clips are `beat-NN.wav`, and if one is still there next to your `beat-03.m4a`, the mixer stops with "Beat 3 has 2 recordings… Keep one."
2. **Record:** a quiet room, soft furnishings, no fan. A phone voice-memo app or a headset mic is fine. Leave a second of silence before and after each line; the mixer trims it.
3. **Save** each clip into `media/raw/vo/` as `beat-01` … `beat-16`, in **any format** (`.m4a`, `.mp3`, `.wav`, `.ogg`, `.opus`, `.flac`…). `beat-7.mp3` works too. One file per beat.
4. **Mix:** `uv run python scripts/video/mix_narration.py`. It converts each clip to 48 kHz mono, trims the silence, places it at its beat and normalises loudness. It writes `media/raw/final.mp4` and `final.srt`, with one line per beat:
   - `✓` fits;
   - `!` fits but is tight (fine, especially for beats marked **tight** below);
   - `✗` too long: it runs into the next beat, so re-record a little brisker (don't speed it up in software). It says how many seconds to cut.
   A beat with no recording yet just stays silent, so you can mix as you go.

**Pace:** natural and unhurried. "Max" is the limit after trimming; "Natural" is the length at 2.3 words/s.

Fallback only, if a file won't read (the mixer says "Couldn't read the recording…"): convert it yourself, then save the result as that beat's only file.

```bash
ffmpeg -y -i ~/Recordings/beat-03.m4a -ac 1 -ar 48000 media/raw/vo/beat-03.wav
```

## Lines

| # | File | Read exactly | Max | Natural |
|---|---|---|---|---|
| 1 | `beat-01` | Every developer tools team has this thread. Can't reproduce. Which Node version? Works for me. Closed. | **9.5 s** | ~7.0 s (16 words) |
| 2 | `beat-02` | One study of six bug trackers found about one in six reports gets marked non-reproducible, and those stay open months longer. | **9.5 s** | ~9.1 s (21 words) · **tight** |
| 3 | `beat-03` | One Switch room, two companies. The vendor's three agents belong to Dev. The customer's agent belongs to Meera, and she sets who can command it. | **11.5 s** | ~10.9 s (25 words) · **tight** |
| 4 | `beat-04` | Meera reports a payout that slips to Monday. The vendor's sandbox runs her repro in a throwaway container. It passes. Not reproduced. | **11.5 s** | ~9.6 s (22 words) |
| 5 | `beat-05` | Triage asks Meera's agent for five environment facts. Her agent asks her first, showing the exact values. Nothing leaves until she says yes. | **13.5 s** | ~10.0 s (23 words) |
| 6 | `beat-06` | The vendor's runner swaps in one of her values at a time. Node, language, OS, library version: all pass. Time zone fails. Asia/Kolkata, plus five-thirty. | **15.5 s** | ~10.9 s (25 words) |
| 7 | `beat-07` | Then her agent proves it on her machine. With UTC, the repro passes. With her real time zone, it fails. Confirmed on both machines. | **13.5 s** | ~10.4 s (24 words) |
| 8 | `beat-08` | What if the vendor skips the process and asks her agent directly? Switch itself refuses. Not our code. Meera's rule. | **11.5 s** | ~8.7 s (20 words) |
| 9 | `beat-09` | Dev replies, patch it. Claude Code writes a failing test and the fix in a fresh clone. Our code runs the tests and pushes. The commit hash comes from git, never from the model. | **19.5 s** | ~14.8 s (34 words) |
| 10 | `beat-10` | The vendor re-runs the matrix. Four for four. | **7.5 s** | ~3.5 s (8 words) |
| 11 | `beat-11` | And the last word goes to the customer. Her agent asks again, installs that exact commit, and re-runs her repro. Fixed on Meera's machine. | **13.5 s** | ~10.4 s (24 words) |
| 12 | `beat-12` | A team in Nepal hits the same bug. Their agent shares five values, with consent. Triage answers from Meera's verified fix. | **9.5 s** | ~9.1 s (21 words) · **tight** |
| 13 | `beat-13` | Only three things cross the room. Five consented values, results, and a commit hash. Shell access, other files, her code: never. | **13.5 s** | ~9.1 s (21 words) |
| 14 | `beat-14` | Per-owner keys. Rules Dev can't override. Targeted messages. One shared, auditable thread. | **6.5 s** | ~5.2 s (12 words) |
| 15 | `beat-15` | One command runs it all. | **2.5 s** | ~2.2 s (5 words) · **tight** |
| 16 | `beat-16` | Works on my machine. Now proven on yours. | **3.5 s** | ~3.5 s (8 words) · **tight** |

Pronunciation: "Kolkata" = *kol-KAH-tah*. Say "UTC" as letters. "Asia/Kolkata, plus five-thirty" reads as written. "Four for four" is the matrix result. The scripted chat lines in the footage (Meera's and Kiran's messages, Dev's two lines) are already captioned as scripted, so you don't need to mention them.
