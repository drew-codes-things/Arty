import { h } from "./dom.js";

export const QR_BORDER = 4;
const ERROR_CORRECTION = "M";
const PICK_SIZE_TO_FIT = 0;
const QR_MAKER_PATH = "js/vendor/qrcode-generator.js";
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const LIGHT = "#ffffff";
const DARK = "#000000";

let qrMakerLoading = null;

function loadQrMaker() {
  if (globalThis.qrcode) return Promise.resolve(globalThis.qrcode);
  qrMakerLoading ??= new Promise((resolve, reject) => {
    const script = h("script", { src: QR_MAKER_PATH });
    const failToLoad = () => {
      qrMakerLoading = null;
      reject(new Error("Couldn't load the QR code maker. Check the connection and try again."));
    };
    script.addEventListener("load", () => (globalThis.qrcode ? resolve(globalThis.qrcode) : failToLoad()));
    script.addEventListener("error", failToLoad);
    document.head.append(script);
  });
  return qrMakerLoading;
}

export function qrModules(makeQr, text) {
  const content = String(text ?? "");
  if (!content) throw new Error("There's nothing to put in the QR code.");
  const code = makeQr(PICK_SIZE_TO_FIT, ERROR_CORRECTION);
  code.addData(content, "Byte");
  code.make();
  const count = code.getModuleCount();
  return Array.from({ length: count }, (_, row) => Array.from({ length: count }, (_, column) => code.isDark(row, column)));
}

export function qrPath(modules) {
  const squares = [];
  modules.forEach((row, y) => row.forEach((dark, x) => {
    if (dark) squares.push(`M${x + QR_BORDER} ${y + QR_BORDER}h1v1h-1z`);
  }));
  return { size: modules.length + 2 * QR_BORDER, path: squares.join("") };
}

function svgElement(tag, attributes) {
  const element = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  return element;
}

export async function qrImage(text, label) {
  const { size, path } = qrPath(qrModules(await loadQrMaker(), text));
  const image = svgElement("svg", { viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": label, class: "qr", "shape-rendering": "crispEdges" });
  image.append(svgElement("rect", { width: size, height: size, fill: LIGHT }), svgElement("path", { d: path, fill: DARK }));
  return image;
}
