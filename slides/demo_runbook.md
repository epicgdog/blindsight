# Demo runbook

Everything needed to present Insight: the deck, the live demo, and what to do when it breaks. The script is in slides/pitch_script.md, the hard questions in slides/qa.md.

## Roles (nobody multitasks)

| Role | Does |
|---|---|
| Speaker | Delivers slides/pitch_script.md, clicks the deck |
| Laptop driver | Runs the server and tunnel, keeps the viewer on screen, switches to the backup video if needed |
| Volunteer guide | Blindfolds the volunteer (ideally a judge), walks them to the first taught place, keeps them safe |

## The day before

1. **Deck.** The committed deck is slides/blindsight.pptx (13 slides, speaker notes on every slide; slides 8 and 12 are for questions and track judges). For the presenting copy with real images, put these files in place first (they stay out of git):
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
   - **Property mode** (default, recommended for the demo): matches against all 66 scans, uses the taught place photos and the scan disambiguation checks, and (since the voice-agent fix in d0c2542) tells the voice agent where you are. The floor-plan position and the tilt check stay empty.
   - **Single mode** (`LOCALIZATION_SCOPE=single`): also matches against every scan (CANDIDATE_PLACES=all) and adds the tilt check and the floor-plan position. Taught place photos are not used.

   Apple Silicon laptop: `LOCALIZER=real RECORD=1 make server` (add `LOCALIZATION_SCOPE=single` for single mode). Intel Mac: `RECORD=1 make -f make/part3.mk p3-server-lite` (same `LOCALIZATION_SCOPE` choice). Wait for `Server ready`.

   The voice agent needs a Gemini key (GOOGLE_API_KEY in .env.local); without one, skip that beat. Check http://localhost:8000/api/agent/health (ready, key configured, ffmpeg) before the demo, and reload the Expo app after pulling the voice-agent fix. Hold the talk button until "Listening" appears before speaking.
2. **Tunnel.** In a second window, run `cloudflared tunnel --url http://localhost:8000` and leave it open. Type the `https://….trycloudflare.com` address it prints into the phone app and tap Start. Each restart gives a new address.
3. **Viewer.** Open http://localhost:8000 on the laptop, full screen, next to the deck.
4. **Phone.** Portrait, 1x, volume up. The app plays through the silent switch.

## During the demo (slide 6)

1. Location cue at the first place, such as "View matches the front door."
2. Turn toward people or a bench: a clock-direction cue.
3. A teammate walks toward the volunteer: "getting closer".
4. Only if the voice agent is running: hold to talk, "How do I get to the entrance?"

## When something breaks

- **Phone will not connect:** check that the tunnel window is still running, then retype the address. Do not debug on stage.
- **No location cue:** keep talking over the object cues. Open http://localhost:8000/api/localization later to see why.
- **Anything else:** play slides/backup.mp4 without apologizing. It is labelled simulated; say so.

## Track judges

The pitch script ends with one optional line per track. Show slide 12 (Built for our tracks) when a track's judges ask what fits their track.

| Track | Judged on (from the F26 track documents) |
|---|---|
| Best Healthcare Hack (main) | Improves healthcare delivery, promotes wellness or tackles a public health challenge. Best Overall rubric: creativity, utility, charity, avidity |
| CoStar, Inside and Out | 30% use of the data (combine at least two of CoStar data, Matterport, Vexcel), 30% agentic depth, 25% usefulness, 15% demo and polish |
| Peri, Forward Deployed Challenge | A real business consulted this weekend: real-world need, usability (could they adopt it now), execution. The submission must name the business, the person and their role, and include one piece of evidence |
| Amazon, Best Practical AI Innovation | 25% each: creativity, usefulness, technical implementation, user experience. Must include a working prototype, a clear use case and target user, basic documentation and a pitch |
| MLH, Best Use of Gemini API | Push what is possible with the Gemini API (no rubric published in MLH's email) |

The Peri row on slide 12 appears once PERI in slides/build_deck.py is filled in with the real business, person, role, need and evidence.

On Devpost, enter Best Healthcare Hack as the required track and opt in to all four others: CoStar (Inside and Out), Amazon (Best Practical AI Innovation), Peri (Forward Deployed Challenge, with its required evidence) and MLH Best Use of Gemini API.

## Honesty rules (from the team's plan)

- No accuracy or latency numbers in the pitch. If asked, use slides/qa.md and say which numbers are simulated.
- Do not show Vexcel guidance, the door handoff or room-to-room routing as built.
- Do not claim blind-user testing.
- Say frames are not stored only if the server runs without RECORD=1.
