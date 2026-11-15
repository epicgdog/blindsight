import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { RecordingPresets, createAudioPlayer, requestRecordingPermissionsAsync, setAudioModeAsync, useAudioRecorder } from "expo-audio";
import * as Location from "expo-location";
import * as ScreenOrientation from "expo-screen-orientation";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { Accelerometer, Pedometer } from "expo-sensors";
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
import { PICTURE_OPTIONS, PICTURE_SIZE, SAVE_OPTIONS, resizePlan } from "./src/camera";
import { File } from "expo-file-system";
import { RECORD_MODE, agentQuietUrl, agentTalkUrl, postClip, createHoldToTalk } from "./src/talk";
import NativeNavigation, { trackingModule } from "./NativeNavigation";

const KEY = "blindsight.server";
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
  const ackUrl = useRef(null);
  const attitude = useRef(null);
  const [pitch, setPitch] = useState(null);
  const [moving, setMoving] = useState(null);
  const ackTracker = useRef(createAckTracker());
  const imuTimer = useRef(null);

  useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem(KEY);
      if (saved && !DEFAULT) setAddress(saved);
      await setAudioModeAsync(AUDIO_MODE).catch((e) => logApp("audio mode failed", { error: e?.message }));
      await ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
      const loc = await Location.requestForegroundPermissionsAsync();
      if (loc.granted) {
        await Location.watchHeadingAsync((h) => {
          heading.current = h.trueHeading >= 0 ? h.trueHeading : h.magHeading;
        });
      }
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
        const m = motion.push(g, now);
        const att = attitudeFromGravity(gravity);
        attitude.current = att && typeof m === "boolean" ? { ...att, moving: m } : att;
        if (now - last > 500 && att) { last = now; setPitch(att.pitch_deg); setMoving(m); }
      });
    })().catch((e) => logApp("accelerometer failed", { error: e?.message }));
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

  return (
    <View style={s.root}>
      <Text style={s.title}>Blindsight</Text>
      <TextInput style={s.input} value={address} onChangeText={setAddress} autoCapitalize="none" autoCorrect={false}
        placeholder="server address (wss://… or LAN ip:8000)" placeholderTextColor="#888" />
      <Button title={running ? "Stop" : "Start"} onPress={running ? stop : start} />
      <Text style={s.status}>
        {status.connected ? "connected" : "disconnected"} · {status.state ?? "–"} · {fps.toFixed(1)} fps · pitch {pitch == null ? "–" : `${Math.round(pitch)}°`} · {moving == null ? "–" : moving ? "walking" : "still"}
      </Text>
      <CameraView ref={camera} style={s.preview} facing="back" pictureSize={PICTURE_SIZE} />
      <Pressable style={[s.talk, talkState === "listening" && s.talkOn]} onPressIn={talkIn} onPressOut={talkOut}
        accessibilityRole="button" accessibilityLabel="Hold to talk">
        <Text style={s.talkText}>
          {talkState === "preparing" ? "Preparing microphone…" : talkState === "listening" ? "Listening…" : talkState === "thinking" ? "Thinking…" : "Hold to talk"}
        </Text>
      </Pressable>
    </View>
  );
}

export default function App() {
  return process.env.EXPO_PUBLIC_NATIVE_NAVIGATION === "1" && trackingModule ? <NativeNavigation /> : <LegacyApp />;
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000", padding: 20, paddingTop: 60, gap: 12 },
  title: { color: "#fff", fontSize: 32, fontWeight: "800" },
  input: { backgroundColor: "#222", color: "#fff", padding: 12, borderRadius: 8, fontSize: 16 },
  status: { color: "#fff", fontSize: 20 },
  preview: { width: 120, height: 160, borderRadius: 8, overflow: "hidden" },
  talk: { flex: 1, minHeight: 160, backgroundColor: "#1d4ed8", borderRadius: 16, alignItems: "center", justifyContent: "center" },
  talkOn: { backgroundColor: "#dc2626" },
  talkText: { color: "#fff", fontSize: 34, fontWeight: "800" },
});
