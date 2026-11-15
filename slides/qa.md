# Tough questions (answers must stay honest)

Rule: quote a number only with where it came from, and say "simulated" when it is. Real-phone accuracy and latency have not been measured (NOTES-part2.md, "Measured numbers").

## Product

- **Why not Be My Eyes or Seeing AI?** They describe what the camera sees. Blindsight knows the building from CoStar data: which scan point you are at, which way you face, what is around, and the route to the entrance, before a person ever has to help.
- **Who is it for?** Blind and low-vision people touring a property on their own: buyers, renters, or anyone visiting an unfamiliar building that already has a 3D tour.
- **Tested with blind users?** Not yet. It is our first priority, and the cues follow established clock-face direction conventions. Do not claim more.
- **Is it safe to walk with?** It is an aid next to a cane or guide dog, not a replacement. The entrance route is demo guidance on the scan graph, not verified-safe navigation (server/entrance_nav.py). Verified routes need a surveyed map (NAVIGATION.md).

## Accuracy and speed

- **How accurate is it?** On a real phone: not measured yet. In simulation, using phone-like views rendered from the real Rabinowitz Hall scans (scan 34 against its 20 nearest scans, SIFT matching):
  - 100 of 100 held-out views matched, 0 of 100 false matches (NOTES-part3.md, p3-eval; PROPERTY-VERIFICATION.md).
  - 20 of 20 walkthroughs locked within 2 seconds. 0 of 20 negative walkthroughs falsely locked in that run; one later report of the deployed server had 1 false lock in 20 (SPECIFIC-CUES.md). Say both.
  - Heading error median about 0.05 degrees on renders. Renders are far easier than a real phone (lens, lighting, people), so do not present this as real-world accuracy.
  - Whole-building mode (all 66 scans) is tested on a procedural room only (PROPERTY-LOCALIZATION.md).
- **What does the geometry add?** On the same simulated split, retrieval alone made 1 false match in 100 frames; with geometric verification, 0 in 100 (NOTES-part3.md, ablation).
- **How fast?** On the server, not end to end: real YOLO11n on a Mac returned 360 of 360 frames at 6 fps, server time p50 about 40 ms (NOTES-part1.md, public test image). Localization ran at 8.5 Hz on the Intel Mac (NOTES-part3.md). Phone capture to speech is unmeasured.

## Data and privacy

- **Privacy?** Camera frames are processed on our own laptop. Say "not stored" only if the server runs without RECORD=1 (with it, frames are saved locally in data/sessions/). Holding the talk button sends that voice clip to Google's Gemini API, and "describe the view" also sends the current camera frame. Say this if asked.
- **Whose data is it?** The 3D scans come from Matterport through CoStar's hackathon API access; Vexcel aerial imagery came through the same track. The committed deck carries no imagery; the presenting copy credits both.
- **What if furniture moved since the scan?** Not measured. A match needs many points across the view to agree geometrically, so small changes should matter less than large ones, and a changed place can be taught again with a few phone photos (PROPERTY-LOCALIZATION.md).

## Statistics and tracks

- **Where do your statistics come from?** Slide 14 lists every source with its link; NOTES-part2.md has the exact quotes and dates: IAPB Vision Atlas (43M blind, 295M moderate to severe, 61M by 2050), CDC VEHSS (7M+ Americans), National Fair Housing Alliance 2025 (54.6% of complaints about disability), WebAIM Million 2026 (95.9% of home pages fail automated checks), CoStar's acquisition release (14M+ Matterport spaces).
- **Healthcare: is this a medical device?** No. It is an independence and mobility aid, used next to a cane or guide dog. We make no clinical claims and have not run a clinical study.
- **Gemini: what does Gemini actually do?** Gemini 2.5 Flash receives the spoken question as audio and calls six tools (location, surroundings, start and stop the entrance route, route status, describe the view). Its instructions say to answer from those tools and never guess. Tested with a fake client in our test suite, and once end to end on the server: a recorded "Where am I?" was transcribed and answered in about five seconds (VOICE-AGENT-FIX.md). The iPhone microphone path has not been verified yet; say so if asked about live reliability.
- **CoStar: which of the three layers do you use?** (Their criteria: 30% use of the data, combining at least two of CoStar building data, Matterport and Vexcel.) Matterport is the core: all 66 scans of Rabinowitz Hall are the map. We pulled Vexcel aerial imagery of the building, but street-to-door guidance from it is roadmap. CoStar building data is not used yet. Say this plainly.
- **CoStar: what makes it agentic?** (30% agentic depth.) The Gemini agent decides which tool to call, plans a route to the entrance over the scan points and then guides along it, turn by turn. The cue engine also decides on its own what to say and when.
- **Amazon: is it agentic and adaptive?** (25% each: creativity, usefulness, technical implementation, user experience.) It reasons and acts: the voice agent picks tools and plans the entrance route; the cue engine decides what to say and when. It adapts to a building from a few labelled phone photos, and keeps a minute of scene memory. It has not yet learned from user feedback; say so if asked.
- **Peri: who did you build this for?** Only answer with the business you actually consulted this weekend, the person and their role, and the evidence you submitted. Do not stretch it.
- **CoStar: why does this need CoStar?** The map is the scan library. Without existing 3D scans there is nothing to match against, and every building would first have to be mapped by someone.

## Business and tech

- **How does this make CoStar money?** A differentiating feature on its listing platforms that opens an underserved audience, built on data competitors do not have.
- **What is technically new?** Using an existing library of 3D scans as the localization map for assistive navigation, verified with geometry, instead of building new maps.
- **What is built versus roadmap?** Built: phone app, object cues, matching against all 66 scans, the voice agent demo, the judges' viewer. Simulated only: position on the floor plan. Roadmap: Vexcel street-to-door guidance, the door handoff, and verified room-to-room routes.
