/**
 * The window's Content Security Policy against what the window actually does.
 *
 * Tauri injects this policy as a header on its own protocol, so it applies in
 * the **packaged** application and not under `pnpm tauri dev`, where Vite
 * serves the page over http. Every CSP mistake is therefore invisible in
 * development and fatal after installing — which is how the viewer shipped
 * unable to load a mesh: `connect-src` did not permit `blob:`, three.js fetched
 * the blob URL the result screen had made for it, and WebKit refused with
 * "Load failed".
 *
 * These are contracts between two files, not copies of the policy string: each
 * asks what the source code needs and then whether the policy allows it.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const CONFIG = "src-tauri/tauri.conf.json";

function csp(): Map<string, string[]> {
  const config = JSON.parse(readFileSync(CONFIG, "utf8")) as {
    app: { security: { csp: string } };
  };
  const directives = new Map<string, string[]>();
  for (const part of config.app.security.csp.split(";")) {
    const [name, ...values] = part.trim().split(/\s+/);
    if (name) directives.set(name, values);
  }
  return directives;
}

/**
 * What a directive resolves to for this document. A missing fetch directive
 * falls back to `default-src`, so deleting `connect-src` would reintroduce the
 * same breakage and must fail the same way.
 */
function effective(name: string): string[] {
  const directives = csp();
  return directives.get(name) ?? directives.get("default-src") ?? [];
}

function sources(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.tsx?$/.test(entry) && !entry.endsWith(".test.ts")) files.push(path);
    }
  };
  walk("src");
  return files;
}

describe("the packaged window's content security policy", () => {
  it("allows fetching the blob URLs the window makes", () => {
    // The result screen fetches the mesh with the engine's bearer token and
    // hands three.js a blob URL, because a loader cannot carry that header.
    // three.js then fetches that URL — which is `connect-src`, not `img-src`.
    const makesBlobUrls = sources().filter((path) =>
      readFileSync(path, "utf8").includes("URL.createObjectURL"),
    );
    expect(makesBlobUrls.length).toBeGreaterThan(0);
    expect(effective("connect-src")).toContain("blob:");
  });

  it("allows talking to the engine on loopback", () => {
    // Ephemeral port, so the pattern has to be a wildcard; without this the
    // window cannot reach its own engine at all.
    expect(effective("connect-src")).toContain("http://127.0.0.1:*");
  });

  it("keeps the policy restrictive where nothing needs it", () => {
    // Guard rails on the fix above: widening `connect-src` must not turn into
    // widening everything.
    const directives = csp();
    expect(directives.get("default-src")).toEqual(["'self'"]);
    expect(directives.get("object-src")).toEqual(["'none'"]);
    expect(directives.get("script-src")).toEqual(["'self'"]);
    expect(effective("connect-src")).not.toContain("*");
  });
});
