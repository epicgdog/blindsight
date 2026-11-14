# Blindsight Plan

Hackathon demo: phone camera streams to laptop, laptop runs object detection and localizes the user against a Matterport 360 panorama of the space.

## Goal

Demo one scan position end to end: iPhone video in, YOLO detections out, position/view locked to a single Matterport pano.

## Assumptions

- Matterport "splat" is just a 360 panorama (skybox: 6 cube faces, 0 up, 1-4 horizon, 5 down; 2k or 4k).
- Pano locations come from the API (`panoLocations`: id, label, position). No rotation fields; 360 pano is assumed level/flat.
- Orientation forced upright; view and location are locked for the demo.
- Focus on a single pano for the demo.
- Devices: MacBook (server) and iPhone (client).

## Architecture

```
iPhone (Expo app)  --video frames-->  WebSocket  -->  MacBook server (Python)
                                                         |- YOLO inference
                                                         |- Matterport pano lookup
                                                         '- results/overlay out
```

## Steps

1. **Matterport data**
   - Read quickstart: https://beta-app.matterport.com/2026-princeton-hackathon/quickstart
   - Query model `panoLocations`; pick one pano (id, label, position).
   - Download its skybox faces (2k) and stitch/view as a panorama.
   - Lock view orientation upright, lock location to the chosen pano.
2. **Server (MacBook)**
   - Python, run with `uv run python3 server.py`; deps via `uv`.
   - WebSocket endpoint receives frames from the phone.
   - Decode frames, run YOLO, return detections (class, confidence, bbox).
3. **YOLO inference**
   - Pick the first available model (verify the API/weights actually exist before committing to it).
   - Run per frame; throttle to keep latency low.
4. **Expo app (iPhone)**
   - Camera capture, send JPEG frames over WebSocket to the MacBook IP.
   - Show returned detections as overlay.
5. **Integrate**
   - Map detections to the locked pano view for the demo.
   - Rehearse the demo flow on one pano.

## Out of scope

- Multiple panos / navigation between them.
- Rotation/heading estimation (pano assumed flat).
- Timestamps/time display (removed).

## Open questions

- Which YOLO model/API (first available; confirm it works).
- Frame rate and resolution over local network.
- Credentials for the Matterport API live in `.env.local` (git-ignored).
