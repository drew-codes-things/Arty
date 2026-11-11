export { artyFilePath } from "./api.js";

const PICTURE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif", "bmp", "heic", "avif"]);
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "mkv", "3gp"]);

export function fileNameFromPath(path) {
  const last = path.split("?")[0].split("/").filter(Boolean).pop() ?? "file";
  try {
    return decodeURIComponent(last);
  } catch {
    return last;
  }
}

export function mediaKind(link, markedPicture = false) {
  const extension = link.split("?")[0].split(".").pop().toLowerCase();
  if (VIDEO_EXTENSIONS.has(extension)) return "video";
  if (markedPicture || PICTURE_EXTENSIONS.has(extension)) return "picture";
  return null;
}
