import assert from "node:assert/strict";
import test from "node:test";

import { inspectEsp32AppImage } from "./firmware-image.mjs";

function imageFixture(releaseVersion = "0.4.0") {
  const data = Buffer.alloc(384);
  Buffer.from([0x32, 0x54, 0xcd, 0xab]).copy(data, 0);
  Buffer.from("git-dirty").copy(data, 16);
  Buffer.from(`VEETEE_RELEASE_VERSION=${releaseVersion}\0`).copy(data, 288);
  const image = Buffer.alloc(24 + 8 + data.length);
  image[0] = 0xe9;
  image[1] = 1;
  image.writeUInt32LE(0x3c000020, 24);
  image.writeUInt32LE(data.length, 28);
  data.copy(image, 32);
  return image;
}

test("reads the release marker independently from the ESP-IDF app version", () => {
  assert.deepEqual(inspectEsp32AppImage(imageFixture()), {
    segmentCount: 1,
    appVersion: "git-dirty",
    releaseVersion: "0.4.0",
  });
});

test("rejects images without a Veetee release marker", () => {
  assert.throws(
    () => inspectEsp32AppImage(imageFixture("not-semver")),
    /release version marker/,
  );
});
