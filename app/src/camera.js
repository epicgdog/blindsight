// Capture settings. A full iPhone photo is 4032x3024 (12.2 MP) and the server rejects frames
// over 12 MP, so ask the camera for the 1280x720 preset instead (an expo-camera iOS PictureSize).
export const PICTURE_SIZE = "1280x720";

// skipProcessing keeps capture fast but makes expo-camera ignore `quality`; the server applies
// the EXIF rotation, so the frame still arrives upright.
export const PICTURE_OPTIONS = { exif: false, skipProcessing: true, base64: true };

export const megapixels = (w, h) => Math.round((w * h) / 1e4) / 100;
