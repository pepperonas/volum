import { useCallback, useEffect, useRef, useState } from "react";
import { EngineError, engineInfo, type EngineInfo } from "./engine";

export type Async<T> =
  // Every arm names both fields, one of them as `undefined`. Without that, the
  // union has no common `value` and the discriminant cannot be used to reach
  // it — a caller would have to switch before it could read anything.
  | { state: "loading"; value?: T; error?: undefined }
  | { state: "ready"; value: T; error?: undefined }
  | { state: "failed"; value?: undefined; error: EngineError };

interface Entry<T> {
  key: string;
  result: Async<T>;
}

/**
 * Run an async load, and re-run it on demand.
 *
 * The load is identified by a `key` rather than by a dependency array. An
 * array built at the call site cannot be checked — neither by React nor by a
 * linter — and the loader's own identity changes on every render, so tying the
 * effect to it would restart the load for ever. A key says plainly what the
 * load depends on.
 *
 * "Loading" is *derived* from whether the stored answer belongs to the current
 * key, not stored alongside it. Setting it from inside the effect would be a
 * second render for something already known at render time.
 *
 * A reload keeps the previous value in hand, so a refreshing list does not
 * blink back to a spinner — that flicker is what makes a polling screen
 * unreadable.
 */
export function useAsync<T>(
  key: string,
  load: () => Promise<T>,
): Async<T> & { reload: () => void } {
  // The latest loader, without making the effect depend on its identity — a
  // loader written inline at the call site is a new function every render, so
  // an effect that depended on it would restart the load for ever.
  //
  // Updated from an effect rather than during render, and declared *before*
  // the effect that reads it: effects run in the order they are written, so
  // the loader is current by the time the load starts.
  const latest = useRef(load);
  useEffect(() => {
    latest.current = load;
  });

  const [nonce, setNonce] = useState(0);
  const [entry, setEntry] = useState<Entry<T> | null>(null);

  useEffect(() => {
    let alive = true;
    latest
      .current()
      .then((value) => {
        if (alive) setEntry({ key, result: { state: "ready", value } });
      })
      .catch((error: unknown) => {
        if (alive) setEntry({ key, result: { state: "failed", error: asEngineError(error) } });
      });
    return () => {
      // A slower earlier load must never overwrite a newer one.
      alive = false;
    };
  }, [key, nonce]);

  const result: Async<T> =
    entry?.key === key ? entry.result : { state: "loading", value: entry?.result.value };

  return { ...result, reload: useCallback(() => setNonce((n) => n + 1), []) };
}

export function asEngineError(error: unknown): EngineError {
  return error instanceof EngineError
    ? error
    : new EngineError("Something went wrong in VOLUM.", { technical: String(error) });
}

/** The engine's address, resolved once for the whole window. */
export function useEngineBoot(): Async<EngineInfo> & { retry: () => void } {
  const { reload, ...rest } = useAsync("engine", engineInfo);
  return { ...rest, retry: reload };
}

/** A value that ticks, for durations that must count up while a job runs. */
export function useTicker(active: boolean, everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const handle = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(handle);
  }, [active, everyMs]);
  return now;
}
