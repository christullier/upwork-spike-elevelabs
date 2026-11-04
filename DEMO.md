# 30-second screen recording script

**Setup before recording:**
- Run `uv run python -m app`.
- Open http://localhost:8000 at 100% zoom.
- Have a chapter on your clipboard. `samples/chapter.txt` works; copy it from below the `----` line.

For a short clip, use the **Load 30-second excerpt** button instead. It sets 300-character chunks, so even ~30 seconds of audio has one seam. Turn your system audio capture on.

| Time | On screen | Say (voice-over) |
|---|---|---|
| 0:00–0:05 | Paste the chapter into the text box. The word, character and cost counters update. | "Paste a full chapter. You see the exact character cost before spending anything." |
| 0:05–0:09 | Open the voice list and pick a narrator. Hover over *Clone a voice* for a moment. | "Pick any voice on the account, or clone one with the speaker's permission." |
| 0:09–0:12 | Point at the pronunciation table (*Irene → Eye-ree-nee*). Tick **Level to ACX specs**. | "Fix names once. Level for Audible upload." |
| 0:12–0:18 | Click **Generate**. The progress card shows *Chunk 1 of 2 → Chunk 2 of 2 → Joining*. | "Chunks are generated in order, each one conditioned on the previous one's request ID." |
| 0:18–0:27 | Click **▶ Seam 1 → 2**. Let about 6 seconds play across the join. | "This is the join between chunk one and chunk two. Same voice, same pace, no jump." |
| 0:27–0:30 | Scroll to the loudness table (green *Pass*) and the chunks table with request IDs. | "Measured loudness passes ACX. One file, ready to upload." |

**Tips:**
- Seam timing: the seam is ~4 seconds into the playback that the button starts, so the join lands mid-clip.
- Recording with a full chapter: generation takes a few minutes. Record the click, cut, then resume the recording at "Done".
- Hosted version: the replay demo at `demo.hueman.dev` (see README, *Free hosting*) follows the same flow without any waiting.
