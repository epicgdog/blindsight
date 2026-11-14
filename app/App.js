import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, StyleSheet, Text, TextInput, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as Speech from "expo-speech";
import { setAudioModeAsync } from "expo-audio";
import * as Location from "expo-location";
import * as ScreenOrientation from "expo-screen-orientation";
import { activateKeepAwakeAsync, deactivateKeepAwake } from "expo-keep-awake";
import { Accelerometer } from "expo-sensors";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { createClient } from "./src/client";
import { createCueSpeaker } from "./src/speech";
import { runCaptureLoop } from "./src/loop";
import { encodeFrame } from "./src/protocol";
import { normalizeServerUrl } from "./src/address";
import { base64ToBytes } from "./src/base64";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import { AUDIO_MODE, speechOptions } from "./src/audio";
import { attitudeFromGravity, createMotionDetector, smoothGravity } from "./src/attitude";
import { ackBody, createAckTracker, sendAck, speechAckUrl } from "./src/ack";
import { PICTURE_OPTIONS, PICTURE_SIZE, SAVE_OPTIONS, resizePlan } from "./src/camera";

const KEY = "blindsight.server";
const DEFAULT = process.env.EXPO_PUBLIC_SERVER_URL ?? "";

export default function App() {
  const [perm, askPerm] = useCameraPermissions();
  const [address, setAddress] = useState(DEFAULT);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState({ connected: false, state: null });
  const [fps, setFps] = useState(0);
  const camera = useRef(null);
  const heading = useRef(null);
  const runningRef = useRef(false);
  const client = useRef(null);
  const speaking = useRef(false);
  const frames = useRef([]);
  const sizeLogged = useRef(false);
  const ackUrl = useRef(null);
  const attitude = useRef(null);
  const [pitch, setPitch] = useState(null);
  const [moving, setMoving] = useState(null);
  const ackTracker = useRef(createAckTracker());

  useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem(KEY);
      if (saved && !DEFAULT) setAddress(saved);
      await setAudioModeAsync(AUDIO_MODE).catch((e) => console.log("audio mode", e?.message));
      await ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
      const loc = await Location.requestForegroundPermissionsAsync();
      if (loc.granted) {
        await Location.watchHeadingAsync((h) => {
          heading.current = h.trueHeading >= 0 ? h.trueHeading : h.magHeading;
        });
      }
    })().catch((e) => console.log("init", e?.message));
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
    })().catch((e) => console.log("accelerometer", e?.message));
    return () => { cancelled = true; sub?.remove(); };
  }, []);

  const speaker = useRef(
    createCueSpeaker(
      {
        speak: (t, meta) => {
          Speech.speak(t, speechOptions({
            onStart: () => {
              speaking.current = true;
              // Tell the server speech actually started, so it can measure capture-to-audible latency.
              if (meta?.startup) sendAck(fetch, ackUrl.current, ackBody(null, null));
              else if (meta?.id) sendAck(fetch, ackUrl.current, ackBody(meta.id, ackTracker.current.captureToAudibleMs(meta.id, Date.now())));
            },
            onSettled: () => (speaking.current = false),
          }));
          speaking.current = true;
        },
        stop: () => { Speech.stop(); speaking.current = false; },
        isSpeaking: () => speaking.current,
      },
      { log: (m) => console.log(m) },
    ),
  ).current;

  const start = useCallback(async () => {
    const url = normalizeServerUrl(address);
    if (!url) return;
    await AsyncStorage.setItem(KEY, address);
    if (!perm?.granted) await askPerm();
    await activateKeepAwakeAsync();
    await setAudioModeAsync(AUDIO_MODE).catch((e) => console.log("audio mode", e?.message));
    await speaker.startupCheck();
    ackUrl.current = speechAckUrl(url);
    client.current = createClient({
      url, speaker, onStatus: setStatus,
      onResult: (r) => { if (r.cue) ackTracker.current.noteCue(r.cue.id, r.seq); },
    });
    client.current.start();
    runningRef.current = true; setRunning(true);
    runCaptureLoop({
      isRunning: () => runningRef.current,
      capture: async () => {
        const p = await camera.current.takePictureAsync(PICTURE_OPTIONS);
        // Resize to 720 px on the short side on the phone, then read the JPEG bytes back.
        const ctx = ImageManipulator.manipulate(p.uri);
        const plan = resizePlan(p.width, p.height);
        if (plan) ctx.resize(plan);
        const out = await (await ctx.renderAsync()).saveAsync({ ...SAVE_OPTIONS, format: SaveFormat.JPEG });
        if (!sizeLogged.current) {
          sizeLogged.current = true;
          console.log(`camera ${p.width}x${p.height} -> sending ${out.width}x${out.height}`);
        }
        return { bytes: base64ToBytes(out.base64), w: out.width, h: out.height };
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
      onTiming: (t) => { if (t.slow) console.log(`slow capture ${t.captureMs} ms: lower quality/size`); },
    });
  }, [address, perm, askPerm, speaker]);

  const stop = useCallback(() => {
    runningRef.current = false; setRunning(false);
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
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#000", padding: 20, paddingTop: 60, gap: 12 },
  title: { color: "#fff", fontSize: 32, fontWeight: "800" },
  input: { backgroundColor: "#222", color: "#fff", padding: 12, borderRadius: 8, fontSize: 16 },
  status: { color: "#fff", fontSize: 20 },
  preview: { width: 120, height: 160, borderRadius: 8, overflow: "hidden" },
});
