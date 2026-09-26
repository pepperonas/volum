import { describe, expect, it } from "vitest";
import { SseDecoder } from "./sse";

describe("SseDecoder", () => {
  it("yields the payload of a complete frame", () => {
    expect(new SseDecoder().push('data: {"a":1}\n\n')).toEqual(['{"a":1}']);
  });

  it("waits for a frame that arrives in pieces", () => {
    // The usual case on a real socket: a job record is larger than one chunk.
    const decoder = new SseDecoder();
    expect(decoder.push('data: {"status":"reco')).toEqual([]);
    expect(decoder.push('nstructing"}')).toEqual([]);
    expect(decoder.push("\n\n")).toEqual(['{"status":"reconstructing"}']);
  });

  it("yields every frame in a chunk that carries several", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("data: one\n\ndata: two\n\ndata: three\n\n")).toEqual([
      "one",
      "two",
      "three",
    ]);
  });

  it("does not report a keep-alive as an event", () => {
    // The engine sends a comment after a quiet stretch so the connection is
    // not dropped. Turning that into an empty job update would be a bug.
    const decoder = new SseDecoder();
    expect(decoder.push(": keep-alive\n\n")).toEqual([]);
    expect(decoder.push("data: real\n\n")).toEqual(["real"]);
  });

  it("joins a payload spread over several data lines", () => {
    expect(new SseDecoder().push("data: first\ndata: second\n\n")).toEqual(["first\nsecond"]);
  });

  it("keeps everything after the single framing space", () => {
    expect(new SseDecoder().push("data:  padded \n\n")).toEqual([" padded "]);
  });

  it("understands CRLF framing", () => {
    expect(new SseDecoder().push("data: x\r\n\r\n")).toEqual(["x"]);
  });

  it("holds an incomplete tail rather than guessing", () => {
    const decoder = new SseDecoder();
    decoder.push("data: complete\n\ndata: half");
    expect(decoder.pending).toBe("data: half");
  });
});
