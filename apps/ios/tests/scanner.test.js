import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { decodeImage } from "../js/scanner.js";
import { parseSetupCode } from "../js/setupcode.js";

const require = createRequire(import.meta.url);
const readQr = require("../js/vendor/jsQR.js");
const fixture = JSON.parse(readFileSync(new URL("./fixtures/setup-code.json", import.meta.url), "utf8"));
const PIXELS_PER_MODULE = 4;
const QUIET_ZONE_MODULES = 4;
const DARK = 0;
const LIGHT = 255;

function picture(modules) {
  const size = (modules.length + 2 * QUIET_ZONE_MODULES) * PIXELS_PER_MODULE;
  const data = new Uint8ClampedArray(size * size * 4).fill(LIGHT);
  modules.forEach((row, rowIndex) => [...row].forEach((cell, columnIndex) => {
    if (cell !== "1") return;
    for (let dy = 0; dy < PIXELS_PER_MODULE; dy += 1) {
      for (let dx = 0; dx < PIXELS_PER_MODULE; dx += 1) {
        const y = (rowIndex + QUIET_ZONE_MODULES) * PIXELS_PER_MODULE + dy;
        const x = (columnIndex + QUIET_ZONE_MODULES) * PIXELS_PER_MODULE + dx;
        data.fill(DARK, (y * size + x) * 4, (y * size + x) * 4 + 3);
      }
    }
  }));
  return { data, width: size, height: size };
}

test("the iPhone's QR reader reads the code Arty Desktop draws", () => {
  assert.equal(decodeImage(readQr, picture(fixture.modules)), fixture.text);
});

test("the code Arty Desktop draws turns into the iPhone's settings", () => {
  const settings = parseSetupCode(decodeImage(readQr, picture(fixture.modules)));
  assert.equal(settings.apiKey, JSON.parse(fixture.text).key);
  assert.ok(settings.accessId.endsWith(".access"));
});

test("a picture with no code in it reads as nothing", () => {
  const blank = picture(fixture.modules.map((row) => "0".repeat(row.length)));
  assert.equal(decodeImage(readQr, blank), null);
});
