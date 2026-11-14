// Make spoken cues audible: play through the iPhone silent switch and duck other audio.
export const AUDIO_MODE = {
  playsInSilentMode: true,
  interruptionMode: "duckOthers",
  allowsRecording: false,
  shouldPlayInBackground: false,
};

export const SPEECH_OPTIONS = {
  language: "en-US",
  volume: 1,
  rate: 1,
  useApplicationAudioSession: true, // use the session configured with AUDIO_MODE
};

// Speech options plus lifecycle callbacks. onSettled fires however speech ends.
export function speechOptions({ onStart, onDone, onSettled } = {}) {
  const settle = () => onSettled?.();
  return {
    ...SPEECH_OPTIONS,
    onStart: () => onStart?.(),
    onDone: () => { onDone?.(); settle(); },
    onStopped: settle,
    onError: settle,
  };
}
