# Demo runbook

Everything needed to present Blindsight: the deck, the live demo, and what to do when it breaks. The script is in slides/pitch_script.md, the hard questions in slides/qa.md.

## Roles (nobody multitasks)

| Role | Does |
|---|---|
| Speaker | Delivers slides/pitch_script.md, clicks the deck |
| Laptop driver | Runs the server and tunnel, keeps the viewer on screen, switches to the backup video if needed |
| Volunteer guide | Blindfolds the volunteer (ideally a judge), walks them to the first taught place, keeps them safe |

## The day before

1. **Deck.** The committed deck is slides/blindsight.pptx (8 slides, speaker notes on every slide). For the presenting copy with real images, put these files in place first (they stay out of git):
   - data/rabinowitz_combined.png (3D model and 360 photo)
   - data/vexcel/vexcel_rabinowitz.jpg (aerial image)
   - data/reports/viewer.png (a screenshot of the viewer during a rehearsal, Cmd+Shift+4)

   Then build:
   ```bash
   uv run --no-project --with python-pptx --with lxml --with pillow python slides/build_deck.py --presenting
   ```
   This writes slides/blindsight-presenting.pptx (git-ignored). Present from that file.
2. **Teach the demo places.** Follow PROPERTY-LOCALIZATION.md ("Teach it places with phone photos") for every place in the walk, then check them with `make -f make/part3.mk p3-places-test DIR=...`.
3. **Backup video.** On a machine with Chrome and ffmpeg: `make part2-video` writes slides/backup.mp4, labelled SIMULATED FEED. Copy it to a second device.
4. **Rehearse three times, timed.** The pitch must stay under 3:00. Walk the exact route with a blindfolded volunteer each time.

## Starting the demo

1. **Server.** Pick the mode before the demo; they do different things today:
   - **Property mode** (default): matches against all 66 scans and uses the taught place photos and the scan disambiguation checks. The voice agent cannot tell where you are in this mode, and the floor-plan position stays empty.
   - **Single mode** (`LOCALIZATION_SCOPE=single`): also matches against every scan (CANDIDATE_PLACES=all), and feeds the voice agent, the IMU check, the place ranking and the floor-plan position. Taught place photos are not used.

   Apple Silicon laptop: `LOCALIZER=real RECORD=1 make server` (add `LOCALIZATION_SCOPE=single` for single mode). Intel Mac: `RECORD=1 make -f make/part3.mk p3-server-lite` (same `LOCALIZATION_SCOPE` choice). Wait for `Server ready`.

   The voice agent needs a Gemini key (GOOGLE_API_KEY in .env.local); without one, skip that beat.
2. **Tunnel.** In a second window, run `cloudflared tunnel --url http://localhost:8000` and leave it open. Type the `https://….trycloudflare.com` address it prints into the phone app and tap Start. Each restart gives a new address.
3. **Viewer.** Open http://localhost:8000 on the laptop, full screen, next to the deck.
4. **Phone.** Portrait, 1x, volume up. The app plays through the silent switch.

## During the demo (slide 4)

1. Location cue at the first place, such as "View matches the front door."
2. Turn toward people or a bench: a clock-direction cue.
3. A teammate walks toward the volunteer: "getting closer".
4. Only if the voice agent is running: hold to talk, "How do I get to the entrance?"

## When something breaks

- **Phone will not connect:** check that the tunnel window is still running, then retype the address. Do not debug on stage.
- **No location cue:** keep talking over the object cues. Open http://localhost:8000/api/localization later to see why.
- **Anything else:** play slides/backup.mp4 without apologizing. It is labelled simulated; say so.

## Honesty rules (from the team's plan)

- No accuracy or latency numbers in the pitch. If asked, use slides/qa.md and say which numbers are simulated.
- Do not show Vexcel guidance, the door handoff or room-to-room routing as built.
- Do not claim blind-user testing.
- Say frames are not stored only if the server runs without RECORD=1.
