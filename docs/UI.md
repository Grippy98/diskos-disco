# UI source and build

`mq_ui` is the native LVGL 9.2.2 interface. It drives the stock `mq_player` over POSIX message queues and doesn't
replace the audio engine. Target: 360×360 framebuffer, Ingenic X2000, static little-endian MIPS32r2/o32, FP64,
NaN2008.

Use the vendored LVGL and the pinned toolchain recipe in `ui/toolchain/mcm-config.mak`, not a generic mipsel
compiler or stock LVGL. Build, preview and deploy per [DEV_WORKFLOW.md](DEV_WORKFLOW.md). A clean build and ABI check
don't guarantee byte-identical output.

Components: [source map](SOURCE_MAP.md). Presentation rules: [theme contract](../themes.md). UI code is
GPL-3.0-or-later; keep the notices in `ui/licenses/` and the repo's licensing records.

## Frozen release files

The payload manifest hashes every tracked file under `ui/`, including `ui/README.md` and old dev notes, so those stay
unchanged to keep release binaries verifiable. `ui/README.md`'s version and deployment notes are historical; this
guide supersedes them. Never rewrite the recorded source hash to make a changed tree look like it built an old payload.

## Progressive lyrics development build

The `codex/lyrics-motion` branch builds **Disco! 3.4.4 lyrics-motion.1**. The checked-in `payload/mq_ui`
and its release manifest are the original 3.4.4 artifacts, not this custom UI. Build this branch's source with
the pinned compiler and use the resulting `mq_ui` and candidate manifest together. Do not use the release
packager to present the original payload as this feature build.

**Settings > Display > Text & layout > Progressive lyrics** enables smooth line movement and timed word
highlights in both Lyrics and Immersive. It is on by default. Turning it off restores static, whole-line
highlighting. The separate Screen transitions setting disables line movement without removing word highlights.
Tap a visible timed lyric line to jump to its timestamp in either mode; swipes and long presses do not seek.
Pause stays paused after a seek. In Immersive, tap outside the lyrics to reveal playback controls as before.

Normal LRC files supply line timing only. For a word sweep, use Enhanced LRC with inline word timestamps, for example:

```text
[00:01.00]<00:01.00>Follow <00:02.00>the <00:02.50>light<00:04.00>
[00:05.00]<00:05.00>Keep <00:06.00>moving<00:08.00>
```

The next explicit inline timestamp marks the end of a word or phrase. Without a terminal timestamp, the final
span highlights at its start rather than inventing its duration. Malformed timings, unsupported layout, or
capacity limits fall back to the whole line. Immersive accepts up to 24 timed spans per displayed line and drops
word timing if its shorter display text would truncate a timed span. Untimed lyrics remain readable text.
This does not add lyrics downloads from Apple Music or infer missing word timing. Existing local, embedded,
and optional online lyric sources are retained.

The device is software-rendered at 360×360. Glyph bounds are cached on line changes; per-frame work updates
positions and highlight clips. Immersive reuses cached text snapshots in its compositor. Device frame pacing
and power use still need confirmation on hardware.

Focused checks (host renderer requires native Linux dependencies from the developer workflow):

```sh
python3 ui/tests/lyrics_test.py
python3 ui/tests/lyrics_word_test.py
python3 ui/tests/imm_lyrics_compositor_test.py
make -C ui CROSS= host-render
python3 ui/tests/lyrics_motion_test.py --out /tmp/disco-lyrics-frames
make -C ui check-theme
```
