import { describe, expect, it } from "vitest";
import { LOOKS } from "../looks";
import { VIEWS, viewById } from "./index";

describe("registries", () => {
  it("every view and look has a unique id and the River is the default", () => {
    expect(new Set(VIEWS.map((v) => v.id)).size).toBe(VIEWS.length);
    expect(new Set(LOOKS.map((l) => l.id)).size).toBe(LOOKS.length);
    expect(VIEWS.length).toBe(8);
    expect(viewById("nope").id).toBe("river");
    expect(VIEWS.filter((v) => v.status === "ready").length).toBe(8);
  });
});
