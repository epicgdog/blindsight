import React, { useEffect, useRef, useState } from "react";
import { AppState, Button, Text, TextInput, View } from "react-native";
import { requireOptionalNativeModule } from "expo-modules-core";
import * as Speech from "expo-speech";
import { createNavigationClient } from "./src/navigationClient";

export const trackingModule = requireOptionalNativeModule("BlindsightTracking");

export default function NativeNavigation() {
  const [address, setAddress] = useState(process.env.EXPO_PUBLIC_SERVER_URL ?? "");
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState("Not started");
  const [running, setRunning] = useState(false);
  const client = useRef(null);
  const stop = () => { trackingModule.stop(); client.current?.stop(); setRunning(false); };
  useEffect(() => {
    const frame = trackingModule.addListener("tracking", async packet => {
      const r = await client.current?.tracking(packet);
      if (r) setStatus(r.error ?? r.state);
    });
    const hazard = trackingModule.addListener("hazard", event => {
      client.current?.emergency(event.text); setStatus(event.text);
    });
    const voice = trackingModule.addListener("question", event => {
      if (event.text) ask(event.text); else setStatus(event.error);
    });
    const background = AppState.addEventListener("change", state => { if (state !== "active") stop(); });
    const timer = setInterval(() => client.current?.watchdog(), 200);
    return () => { frame.remove(); hazard.remove(); voice.remove(); background.remove(); clearInterval(timer); stop(); };
  }, []);
  const ask = async text => {
    try { const result = await client.current?.command(text); if (result) setStatus(result.state); }
    catch { setStatus("Question unavailable. Check connection."); }
  };
  const start = async () => {
    try {
      const base = address.trim().replace(/^wss:/, "https:").replace(/^ws:/, "http:").replace(/\/ws\/phone\/?$/, "").replace(/\/$/, "");
      if (!/^https?:\/\//.test(base)) throw new Error("Enter an http or https server URL");
      const caps = await trackingModule.start();
      if (!caps.depth && !caps.objectDetection) {
        setStatus("Local perception is unavailable. Install the detector model before using obstacle warnings.");
        trackingModule.stop(); return;
      }
      setRunning(true);
      client.current = createNavigationClient({ base, speak: text => Speech.speak(text), stop: () => Speech.stop() });
      try { await client.current.start(); }
      catch { setStatus("Local obstacle warnings only. Spatial server unavailable."); return; }
      setStatus(caps.depth ? "Tracking started. Waiting for verified alignment." : "Depth unavailable. Walking instructions disabled.");
    } catch (error) { stop(); setStatus(error.message); }
  };
  return <View style={{ flex: 1, backgroundColor: "#101010", padding: 24, paddingTop: 64, gap: 18 }}>
    <Text accessibilityRole="header" style={{ color: "white", fontSize: 30 }}>Blindsight navigation</Text>
    <TextInput accessibilityLabel="Server address" value={address} onChangeText={setAddress} autoCapitalize="none"
      style={{ color: "white", borderColor: "white", borderWidth: 1, padding: 14 }} />
    <Button title={running ? "Stop navigation" : "Start orientation"} onPress={running ? stop : start} />
    <Text accessibilityLiveRegion="polite" style={{ color: "white", fontSize: 20 }}>{status}</Text>
    <Button title="Ask a question by voice" disabled={!running} onPress={() => trackingModule.listen()} />
    <TextInput accessibilityLabel="Question or destination, for example take me to the exit" value={question} onChangeText={setQuestion}
      style={{ color: "white", borderColor: "white", borderWidth: 1, padding: 14 }} />
    <Button title="Ask or choose destination" disabled={!running} onPress={() => ask(question)} />
    <Button title="Where am I?" disabled={!running} onPress={() => ask("Where am I? ")} />
    <Button title="What's on my left?" disabled={!running} onPress={() => ask("What's on my left? ")} />
    <Button title="Repeat instruction" disabled={!running} onPress={() => ask("repeat")} />
  </View>;
}
