# Narration recording sheet

For recording the WOYM video narration in your own voice. Generated from `docs/drafts/video-script.md`, so if a line changes there, regenerate this sheet or copy the new line.

## Setup (5 minutes)

- **Room:** quiet, soft furnishings, no fan. A phone voice-memo app or a headset mic is fine.
- **Format:** 48 kHz mono WAV is ideal, but any format ffmpeg reads works (phone `.m4a`, `.mp3`, stereo, 44.1 kHz). Convert it with the line below.
- **Per clip:** leave about **1 s of silence before and after** the line (it makes trimming clean), then trim it off with the command below. The pauses must not stay in: the clip length is checked *after* trimming.
- **Pace:** natural and unhurried. Under "Natural" is the length at 2.3 words/s. You have a little room above it, up to "Max".
- **Too long?** Record it again, a bit brisker. Don't speed it up in software. Beats marked **tight** have under 1 s to spare at a natural pace. For those, the mixer's `WARNING` is acceptable; only `ERROR` (the clip runs into the next beat) means re-record.
- **Files:** save as `media/raw/vo/beat-NN.wav`, overwriting the silent placeholders that are there now, and **delete `media/raw/vo/PLACEHOLDERS.txt`** once all 16 are real.

**Convert and trim each recording** (any input format → 48 kHz mono WAV with 0.1 s of breath kept at each end). Tested on a padded stereo 44.1 kHz `.m4a`: 4.0 s → 2.2 s.

```bash
ffmpeg -y -i ~/Recordings/beat-03.m4a -af "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,areverse" -ac 1 -ar 48000 media/raw/vo/beat-03.wav
```

If a noisy room makes the trim cut into words, raise the threshold to `-35dB`.

**Mix:** `uv run python scripts/video/mix_narration.py` lays each clip at its beat's start and normalises loudness (-16 LUFS). It writes `media/raw/final.mp4` and `final.srt`. For each beat it prints `ok`, `WARNING` (less than 0.5 s of slack: fine, but tight) or `ERROR` (the clip runs into the next beat; re-record it). A missing clip just leaves that beat silent.

## Lines

| # | File | Read exactly | Max | Natural |
|---|---|---|---|---|
| 1 | `beat-01.wav` | Every developer tools team has this thread. Can't reproduce. Which Node version? Works for me. Closed. | **9.5 s** | ~7.0 s (16 words) |
| 2 | `beat-02.wav` | One study of six bug trackers found about one in six reports gets marked non-reproducible, and those stay open months longer. | **9.5 s** | ~9.1 s (21 words) · **tight** |
| 3 | `beat-03.wav` | One Switch room, two companies. The vendor's three agents belong to Dev. The customer's agent belongs to Meera, and she sets who can command it. | **11.5 s** | ~10.9 s (25 words) · **tight** |
| 4 | `beat-04.wav` | Meera reports a payout that slips to Monday. The vendor's sandbox runs her repro in a throwaway container. It passes. Not reproduced. | **11.5 s** | ~9.6 s (22 words) |
| 5 | `beat-05.wav` | Triage asks Meera's agent for five environment facts. Her agent asks her first, showing the exact values. Nothing leaves until she says yes. | **13.5 s** | ~10.0 s (23 words) |
| 6 | `beat-06.wav` | The vendor's runner swaps in one of her values at a time. Node, language, OS, library version: all pass. Time zone fails. Asia/Kolkata, plus five-thirty. | **15.5 s** | ~10.9 s (25 words) |
| 7 | `beat-07.wav` | Then her agent proves it on her machine. With UTC, the repro passes. With her real time zone, it fails. Confirmed on both machines. | **13.5 s** | ~10.4 s (24 words) |
| 8 | `beat-08.wav` | What if the vendor skips the process and asks her agent directly? Switch itself refuses. Not our code. Meera's rule. | **11.5 s** | ~8.7 s (20 words) |
| 9 | `beat-09.wav` | Dev replies, patch it. Claude Code writes a failing test and the fix in a fresh clone. Our code runs the tests and pushes. The commit hash comes from git, never from the model. | **19.5 s** | ~14.8 s (34 words) |
| 10 | `beat-10.wav` | The vendor re-runs the matrix. Four for four. | **7.5 s** | ~3.5 s (8 words) |
| 11 | `beat-11.wav` | And the last word goes to the customer. Her agent asks again, installs that exact commit, and re-runs her repro. Fixed on Meera's machine. | **13.5 s** | ~10.4 s (24 words) |
| 12 | `beat-12.wav` | A team in Nepal hits the same bug. Their agent shares five values, with consent. Triage answers from Meera's verified fix. | **9.5 s** | ~9.1 s (21 words) · **tight** |
| 13 | `beat-13.wav` | Only three things cross the room. Five consented values, results, and a commit hash. Shell access, other files, her code: never. | **13.5 s** | ~9.1 s (21 words) |
| 14 | `beat-14.wav` | Per-owner keys. Rules Dev can't override. Targeted messages. One shared, auditable thread. | **6.5 s** | ~5.2 s (12 words) |
| 15 | `beat-15.wav` | One command runs it all. | **2.5 s** | ~2.2 s (5 words) · **tight** |
| 16 | `beat-16.wav` | Works on my machine. Now proven on yours. | **3.5 s** | ~3.5 s (8 words) · **tight** |

Pronunciation: "Kolkata" = *kol-KAH-tah*. Say "UTC" as letters. "Asia/Kolkata, plus five-thirty" reads as written. "Four for four" is the matrix result. The scripted chat lines in the footage (Meera's and Kiran's messages, Dev's two lines) are already captioned as scripted, so you don't need to mention them.
