// Capture settings. A full iPhone photo is 4032x3024 (12.2 MP). The phone resizes every frame to
// 720 pixels on the short side (960x720 or 720x960 for a 4:3 photo) before sending, which keeps
// uploads small and fast. The 4:3 shape also keeps the camera's full field of view.
export const PICTURE_SIZE = "1280x720"; // ask the camera for a small preset (iOS may ignore it)
export const TARGET_SHORT_SIDE = 720;

// skipProcessing keeps capture fast. The bytes come from the resize step, not the camera, so
// base64 is off here to avoid encoding the full 12 MP photo. The server applies EXIF rotation.
export const PICTURE_OPTIONS = { exif: false, skipProcessing: true, base64: false };

// JPEG save options for the resized frame.
export const SAVE_OPTIONS = { compress: 0.6, base64: true };

export const megapixels = (w, h) => Math.round((w * h) / 1e4) / 100;

// Resize plan for ImageManipulator, or null when the frame is already small enough.
// Never upscales; frames within ~5% of the target are left alone.
export function resizePlan(width, height, shortSide = TARGET_SHORT_SIDE) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  if (Math.min(width, height) <= shortSide * 1.05) return null;
  return width <= height ? { width: shortSide } : { height: shortSide };
}
