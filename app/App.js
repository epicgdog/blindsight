import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Animated, Button, PanResponder, Platform, Pressable, ScrollView, StatusBar, StyleSheet, Switch, Text, TextInput, View, useWindowDimensions } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { RecordingPresets, createAudioPlayer, requestRecordingPermissionsAsync, setAudioModeAsync, useAudioRecorder } from "expo-audio";
import * as ScreenOrientation from "expo-screen-orientation";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { Accelerometer, Magnetometer, Pedometer } from "expo-sensors";
import { cameraHeading } from "./src/compass";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "./src/client";
import { createCueSpeaker } from "./src/speech";
import { runCaptureLoop } from "./src/loop";
import { encodeFrame } from "./src/protocol";
import { imuMessage } from "./src/imu";
import { normalizeServerUrl } from "./src/address";
import { base64ToBytes } from "./src/base64";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import { AUDIO_MODE } from "./src/audio";
import { createVoicePlayer, createVoiceDelivery } from "./src/voice";
import { makeLog } from "./src/log";

const logApp = makeLog("app"), logWs = makeLog("ws"), logTalk = makeLog("talk"), logMic = makeLog("mic"),
  logCam = makeLog("cam"), logVoice = makeLog("voice"), logSpeech = makeLog("speech"), logLoc = makeLog("loc");
import { attitudeFromGravity, createMotionDetector, smoothGravity } from "./src/attitude";
import { ackBody, createAckTracker, sendAck, speechAckUrl } from "./src/ack";
import { META_EVERY, PICTURE_OPTIONS, PICTURE_SIZE, SAVE_OPTIONS, cameraMetaMessage, resizePlan } from "./src/camera";
import { File } from "expo-file-system";
import { RECORD_MODE, agentQuietUrl, agentTalkUrl, postClip, createHoldToTalk } from "./src/talk";
import NativeNavigation, { trackingModule } from "./NativeNavigation";
import FlatMap from "./src/FlatMap";
import { BugIcon, CompassIcon, MicIcon, SpeakerIcon, TurnIcon } from "./src/Icons";
import { accuracyText, banner, bottomBar, boxStyle, frameMatch, framePosition, headingText, mapMode, metricsUrl,
  navUrl, panoUrl, rankRows, whyLines, destUrl } from "./src/map";

const KEY = "blindsight.server";
const DEBUG_KEY = "blindsight.debugOpen";
const DEFAULT = process.env.EXPO_PUBLIC_SERVER_URL ?? "";

function LegacyApp() {
  const [perm, askPerm] = useCameraPermissions();
  const [address, setAddress] = useState(DEFAULT);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState({ connected: false, state: null });
  const [fps, setFps] = useState(0);
  const camera = useRef(null);
  const heading = useRef(null);
  const runningRef = useRef(false);
  const client = useRef(null);
  const talking = useRef(false); // hold-to-talk: cues stay quiet while the user speaks
  const [talkState, setTalkState] = useState("idle"); // idle | listening | thinking
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderRef = useRef(recorder); recorderRef.current = recorder;
  const addressRef = useRef(address); addressRef.current = address;
  const talkController = useRef(null);
  const frames = useRef([]);
  const sizeLogged = useRef(false);
  const shots = useRef(0);
  const availableSizes = useRef(null);
  const ackUrl = useRef(null);
  const attitude = useRef(null);
  const gravityRef = useRef(null); // smoothed gravity, shared with the magnetometer compass
  const [pitch, setPitch] = useState(null);
  const [moving, setMoving] = useState(null);
  const ackTracker = useRef(createAckTracker());
  const imuTimer = useRef(null);
  // Navigation screen: latest frame result, the static floor map, and the overlay controls.
  const [result, setResult] = useState(null);
  const [mapData, setMapData] = useState(null); // { map, places } from /api/pano
  const [debugOpen, setDebugOpen] = useState(false);
  const [metrics, setMetrics] = useState(null);
  const [muted, setMuted] = useState(false);
  const [debugDots, setDebugDots] = useState(false); // matched place and candidates on the map
  const [mapPref, setMapPref] = useState("follow"); // follow (heading-up) | north

  useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem(KEY);
      if (saved && !DEFAULT) setAddress(saved);
      await setAudioModeAsync(AUDIO_MODE).catch((e) => logApp("audio mode failed", { error: e?.message }));
      await ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
      // No location permission prompt: the compass heading stays null and the server uses the visual heading.
    })().catch((e) => logApp("init failed", { error: e?.message }));
  }, []);

  // Camera pitch and roll from gravity, sent with every frame so the server can check matches.
  useEffect(() => {
    let sub = null, gravity = null, last = 0, cancelled = false;
    const motion = createMotionDetector();
    (async () => {
      if (!(await Accelerometer.isAvailableAsync()) || cancelled) return;
      Accelerometer.setUpdateInterval(100);
      sub = Accelerometer.addListener((g) => {
        const now = Date.now();
        gravity = smoothGravity(gravity, g);
        gravityRef.current = gravity;
        const m = motion.push(g, now);
        const att = attitudeFromGravity(gravity);
        attitude.current = att && typeof m === "boolean" ? { ...att, moving: m } : att;
        if (now - last > 500 && att) { last = now; setPitch(att.pitch_deg); setMoving(m); }
      });
    })().catch((e) => logApp("accelerometer failed", { error: e?.message }));
    return () => { cancelled = true; sub?.remove(); };
  }, []);

  // Compass without a location prompt: tilt-compensated magnetometer heading of the camera's view
  // direction (magnetic north). The server calibrates it to the map frame, as it did the old compass.
  useEffect(() => {
    let sub = null, cancelled = false;
    (async () => {
      if (!(await Magnetometer.isAvailableAsync()) || cancelled) return;
      Magnetometer.setUpdateInterval(100);
      sub = Magnetometer.addListener((mag) => {
        const h = cameraHeading(gravityRef.current, mag);
        if (h != null) heading.current = h;
      });
    })().catch((e) => logApp("magnetometer failed", { error: e?.message }));
    return () => { cancelled = true; sub?.remove(); };
  }, []);

  // Step count from the phone's pedometer, sent with each frame so the server can measure the
  // distance walked between position fixes. Stays null when the pedometer is not available.
  const stepCount = useRef(null);
  useEffect(() => {
    let sub = null, cancelled = false;
    (async () => {
      if (!(await Pedometer.isAvailableAsync()) || cancelled) return;
      sub = Pedometer.watchStepCount(({ steps }) => { stepCount.current = steps; });
    })().catch((e) => logApp("pedometer failed", { error: e?.message }));
    return () => { cancelled = true; sub?.remove(); };
  }, []);

  // All speech is Gemini TTS audio from the server (/api/voice), played with expo-audio.
  const wsUrl = useRef(null);
  wsUrl.current = normalizeServerUrl(address);
  const voice = useRef(null);
  if (!voice.current) {
    voice.current = createVoicePlayer({
      createPlayer: () => createAudioPlayer(null),
      getWsUrl: () => wsUrl.current,
      log: logVoice,
    });
  }

  const speaker = useRef(
    createCueSpeaker(
      {
        speak: (t, meta) => {
          voice.current.speak(t, {
            onStart: () => {
              // Tell the server speech actually started, so it can measure capture-to-audible latency.
              if (meta?.startup) sendAck(fetch, ackUrl.current, ackBody(null, null));
              else if (meta?.id) sendAck(fetch, ackUrl.current, ackBody(meta.id, ackTracker.current.captureToAudibleMs(meta.id, Date.now())));
            },
          });
        },
        stop: () => voice.current.stop(),
        isSpeaking: () => voice.current.isSpeaking() || talking.current,
      },
      { log: (m) => logSpeech(m) },
    ),
  ).current;

  const delivery = useRef(null);
  if (!delivery.current) delivery.current = createVoiceDelivery(
    (path, options) => voice.current.playPath(path, options), text => speaker.announce(text));

  const start = useCallback(async () => {
    const url = normalizeServerUrl(address);
    if (!url) return;
    await AsyncStorage.setItem(KEY, address);
    if (!perm?.granted) await askPerm();
    await activateKeepAwakeAsync();
    await setAudioModeAsync(AUDIO_MODE).catch((e) => logApp("audio mode failed", { error: e?.message }));
    logApp("start", { server: url });
    await speaker.startupCheck();
    ackUrl.current = speechAckUrl(url);
    let wasConnected = null, lastLoc = null, results = 0;
    client.current = createClient({
      url, speaker,
      onStatus: (st) => {
        if (st.connected !== wasConnected) { logWs(st.connected ? "connected" : "disconnected", { url }); wasConnected = st.connected; }
        setStatus(st);
      },
      onResult: (r) => {
        if (r.type === "voice") {
          // The Live agent's voice, streamed as short WAV segments over this socket.
          logWs("voice segment", { turn: r.turn, seq: r.seq, first: r.first, last: r.last, interrupt: r.interrupt });
          delivery.current.stream(r);
          return;
        }
        results += 1;
        setResult(r);
        if (r.cue) ackTracker.current.noteCue(r.cue.id, r.seq);
        const loc = r.location ? `${r.location.state} ${r.location.label ?? r.location.pano_id ?? ""}` : null;
        const agent = r.agent?.decision ? `${r.agent.decision.state} ${r.agent.place_label ?? ""}` : null;
        const key = `${loc}|${agent}|${r.agent?.nav?.active ?? false}`;
        if (key !== lastLoc) {
          logLoc("changed", { localizer: loc, decided: agent, conf: r.agent?.decision?.conf, nav: r.agent?.nav ?? null });
          lastLoc = key;
        }
        if (results % 20 === 0) logWs("frames", { results, server_ms: Math.round(r.server_ms?.total ?? 0), detections: r.detections?.length ?? 0 });
      },
    });
    client.current.start();
    runningRef.current = true; setRunning(true);
    // Phone sensors at 10 Hz between frames: the server fuses the compass and steps and re-checks guidance.
    imuTimer.current = setInterval(() => {
      try {
        client.current?.sendJson(imuMessage({ now: Date.now(), heading: heading.current, attitude: attitude.current, steps: stepCount.current }));
      } catch {}
    }, 100);
    runCaptureLoop({
      isRunning: () => runningRef.current,
      capture: async () => {
        const p = await camera.current.takePictureAsync(PICTURE_OPTIONS);
        // The frame's sensor stamp: time, compass and attitude at the shutter, before the slow resize.
        const sensors = { ts_ms: Date.now(), heading_deg: heading.current ?? null, attitude: attitude.current };
        // Resize to 720 px on the short side on the phone, then read the JPEG bytes back.
        const ctx = ImageManipulator.manipulate(p.uri);
        const plan = resizePlan(p.width, p.height);
        if (plan) ctx.resize(plan);
        const out = await (await ctx.renderAsync()).saveAsync({ ...SAVE_OPTIONS, format: SaveFormat.JPEG });
        if (!sizeLogged.current) {
          sizeLogged.current = true;
          logCam("first frame", { camera: `${p.width}x${p.height}`, sending: `${out.width}x${out.height}` });
          try { availableSizes.current = await camera.current.getAvailablePictureSizesAsync(); } catch {}
        }
        // Camera metadata (EXIF + sizes) on the first frame and every META_EVERY frames.
        if (shots.current++ % META_EVERY === 0) {
          client.current?.sendJson(cameraMetaMessage({
            seq: shots.current, photo: p, sent: out, exif: p.exif, sensors,
            pictureSize: PICTURE_SIZE, availableSizes: availableSizes.current,
          }));
        }
        return { bytes: base64ToBytes(out.base64), w: out.width, h: out.height, sensors };
      },
      send: async (bytes, header) => {
        await client.current.sendFrame(encodeFrame(header, bytes));
        frames.current = [...frames.current, Date.now()].slice(-20);
        const f = frames.current;
        if (f.length > 1) setFps(((f.length - 1) * 1000) / (f[f.length - 1] - f[0]));
      },
      getHeading: () => heading.current,
      onFrame: ({ seq, captureStartMs }) => ackTracker.current.noteFrame(seq, captureStartMs),
      getAttitude: () => attitude.current,
      getSteps: () => stepCount.current,
      onRate: (r) => logCam("frame rate", r),
    });
  }, [address, perm, askPerm, speaker]);

  const talkIn = useCallback(async () => {
    const quietOn = (on) => {
      const quiet = agentQuietUrl(normalizeServerUrl(addressRef.current), on);
      if (quiet) fetch(quiet, { method: "POST" }).catch(() => {});
    };
    if (!talkController.current) talkController.current = createHoldToTalk({
      prepare: async () => {
        const t0 = Date.now();
        logTalk("button down");
        voice.current.stop();
        quietOn(true);
        const p = await requestRecordingPermissionsAsync();
        if (!p.granted) throw Error("microphone permission denied");
        await setAudioModeAsync(RECORD_MODE); await recorderRef.current.prepareToRecordAsync();
        logMic("prepared", { ms: Date.now() - t0 });
      },
      record: () => { recorderRef.current.record(); logMic("recording"); },
      finish: async () => {
        try { await recorderRef.current.stop(); } catch (e) { logMic("stop failed", { error: e?.message }); }
        const uri = recorderRef.current.uri;
        logTalk("button up", { file: uri ? uri.split("/").pop() : null });
        return uri;
      },
      restore: () => setAudioModeAsync(AUDIO_MODE),
      send: async (uri) => {
        const url = agentTalkUrl(normalizeServerUrl(addressRef.current));
        if (!url) throw Error("Server URL missing");
        logTalk("uploading", { url });
        const t0 = Date.now();
        const out = await postClip(fetch, url, uri, { readBytes: (u) => new File(u).bytes() });
        logTalk("answer", { ms: Date.now() - t0, heard: out.heard, reply: out.reply, tools: out.tools, error: out.error, status: out.status });
        return out;
      },
      // While streaming, the reply already arrives as voice segments over the socket; otherwise play the clip.
      onResult: (out) => {
        delivery.current.reply(out);
      },
      announce: text => speaker.announce(text), state: setTalkState,
      busy: value => { talking.current = value; if (!value) quietOn(false); }, // a clip too short to send must not leave the agent muted
    });
    talkController.current.begin();
  }, [speaker]);

  const talkOut = useCallback(async () => {
    await talkController.current?.end();
  }, []);

  const stop = useCallback(() => {
    logApp("stop");
    runningRef.current = false; setRunning(false);
    clearInterval(imuTimer.current); imuTimer.current = null;
    client.current?.stop();
    deactivateKeepAwake();
  }, []);

  // ---- navigation screen ----
  // Debug sheet open/closed is remembered; with no saved server address it opens by itself.
  const debugLoaded = useRef(false);
  useEffect(() => {
    (async () => {
      const [open, saved] = await Promise.all([AsyncStorage.getItem(DEBUG_KEY), AsyncStorage.getItem(KEY)]);
      if (!saved && !DEFAULT) setDebugOpen(true);
      else if (open != null) setDebugOpen(open === "1");
    })().catch((e) => logApp("debug state load failed", { error: e?.message })).finally(() => { debugLoaded.current = true; });
  }, []);
  useEffect(() => {
    if (debugLoaded.current) AsyncStorage.setItem(DEBUG_KEY, debugOpen ? "1" : "0").catch(() => {});
  }, [debugOpen]);
  const toggleDebug = useCallback(() => setDebugOpen((o) => !o), []);

  // Floor map (walls, scan points) once per server, as soon as the socket is up.
  useEffect(() => { setMapData(null); }, [address]);
  useEffect(() => {
    if (!status.connected || mapData) return;
    const url = panoUrl(normalizeServerUrl(addressRef.current));
    if (!url) return;
    let cancelled = false;
    fetch(url).then((r) => r.json())
      .then((p) => { if (!cancelled) setMapData({ map: p.map ?? null, places: p.places ?? {} }); })
      .catch((e) => logApp("map fetch failed", { error: e?.message }));
    return () => { cancelled = true; };
  }, [status.connected, mapData]);

  // Server latency percentiles, only while someone is looking at them.
  useEffect(() => {
    if (!debugOpen || !running) return;
    const url = metricsUrl(normalizeServerUrl(addressRef.current));
    if (!url) return;
    const poll = () => fetch(url).then((r) => r.json()).then(setMetrics).catch(() => {});
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, [debugOpen, running]);

  useEffect(() => { voice.current.setMuted(muted); }, [muted]);

  const nav = result?.agent?.nav ?? null;
  const navActive = !!nav?.active;
  // Exit / Start: guidance off or on at the server; shown at once, the next frame carries the truth.
  // Dropped pin: long-press the map -> preview card (where it lands, route length) -> Start or Cancel.
  const [pinDraft, setPinDraft] = useState(null); // { x, y, moved, length_m, pts } from the preview
  const [pinMode, setPinMode] = useState(false); // "Add pin" pressed: the next map tap drops the pin
  const onTapMap = useCallback(async (m) => {
    setPinMode(false);
    const url = destUrl(normalizeServerUrl(addressRef.current), m.x, m.y);
    if (!url) return;
    setPinDraft({ x: m.x, y: m.y, loading: true });
    try {
      const p = await (await fetch(url)).json();
      logApp("pin preview", p);
      setPinDraft(p?.ok ? p : { x: m.x, y: m.y, error: p?.reason ?? "can't route there" });
    } catch (e) { setPinDraft({ x: m.x, y: m.y, error: e?.message ?? "preview failed" }); }
  }, []);
  const postDest = useCallback(async (body) => {
    const url = destUrl(normalizeServerUrl(addressRef.current));
    if (!url) return null;
    try {
      const r = await (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
      logApp("destination", r);
      return r;
    } catch (e) { logApp("destination failed", { error: e?.message }); return null; }
  }, []);
  const startPin = useCallback(async () => {
    const d = pinDraft;
    setPinDraft(null);
    if (d && Number.isFinite(d.x)) await postDest({ x: d.x, y: d.y });
  }, [pinDraft, postDest]);
  const clearPin = useCallback(() => postDest({ entrance: true }), [postDest]);

  const toggleNav = useCallback(async () => {
    const on = !navActive;
    const url = navUrl(normalizeServerUrl(addressRef.current), on);
    if (!url) return;
    logApp(on ? "guidance on" : "guidance off");
    setResult((r) => (r ? { ...r, agent: { ...r.agent, nav: { active: on } } } : r));
    try {
      const res = await fetch(url, { method: "POST" });
      logApp("guidance toggled", { on, status: res.status });
    } catch (e) { logApp("guidance toggle failed", { error: e?.message }); }
  }, [navActive]);

  const { height: winH, width: winW } = useWindowDimensions(); // the root got 0 height from its parent
  const sheetH = Math.round(winH * 0.5); // half: the map stays visible above it
  const sheet = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.timing(sheet, { toValue: debugOpen ? 1 : 0, duration: 250, useNativeDriver: true }).start();
  }, [debugOpen, sheet]);
  // Closed is slid off-screen, not unmounted: capture needs the CameraView inside it.
  const sheetY = sheet.interpolate({ inputRange: [0, 1], outputRange: [sheetH + 40, 0] });
  // Swipe down on the top bar of the sheet: it follows the finger, and closes past 80 px or on a fast flick.
  const drag = useRef(new Animated.Value(0)).current;
  const pan = useRef(PanResponder.create({
    onMoveShouldSetPanResponder: (_, g) => g.dy > 6 && Math.abs(g.dy) > Math.abs(g.dx),
    onPanResponderMove: (_, g) => drag.setValue(Math.max(0, g.dy)),
    onPanResponderRelease: (_, g) => {
      if (g.dy > 80 || g.vy > 0.8) setDebugOpen(false);
      Animated.timing(drag, { toValue: 0, duration: 200, useNativeDriver: true }).start();
    },
    onPanResponderTerminate: () => drag.setValue(0),
  })).current;

  const facing = result?.agent?.heading ?? null;
  const position = framePosition(result);
  const path = result?.agent?.path ?? null;
  const b = running ? banner({ connected: status.connected, nav, position })
    : { title: "Not started", meters: "", arrow: null, tone: "off" };
  const bar = bottomBar(nav);
  const mode = mapMode(mapPref, position, facing?.heading_deg);
  const match = frameMatch(result);
  const loc = result?.location ?? null;
  const decision = result?.agent?.decision ?? null;
  const ranks = rankRows(match, loc?.pano_id, mapData?.places ?? {});
  const why = whyLines(result?.localization_debug);
  const accuracy = accuracyText(position);
  const busyTalk = talkState === "preparing" || talkState === "thinking";
  const talkNote = { preparing: "Preparing microphone…", listening: "Listening…", thinking: "Thinking…" }[talkState];
  const num = (v, d = 0) => (Number.isFinite(v) ? v.toFixed(d) : "–");

  return (
    <View style={[s.root, { height: winH, width: winW }]}>
      <StatusBar barStyle="dark-content" />
      <FlatMap map={mapData?.map} path={path} position={position} headingDeg={facing?.heading_deg} ranking={frameMatch(result)?.ranking} activeId={result?.agent?.decision?.place} debugDots={debugDots}
        insetTop={TOP + 120} insetBottom={debugOpen ? sheetH + 8 : BOTTOM + BAR_H + 8}
        preview={pinDraft?.ok || pinDraft?.loading ? pinDraft : null} onTapMap={pinMode && status.connected ? onTapMap : null} />

      <View style={s.top} pointerEvents="box-none">
        <View style={[s.banner, b.tone === "go" || b.tone === "done" ? s.bannerGo : s.bannerWait]}>
          <View style={s.bannerArrow}>
            {b.arrow ? <TurnIcon arrow={b.arrow} size={44} /> : b.tone === "wait" ? <ActivityIndicator color="#fff" /> : null}
          </View>
          <View style={s.bannerText} accessible accessibilityLabel={[b.title, b.meters].filter(Boolean).join(", ")}>
            <Text style={s.bannerTitle} numberOfLines={2}>{b.title}</Text>
            {b.meters ? <Text style={s.bannerMeters}>{b.meters}</Text> : null}
          </View>
          <Pressable style={[s.mic, talkState === "listening" && s.micOn]} onPressIn={talkIn} onPressOut={talkOut}
            hitSlop={10} accessibilityRole="button" accessibilityLabel="Hold to talk" accessibilityState={{ busy: busyTalk }}>
            {busyTalk ? <ActivityIndicator color="#0d652d" /> : <MicIcon color={talkState === "listening" ? "#fff" : "#0d652d"} />}
          </Pressable>
        </View>
        {talkNote ? <Text style={[s.talkNote, talkState === "listening" && s.talkNoteOn]}>{talkNote}</Text> : null}
      </View>

      <View style={s.side} pointerEvents="box-none">
        <Pressable style={s.round} hitSlop={8} onPress={() => setMapPref((p) => (p === "follow" ? "north" : "follow"))}
          accessibilityRole="button" accessibilityLabel={mapPref === "follow" ? "Show the whole floor, north up" : "Follow my heading"}>
          <CompassIcon follow={mapPref === "follow"} />
        </Pressable>
        <Pressable style={[s.round, muted && s.roundOn]} hitSlop={8} onPress={() => setMuted((m) => !m)}
          accessibilityRole="button" accessibilityLabel={muted ? "Unmute voice" : "Mute voice"} accessibilityState={{ selected: muted }}>
          <SpeakerIcon muted={muted} />
        </Pressable>
        <Pressable style={[s.round, debugOpen && s.roundOn]} hitSlop={8} onPress={toggleDebug}
          accessibilityRole="button" accessibilityLabel={debugOpen ? "Close debug panel" : "Open debug panel"}>
          <BugIcon />
        </Pressable>
      </View>
      {accuracy ? <View style={s.chip}><Text style={s.chipText}>{accuracy}</Text></View> : null}

      {pinMode ? (
        <View style={s.pinCard} pointerEvents="none">
          <Text style={s.pinTitle}>Tap the map where you want to go</Text>
        </View>
      ) : pinDraft ? (
        <View style={s.pinCard}>
          <Text style={s.pinTitle}>
            {pinDraft.loading ? "Finding a route…" : pinDraft.error ? `Can't go there: ${pinDraft.error}`
              : `Dropped pin${pinDraft.length_m != null ? ` · ${Math.round(pinDraft.length_m)} m` : ""}`}
          </Text>
          {pinDraft.moved ? <Text style={s.pinNote}>Moved to the nearest reachable spot</Text> : null}
          <View style={s.pinRow}>
            <Pressable style={s.pinCancel} onPress={() => setPinDraft(null)} accessibilityRole="button">
              <Text style={s.pinCancelText}>Cancel</Text>
            </Pressable>
            {pinDraft.ok ? (
              <Pressable style={s.pinStart} onPress={startPin} accessibilityRole="button" accessibilityLabel="Start guidance to the pin">
                <Text style={s.pinStartText}>Start</Text>
              </Pressable>
            ) : null}
          </View>
        </View>
      ) : path?.goal_kind === "pin" ? (
        <Pressable style={s.pinChip} onPress={clearPin} accessibilityRole="button" accessibilityLabel="Clear pin, go back to the entrance">
          <Text style={s.pinChipText}>📍 {path.goal_label ?? "your pin"} · Clear</Text>
        </Pressable>
      ) : null}

      <View style={s.bar}>
        <View style={s.barText} accessible>
          {bar.time ? (
            <Text style={s.barLine}><Text style={s.barTime}>{bar.time}</Text><Text style={s.barLeft}>{"  ·  "}{bar.left}</Text></Text>
          ) : (
            <Text style={s.barIdle}>{nav?.arrived ? "Arrived" : !running ? "Not started" : !status.connected ? "Offline" : navActive ? "Finding the route…" : "No destination"}</Text>
          )}
          {result?.agent?.place_label && decision?.state !== "unknown" ? <Text style={s.barPlace} numberOfLines={1}>{result.agent.place_label}</Text> : null}
        </View>
        {navActive ? (
          <Pressable style={[s.exit, !status.connected && s.disabled]} onPress={toggleNav} disabled={!status.connected}
            hitSlop={8} accessibilityRole="button" accessibilityLabel="Exit guidance">
            <Text style={s.exitText}>Exit</Text>
          </Pressable>
        ) : (
          <Pressable style={[s.exit, s.startNav, pinMode && s.pinModeOn, !status.connected && s.disabled]} disabled={!status.connected}
            onPress={() => { setPinDraft(null); setPinMode((m) => !m); }} hitSlop={8} accessibilityRole="button"
            accessibilityLabel={pinMode ? "Cancel adding a pin" : "Add pin"}>
            <Text style={s.exitText}>{pinMode ? "Cancel" : "Add pin"}</Text>
          </Pressable>
        )}
      </View>

      <Animated.View style={[s.sheet, { height: sheetH, transform: [{ translateY: Animated.add(sheetY, drag) }] }]} pointerEvents={debugOpen ? "auto" : "none"}
        accessibilityElementsHidden={!debugOpen} importantForAccessibility={debugOpen ? "auto" : "no-hide-descendants"}>
        <View style={s.handleArea} {...pan.panHandlers}>
          <View style={s.handle} />
          <Pressable style={s.close} hitSlop={12} onPress={() => setDebugOpen(false)}
            accessibilityRole="button" accessibilityLabel="Close debug panel">
            <Text style={s.closeText}>✕</Text>
          </Pressable>
        </View>
        <View style={s.sheetTop}>
          <View style={s.preview}>
            <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" pictureSize={PICTURE_SIZE} />
            {(result?.detections ?? []).map((d, i) => (
              <View key={i} style={[s.det, boxStyle(d.bbox)]} pointerEvents="none">
                <Text style={s.detText}>{d.cls} {Math.round(d.conf * 100)}%</Text>
              </View>
            ))}
          </View>
          <View style={s.controls}>
            <TextInput style={s.input} value={address} onChangeText={setAddress} autoCapitalize="none" autoCorrect={false}
              placeholder="server address (wss://… or LAN ip:8000)" placeholderTextColor="#888" />
            <Button title={running ? "Stop" : "Start"} onPress={running ? stop : start} />
            <Text style={s.dbg}>{status.connected ? "connected" : "disconnected"} · {fps.toFixed(1)} fps</Text>
          </View>
        </View>
        <ScrollView style={s.sheetBody} contentContainerStyle={s.sheetContent}>
          <View style={s.toggleRow}>
            <Text style={s.dbg}>Debug dots (scan points, matched place, candidates)</Text>
            <Switch value={debugDots} onValueChange={setDebugDots} />
          </View>
          <Text style={s.h}>Connection</Text>
          <Text style={s.dbg}>server {num(result?.server_ms?.total)} ms · p50 {num(metrics?.p50_ms)} / p95 {num(metrics?.p95_ms)} ms · {status.state ?? "–"}</Text>
          <Text style={s.h}>Localizer</Text>
          <Text style={s.dbg}>{loc?.state ?? "–"} · score {num(loc?.score, 2)} · inliers {loc?.inliers ?? "–"} · yaw {num(loc?.yaw_deg)}°</Text>
          {why.map((t, i) => <Text key={i} style={s.dbgSmall}>{t}</Text>)}
          <Text style={s.h}>Place</Text>
          <Text style={s.dbg}>{decision?.state ?? "–"} · {result?.agent?.place_label ?? "–"} · conf {num(decision?.conf, 2)}</Text>
          {ranks.map((r) => (
            <Text key={r.place} style={[s.dbgSmall, r.here && s.here]}>
              {r.rank}. {r.name}{r.scan != null ? ` · scan ${r.scan}` : ""} · {r.percent}%{r.inliers ? ` · ${r.inliers} in` : ""}
            </Text>
          ))}
          <Text style={s.h}>Position and heading</Text>
          <Text style={s.dbg}>{position ? `x ${num(position.x, 1)} y ${num(position.y, 1)} · ±${num(position.sigma_m, 2)} m · ${position.source ?? "track"}` : "no position"}</Text>
          <Text style={s.dbg}>facing {headingText(facing)} · compass {num(facing?.compass_deg)}°</Text>
          <Text style={s.dbg}>route {path?.length_m ?? "–"} m{path?.length_m != null && !path?.guiding ? " (preview)" : ""} · map {mode}</Text>
          <Text style={s.h}>Phone</Text>
          <Text style={s.dbg}>pitch {pitch == null ? "–" : `${Math.round(pitch)}°`} · {moving == null ? "–" : moving ? "walking" : "still"} · steps {stepCount.current ?? "–"}</Text>
        </ScrollView>
      </Animated.View>
    </View>
  );
}

export default function App() {
  return process.env.EXPO_PUBLIC_NATIVE_NAVIGATION === "1" && trackingModule ? <NativeNavigation /> : <LegacyApp />;
}

const TOP = Platform.OS === "ios" ? 54 : (StatusBar.currentHeight ?? 24) + 8;
const BOTTOM = Platform.OS === "ios" ? 30 : 14;
const BAR_H = 76;
const shadow = { shadowColor: "#000", shadowOpacity: 0.25, shadowRadius: 6, shadowOffset: { width: 0, height: 2 }, elevation: 5 };

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#e8eaed" },
  top: { position: "absolute", top: TOP, left: 10, right: 10 },
  banner: { flexDirection: "row", alignItems: "center", borderRadius: 16, paddingVertical: 12, paddingHorizontal: 14, gap: 12, ...shadow },
  bannerGo: { backgroundColor: "#0d652d" },
  bannerWait: { backgroundColor: "#3c4043" },
  bannerArrow: { width: 44, alignItems: "center" },
  bannerText: { flex: 1 },
  bannerTitle: { color: "#fff", fontSize: 24, fontWeight: "700" },
  bannerMeters: { color: "#d2e3d8", fontSize: 20, fontWeight: "600", marginTop: 2 },
  mic: { width: 48, height: 48, borderRadius: 24, backgroundColor: "#fff", alignItems: "center", justifyContent: "center" },
  micOn: { backgroundColor: "#d93025" },
  talkNote: { alignSelf: "center", marginTop: 8, backgroundColor: "#3c4043", color: "#fff", fontSize: 15, fontWeight: "600",
    paddingHorizontal: 14, paddingVertical: 6, borderRadius: 14, overflow: "hidden" },
  talkNoteOn: { backgroundColor: "#d93025" },
  side: { position: "absolute", right: 12, bottom: BOTTOM + BAR_H + 16, gap: 12 },
  round: { width: 48, height: 48, borderRadius: 24, backgroundColor: "#fff", alignItems: "center", justifyContent: "center", ...shadow },
  roundOn: { backgroundColor: "#d2e3fc" },
  chip: { position: "absolute", left: 12, bottom: BOTTOM + BAR_H + 16, backgroundColor: "#fff", borderRadius: 14,
    paddingHorizontal: 12, paddingVertical: 6, ...shadow },
  chipText: { color: "#3c4043", fontSize: 14, fontWeight: "600" },
  pinCard: { position: "absolute", left: 10, right: 10, bottom: BOTTOM + BAR_H + 12, backgroundColor: "#fff", borderRadius: 16,
    padding: 14, gap: 6, ...shadow },
  pinTitle: { color: "#202124", fontSize: 18, fontWeight: "700" },
  pinNote: { color: "#5f6368", fontSize: 14 },
  pinRow: { flexDirection: "row", justifyContent: "flex-end", gap: 10, marginTop: 4 },
  pinCancel: { paddingVertical: 10, paddingHorizontal: 18, borderRadius: 20, backgroundColor: "#f1f3f4" },
  pinCancelText: { color: "#1a73e8", fontSize: 16, fontWeight: "700" },
  pinStart: { paddingVertical: 10, paddingHorizontal: 22, borderRadius: 20, backgroundColor: "#1a73e8" },
  pinStartText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  pinModeOn: { backgroundColor: "#5f6368" },
  pinChip: { position: "absolute", left: 12, bottom: BOTTOM + BAR_H + 60, backgroundColor: "#fff", borderRadius: 18,
    paddingVertical: 8, paddingHorizontal: 14, ...shadow },
  pinChipText: { color: "#202124", fontSize: 15, fontWeight: "600" },
  bar: { position: "absolute", left: 10, right: 10, bottom: BOTTOM, height: BAR_H, backgroundColor: "#fff", borderRadius: 20,
    flexDirection: "row", alignItems: "center", paddingHorizontal: 18, gap: 12, ...shadow },
  barText: { flex: 1 },
  barLine: { fontSize: 22 },
  barTime: { color: "#188038", fontSize: 24, fontWeight: "700" },
  barLeft: { color: "#3c4043", fontSize: 20 },
  barIdle: { color: "#3c4043", fontSize: 20, fontWeight: "600" },
  barPlace: { color: "#5f6368", fontSize: 14, marginTop: 2 },
  exit: { backgroundColor: "#d93025", borderRadius: 22, minWidth: 88, height: 48, paddingHorizontal: 20, alignItems: "center", justifyContent: "center" },
  startNav: { backgroundColor: "#1a73e8" },
  disabled: { opacity: 0.4 },
  exitText: { color: "#fff", fontSize: 18, fontWeight: "700" },
  sheet: { position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: "#202124", borderTopLeftRadius: 20,
    borderTopRightRadius: 20, ...shadow },
  handleArea: { alignItems: "center", justifyContent: "center", height: 44 },
  close: { position: "absolute", right: 12, top: 6, width: 32, height: 32, borderRadius: 16, backgroundColor: "#3c4043",
    alignItems: "center", justifyContent: "center" },
  closeText: { color: "#e8eaed", fontSize: 16, fontWeight: "700" },
  handle: { width: 44, height: 5, borderRadius: 3, backgroundColor: "#5f6368" },
  sheetTop: { flexDirection: "row", gap: 12, paddingHorizontal: 16 },
  preview: { width: 96, height: 128, borderRadius: 8, overflow: "hidden", backgroundColor: "#000" },
  det: { position: "absolute", borderWidth: 1.5, borderColor: "#fbbc04" },
  detText: { color: "#fbbc04", fontSize: 8, fontWeight: "700" },
  controls: { flex: 1, gap: 8 },
  input: { backgroundColor: "#303134", color: "#fff", padding: 10, borderRadius: 8, fontSize: 15 },
  sheetBody: { flex: 1, marginTop: 8 },
  sheetContent: { paddingHorizontal: 16, paddingBottom: BOTTOM + 16, gap: 4 },
  toggleRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 6 },
  h: { color: "#8ab4f8", fontSize: 13, fontWeight: "700", marginTop: 10, textTransform: "uppercase" },
  dbg: { color: "#e8eaed", fontSize: 14 },
  dbgSmall: { color: "#bdc1c6", fontSize: 12 },
  here: { color: "#81c995", fontWeight: "700" },
});
