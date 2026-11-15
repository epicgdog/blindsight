// Capture settings. A full iPhone photo is 4032x3024 (12.2 MP). The phone resizes every frame to
// 720 pixels on the short side (960x720 or 720x960 for a 4:3 photo) before sending, which keeps
// uploads small and fast. The 4:3 shape also keeps the camera's full field of view.
export const PICTURE_SIZE = "1280x720"; // ask the camera for a small preset (iOS may ignore it)
export const TARGET_SHORT_SIDE = 720;

// skipProcessing false: expo-camera rotates the photo upright (teammate fix d8471e5). The bytes
// come from the resize step, not the camera, so base64 is off to avoid encoding the full photo.
// exif on: the camera's own metadata (focal length, 35 mm equivalent, lens) is sent to the server
// now and then (cameraMetaMessage) to measure the real field of view for mesh-based localization.
export const PICTURE_OPTIONS = { exif: true, skipProcessing: false, base64: false };

// Camera metadata sample: on the first frame and every META_EVERY frames after it.
export const META_EVERY = 20;

export function cameraMetaMessage({ seq, photo, sent, exif, sensors, pictureSize, availableSizes }) {
  return {
    type: "camera_meta",
    seq,
    t_ms: sensors?.ts_ms ?? Date.now(),
    photo: photo ? { width: photo.width, height: photo.height } : null, // as taken, before the resize
    sent: sent ? { width: sent.width, height: sent.height } : null, // what the server decodes
    picture_size: pictureSize ?? null, // the size requested from the camera
    available_sizes: availableSizes ?? null,
    sensors: sensors ?? null,
    exif: exif ?? null,
  };
}

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
