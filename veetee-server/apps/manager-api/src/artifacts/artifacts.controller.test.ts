import { PATH_METADATA } from "@nestjs/common/constants";
import { describe, expect, it } from "vitest";

import { ArtifactsController } from "./artifacts.controller.js";

describe("ArtifactsController routes", () => {
  it("exposes only canonical Veetee artifact routes", () => {
    expect(Reflect.getMetadata(PATH_METADATA, ArtifactsController.prototype.manifest)).toBe(
      "veetee/artifacts/manifests/:id",
    );
    expect(Reflect.getMetadata(PATH_METADATA, ArtifactsController.prototype.content)).toBe(
      "veetee/artifacts/:id/content",
    );
  });
});
