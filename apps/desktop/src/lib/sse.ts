/**
 * Decoding a server-sent event stream.
 *
 * The browser's own `EventSource` cannot set request headers, and every engine
 * endpoint requires a bearer token — so the streams are read with `fetch` and a
 * `ReadableStream`, and the framing is done here.
 *
 * Network chunks have nothing to do with frame boundaries: one read can carry
 * half a frame, or three whole ones. The decoder therefore holds a buffer and
 * only ever yields frames it has seen the end of.
 */
export class SseDecoder {
  private buffer = "";

  /** Feed a chunk; get back the `data:` payload of every complete frame in it. */
  push(chunk: string): string[] {
    this.buffer += chunk;
    const payloads: string[] = [];

    for (;;) {
      const end = findFrameEnd(this.buffer);
      if (end === null) break;
      const frame = this.buffer.slice(0, end.start);
      this.buffer = this.buffer.slice(end.next);
      const payload = dataOf(frame);
      // A frame with no `data:` line is a comment — the keep-alive the engine
      // sends when a job is quiet. It is not an event.
      if (payload !== null) payloads.push(payload);
    }
    return payloads;
  }

  /** What is still buffered. A well-behaved stream ends here with nothing. */
  get pending(): string {
    return this.buffer;
  }
}

function findFrameEnd(buffer: string): { start: number; next: number } | null {
  const lf = buffer.indexOf("\n\n");
  const crlf = buffer.indexOf("\r\n\r\n");
  if (lf === -1 && crlf === -1) return null;
  if (crlf !== -1 && (lf === -1 || crlf < lf)) return { start: crlf, next: crlf + 4 };
  return { start: lf, next: lf + 2 };
}

function dataOf(frame: string): string | null {
  const lines = frame.split(/\r?\n/).filter((line) => line.startsWith("data:"));
  if (lines.length === 0) return null;
  // Per the specification a single leading space after the colon is part of
  // the framing, not of the value; anything beyond it is data.
  return lines.map((line) => line.slice(5).replace(/^ /, "")).join("\n");
}
