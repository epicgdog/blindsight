import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, StyleSheet, Text, TextInput, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as Speech from "expo-speech";
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
import { isUprightPortrait, smoothGravity } from "./src/orientation";
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
  const [upright, setUpright] = useState(false);
  const uprightRef = useRef(false);
  const sensorOk = useRef(true);
  const blockedSince = useRef(0);
  const lastWarn = useRef(0);

  useEffect(() => {
    (async () => {
      const saved = await AsyncStorage.getItem(KEY);
      if (saved && !DEFAULT) setAddress(saved);
      await ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
      const loc = await Location.requestForegroundPermissionsAsync();
      if (loc.granted) {
        await Location.watchHeadingAsync((h) => {
          heading.current = h.trueHeading >= 0 ? h.trueHeading : h.magHeading;
        });
      }
    })().catch((e) => console.log("init", e?.message));
  }, []);

  // Only send frames while the phone is held upright in portrait (accelerometer gravity cone).
  useEffect(() => {
    let sub = null, gravity = null, cancelled = false;
    (async () => {
      if (!(await Accelerometer.isAvailableAsync())) { sensorOk.current = false; return; }
      Accelerometer.setUpdateInterval(100);
      if (cancelled) return;
      sub = Accelerometer.addListener((g) => {
        gravity = smoothGravity(gravity, g);
        const ok = isUprightPortrait(gravity);
        if (ok !== uprightRef.current) {
          uprightRef.current = ok;
          blockedSince.current = ok ? 0 : Date.now();
          setUpright(ok);
        }
      });
    })().catch((e) => { sensorOk.current = false; console.log("accelerometer", e?.message); });
    return () => { cancelled = true; sub?.remove(); };
  }, []);

  const speaker = useRef(
    createCueSpeaker(
      {
        speak: (t) => { Speech.speak(t, { onStart: () => (speaking.current = true), onDone: () => (speaking.current = false), onStopped: () => (speaking.current = false), onError: () => (speaking.current = false) }); speaking.current = true; },
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
    await speaker.startupCheck();
    client.current = createClient({ url, speaker, onStatus: setStatus });
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
      canCapture: () => !sensorOk.current || uprightRef.current,
      onBlocked: () => {
        const now = Date.now();
        if (now - blockedSince.current > 2000 && now - lastWarn.current > 8000) {
          lastWarn.current = now;
          speaker.announce("Hold the phone upright");
        }
      },
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
        {status.connected ? "connected" : "disconnected"} · {status.state ?? "–"} · {fps.toFixed(1)} fps · {upright ? "upright" : "tilted: hold upright"}
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
