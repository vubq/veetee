const appDescriptionMagic = Buffer.from([0x32, 0x54, 0xcd, 0xab]);
const releaseVersionMarker = Buffer.from("VEETEE_RELEASE_VERSION=", "ascii");
const semverPattern = /^\d+\.\d+\.\d+$/;

export function inspectEsp32AppImage(image) {
  if (!Buffer.isBuffer(image) || image.length < 32 || image[0] !== 0xe9) {
    throw new Error("Firmware input does not have a valid ESP32 image header");
  }
  const segmentCount = image[1] ?? 0;
  if (segmentCount < 1 || segmentCount > 16) {
    throw new Error("Firmware input segment table is invalid");
  }
  const descriptions = [];
  const releaseVersions = [];
  let offset = 24;
  for (let segment = 0; segment < segmentCount; segment += 1) {
    if (offset + 8 > image.length) throw new Error("Firmware input segment header is truncated");
    const length = image.readUInt32LE(offset + 4);
    offset += 8;
    if (offset + length > image.length) throw new Error("Firmware input segment is truncated");
    const end = offset + length;
    for (
      let magicOffset = image.indexOf(appDescriptionMagic, offset);
      magicOffset >= 0 && magicOffset + 48 <= end;
      magicOffset = image.indexOf(appDescriptionMagic, magicOffset + 1)
    ) {
      const rawVersion = image.subarray(magicOffset + 16, magicOffset + 48);
      const nul = rawVersion.indexOf(0);
      const version = rawVersion.subarray(0, nul >= 0 ? nul : rawVersion.length).toString("utf8");
      if (version && /^[\x20-\x7e]+$/.test(version)) descriptions.push(version);
    }
    for (
      let markerOffset = image.indexOf(releaseVersionMarker, offset);
      markerOffset >= 0 && markerOffset + releaseVersionMarker.length < end;
      markerOffset = image.indexOf(releaseVersionMarker, markerOffset + 1)
    ) {
      const valueOffset = markerOffset + releaseVersionMarker.length;
      const valueEnd = image.indexOf(0, valueOffset);
      if (valueEnd > valueOffset && valueEnd <= end && valueEnd - valueOffset <= 32) {
        const version = image.subarray(valueOffset, valueEnd).toString("ascii");
        if (semverPattern.test(version)) releaseVersions.push(version);
      }
    }
    offset = end;
    while (offset % 4 !== 0) offset += 1;
  }
  if (descriptions.length === 0) {
    throw new Error("Firmware input does not contain a valid ESP-IDF app description");
  }
  if (releaseVersions.length !== 1) {
    throw new Error("Firmware input does not contain one Veetee release version marker");
  }
  return {
    segmentCount,
    appVersion: descriptions[0],
    releaseVersion: releaseVersions[0],
  };
}
