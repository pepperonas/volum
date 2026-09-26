import { describe, expect, it } from "vitest";
import { bytes, duration, elapsed, fileName, jobLabel, since, stageWord } from "./format";

describe("bytes", () => {
  it("scales to the unit that keeps the number readable", () => {
    expect(bytes(2.5 * 1024 ** 3)).toBe("2.5 GB");
    expect(bytes(700 * 1024 ** 2)).toBe("700 MB");
    expect(bytes(12 * 1024)).toBe("12 KB");
  });

  it("drops the decimal once the number is large enough not to need it", () => {
    expect(bytes(15.12 * 1024 ** 3)).toBe("15 GB");
  });

  it("says unknown rather than showing a zero it cannot vouch for", () => {
    // The engine reports an undetectable value as null; rendering that as
    // "0 GB" would be a measurement the machine never made.
    expect(bytes(null)).toBe("unknown");
    expect(bytes(undefined)).toBe("unknown");
    expect(bytes(0)).toBe("0");
  });
});

describe("duration", () => {
  it("covers the range a generation actually takes", () => {
    expect(duration(0.116)).toBe("116 ms");
    expect(duration(5.24)).toBe("5.2 s");
    expect(duration(63.3)).toBe("1:03 min");
    expect(duration(3725)).toBe("1:02 h");
  });

  it("shows a dash when there is nothing to show", () => {
    expect(duration(null)).toBe("—");
  });
});

describe("since", () => {
  const now = Date.parse("2026-09-26T12:00:00Z");

  it("uses the coarsest unit that is still true", () => {
    expect(since("2026-09-26T11:59:40Z", now)).toBe("just now");
    expect(since("2026-09-26T11:30:00Z", now)).toBe("30 min ago");
    expect(since("2026-09-26T06:00:00Z", now)).toBe("6 h ago");
    expect(since("2026-09-25T12:00:00Z", now)).toBe("yesterday");
    expect(since("2026-09-20T12:00:00Z", now)).toBe("6 days ago");
  });

  it("never reports a negative age from a clock that ran backwards", () => {
    expect(since("2026-09-26T12:00:30Z", now)).toBe("just now");
  });

  it("does not guess at a value it cannot read", () => {
    expect(since(null, now)).toBe("—");
    expect(since("not a date", now)).toBe("—");
  });
});

describe("elapsed", () => {
  const now = Date.parse("2026-09-26T12:01:00Z");

  it("measures a finished job between its own timestamps", () => {
    expect(elapsed("2026-09-26T12:00:00Z", "2026-09-26T12:00:46Z", now)).toBe(46);
  });

  it("measures a running job up to now, so the window can tick", () => {
    expect(elapsed("2026-09-26T12:00:00Z", null, now)).toBe(60);
  });

  it("is null before a job has started", () => {
    expect(elapsed(null, null, now)).toBeNull();
  });
});

describe("fileName", () => {
  it("takes the base name whichever separator the platform uses", () => {
    expect(fileName("/Users/x/photos/horse.png")).toBe("horse.png");
    expect(fileName("C:\\Users\\x\\horse.png")).toBe("horse.png");
    expect(fileName("horse.png")).toBe("horse.png");
  });
});

describe("stageWord", () => {
  it("says what the stage means rather than its identifier", () => {
    expect(stageWord("reconstructing")).toBe("Reconstructing geometry");
  });

  it("passes an unknown stage through instead of hiding it", () => {
    // A provider reporting a stage this build does not know about should show
    // up, not vanish behind a blank.
    expect(stageWord("upscaling")).toBe("upscaling");
  });
});

describe("jobLabel", () => {
  const base = { id: "abc123def456", input_files: [], input_names: [] };

  it("uses the name the file had when it was chosen", () => {
    expect(
      jobLabel({ ...base, input_names: ["horse.png"], input_files: ["/data/jobs/x/input/9f2.png"] }),
    ).toBe("horse.png");
  });

  it("falls back to the staged file for jobs made before names were kept", () => {
    // Records written by an earlier build have no input_names. Showing them as
    // nameless would be worse than showing the copy's name.
    expect(jobLabel({ ...base, input_files: ["/data/jobs/x/input/9f2.png"] })).toBe("9f2.png");
  });

  it("names a job with no inputs at all by its id", () => {
    expect(jobLabel(base)).toBe("job abc123de");
  });
});
