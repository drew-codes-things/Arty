import { h } from "./dom.js";

const SCAN_EVERY_MS = 200;
const MAX_UNREADABLE_FRAMES_IN_A_ROW = 5;
const MAX_SCAN_WIDTH = 720;
const QR_READER_PATH = "js/vendor/jsQR.js";
const NO_CAMERA = "Arty can't use the camera here. Allow camera access for Arty in Settings, or enter the details manually.";

let qrReaderLoading = null;

function loadQrReader() {
  if (globalThis.jsQR) return Promise.resolve(globalThis.jsQR);
  qrReaderLoading ??= new Promise((resolve, reject) => {
    const script = h("script", { src: QR_READER_PATH });
    const failToLoad = () => {
      qrReaderLoading = null;
      reject(new Error("Couldn't load the QR code reader. Check the connection and try again."));
    };
    script.addEventListener("load", () => (globalThis.jsQR ? resolve(globalThis.jsQR) : failToLoad()));
    script.addEventListener("error", failToLoad);
    document.head.append(script);
  });
  return qrReaderLoading;
}

export function decodeImage(readQr, image) {
  return readQr(image.data, image.width, image.height)?.data ?? null;
}

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error(NO_CAMERA);
  try {
    return await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  } catch (error) {
    throw new Error(`${NO_CAMERA} (${error.name})`);
  }
}

function scanSize(video) {
  const scale = Math.min(1, MAX_SCAN_WIDTH / video.videoWidth);
  return { width: Math.round(video.videoWidth * scale), height: Math.round(video.videoHeight * scale) };
}

function watchForCode(readQr, stream) {
  return new Promise((resolve, reject) => {
    const video = h("video", { class: "scanner-video", playsinline: true, muted: true, autoplay: true });
    video.muted = true;
    video.srcObject = stream;
    const cancel = h("button", { type: "button", class: "button" }, "Cancel");
    const overlay = h("div", { class: "scanner", role: "dialog", "aria-label": "Scan the setup code" },
      video, h("p", {}, "Point the camera at the QR code on your computer."), cancel);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    let timer = null;
    let finished = false;
    let unreadableFramesInARow = 0;
    const finish = (found, error) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      stream.getTracks().forEach((track) => track.stop());
      overlay.remove();
      if (error) reject(error);
      else resolve(found);
    };
    const readFrame = () => {
      Object.assign(canvas, scanSize(video));
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      return decodeImage(readQr, context.getImageData(0, 0, canvas.width, canvas.height));
    };
    const look = () => {
      if (finished) return undefined;
      if (video.readyState >= video.HAVE_ENOUGH_DATA && video.videoWidth) {
        let found;
        try {
          found = readFrame();
          unreadableFramesInARow = 0;
        } catch (error) {
          unreadableFramesInARow += 1;
          if (unreadableFramesInARow >= MAX_UNREADABLE_FRAMES_IN_A_ROW) {
            return finish(null, new Error(`Reading the camera failed (${error.message}). Try again, or enter the details manually.`));
          }
        }
        if (found) return finish(found);
      }
      timer = setTimeout(look, SCAN_EVERY_MS);
      return undefined;
    };
    cancel.addEventListener("click", () => finish(null));
    document.body.append(overlay);
    video.play().then(look, (error) => finish(null, new Error(`The camera wouldn't start (${error.message}).`)));
  });
}

export async function scanQrCode() {
  const readQr = await loadQrReader();
  return watchForCode(readQr, await openCamera());
}
