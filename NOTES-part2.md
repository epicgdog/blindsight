# NOTES part 2

Branch is `part_2` (the brief says `part2-app`; the requester asked for `part_2`).

## Assumptions (made without asking, per the brief)

- Part 1's server and Part 3's `tools/synthetic_feed.py` are not in this repo yet, so everything runs against `app/dev/echo_server.py`. It also serves the viewer and the 5.3 endpoints, so it is longer than the brief's 30 lines. Switching to the real server is a URL change.
- `best_ref` is treated as either a face index or a face URL by the viewer.
- Cue ids are what the phone dedupes on; the echo server repeats the location cue id so dedupe is exercised.
- `tunnel.sh` bakes the server URL into the Expo bundle via `EXPO_PUBLIC_SERVER_URL`, so the QR Metro prints opens the app with the address filled in. Metro itself is served over the LAN; pass `BLINDSIGHT_EXPO_ARGS=--tunnel` if venue Wi-Fi isolates clients.
- Backup video uses a built-in placeholder pano until Part 3's feed exists, and the echo server instead of the real pipeline. It is labelled SIMULATED in the video and in the render log.
- The viewer test and video renderer use the macOS Chrome path; override with `CHROME`.
- Expo SDK 58 packages were verified to exist and `expo export --platform ios` bundles; the app has not yet been run on a physical iPhone.

## Not yet done / human steps

- A2 (Expo Go install, scan, Allow prompts) and A3 (demo) are human steps.
- Phone capture time on a real iPhone is unmeasured. If over 250 ms, lower quality/size, then consider react-native-vision-camera with a dev build.
- Three timed rehearsals with a blindfolded volunteer are not done.

## Measured numbers

None yet. The pitch uses no numeric claims. Add real measurements here before quoting them.

## Sourced statistics (public sources, checked 2026-11-15)

Quoted on slides 3, 5, 12 and 13 and in the pitch; slide 14 lists them with links. External figures, not project measurements. Each was checked against the publisher's own page.

1. IAPB Vision Atlas, Magnitude of Global Vision Loss evidence series (2020 estimates): "43 million people are blind (crude prevalence: 0.5%)" and "295 million people have moderate to severe vision impairment (crude prevalence: 3.7%)". https://www.iapb.org/wp-content/uploads/2021/02/Vision-Atlas_Evidence-Series_Magnitude-of-VIsion-Loss_24022021.pdf
2. GBD 2019 Blindness and Vision Impairment Collaborators, "Trends in prevalence of blindness and distance and near vision impairment over 30 years: an analysis for the Global Burden of Disease Study", The Lancet Global Health, 2021: 61.0 million (52.9 to 69.3) people projected to be blind by 2050. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7820390/
3. CDC, VEHSS Modeled Estimates: Vision Loss and Blindness (2024 estimates): "7.15 million people had best corrected visual acuity loss (prevalence: 2.10%)", meaning they could not improve their vision with eyeglasses or contact lenses alone. https://www.cdc.gov/vision-health-data/prevalence-estimates/vision-loss-prevalence.html
4. National Fair Housing Alliance, 2025 Fair Housing Trends Report (published Nov 5, 2025): 17,645 of 32,321 housing discrimination complaints in 2024, 54.59%, were based on disability, the largest share. https://nationalfairhousing.org/wp-content/uploads/2025/11/2025-NFHA-Fair-Housing-Trends-Report.pdf
5. WebAIM, The WebAIM Million, 2026 report (home pages analysed February 2026): 95.9% of the top one million home pages had detected WCAG 2 failures, up from 94.8% in 2025. https://webaim.org/projects/million/
6. CoStar Group press release, "CoStar Group Completes Acquisition of Matterport", Feb 28, 2025 (SEC Form 8-K, Exhibit 99.1): "over 14 million spaces and 50 billion square feet digitized across 177 countries". https://www.sec.gov/Archives/edgar/data/1057352/000119312525042465/d899523dex991.htm
7. Project data: Matterport Model API, model P3S6VUTP3qB (66 scan points, two floors; NOTES-part3.md), and the server startup log (1,584 reference views).
8. HackPrinceton F26 Main Track and F26 Special Tracks documents, and MLH's prize email to participants (Nov 12, 2026): track descriptions and criteria on slide 12.
