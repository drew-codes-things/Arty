import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";
import { QR_BORDER, qrModules, qrPath } from "../js/qr.js";

const require = createRequire(import.meta.url);
const makeQr = require("../js/vendor/qrcode-generator.js");
const readQr = require("../js/vendor/jsQR.js");

const PIXELS_PER_MODULE = 4;

function pixelsOf(modules) {
  const side = (modules.length + 2 * QR_BORDER) * PIXELS_PER_MODULE;
  const data = new Uint8ClampedArray(side * side * 4).fill(255);
  modules.forEach((row, y) => row.forEach((dark, x) => {
    if (!dark) return;
    for (let dy = 0; dy < PIXELS_PER_MODULE; dy += 1) {
      for (let dx = 0; dx < PIXELS_PER_MODULE; dx += 1) {
        const offset = (((y + QR_BORDER) * PIXELS_PER_MODULE + dy) * side + (x + QR_BORDER) * PIXELS_PER_MODULE + dx) * 4;
        data[offset] = data[offset + 1] = data[offset + 2] = 0;
      }
    }
  }));
  return { data, side };
}

test("a link drawn as a QR code reads back the same", () => {
  const link = "https://starfire.example/arty-devices.html?code=ABCD-EFGH";
  const { data, side } = pixelsOf(qrModules(makeQr, link));
  assert.equal(readQr(data, side, side)?.data, link);
});

test("a phone setup code with a long key still reads back the same", () => {
  const code = JSON.stringify({ arty: 1, url: "https://arty.example", key: `arty_dev_${"k".repeat(43)}`, id: "a.access", secret: "s".repeat(64) });
  const { data, side } = pixelsOf(qrModules(makeQr, code));
  assert.equal(readQr(data, side, side)?.data, code);
});

test("the drawing has one square per dark module and a quiet border", () => {
  const modules = qrModules(makeQr, "ABCD-EFGH");
  const dark = modules.flat().filter(Boolean).length;
  const drawn = qrPath(modules);
  assert.equal(drawn.size, modules.length + 2 * QR_BORDER);
  assert.equal(drawn.path.match(/h1v1h-1z/g).length, dark);
  assert.match(drawn.path, new RegExp(`^M${QR_BORDER} ${QR_BORDER}|^M\\d+ \\d+`));
});

test("nothing is drawn for empty text", () => {
  assert.throws(() => qrModules(makeQr, ""), /nothing to put in the QR code/);
});
