/** Turning engine numbers into something a person reads without effort. */

/**
 * Bytes as GB/MB.
 *
 * Binary units, because that is what the engine measures in and what a disk
 * dialog shows; labelling them "GB" matches every other tool on the machine
 * even though the strict name is GiB.
 */
export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unknown";
  if (value === 0) return "0";
  const gb = value / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(gb >= 10 ? 0 : 1)} GB`;
  const mb = value / 1024 ** 2;
  if (mb >= 1) return `${mb.toFixed(0)} MB`;
  return `${(value / 1024).toFixed(0)} KB`;
}

/** Seconds as a duration a person reads at a glance. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes}:${String(rest).padStart(2, "0")} min`;
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")} h`;
}

/** How long ago, in the coarsest unit that is still true. */
export function since(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, (now - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  const days = Math.round(seconds / 86_400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/** Elapsed seconds between two timestamps, or from a start until now. */
export function elapsed(startedAt: string | null, finishedAt: string | null, now = Date.now()) {
  if (!startedAt) return null;
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return null;
  const end = finishedAt ? Date.parse(finishedAt) : now;
  return Math.max(0, (end - start) / 1000);
}

/** The base name of a path, whichever separator it uses. */
export function fileName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? path;
}

/**
 * What to call a job.
 *
 * The staged inputs are named by UUID — deliberately, they are copies inside
 * the job — so the readable name comes from what the user picked.
 */
export function jobLabel(job: { input_names: string[]; input_files: string[]; id: string }): string {
  return job.input_names[0] ?? (job.input_files[0] ? fileName(job.input_files[0]) : `job ${job.id.slice(0, 8)}`);
}

/** Stage names as the window says them (spec §18: stages, never a made-up %). */
const STAGE_WORDS: Record<string, string> = {
  queued: "Waiting",
  preprocessing: "Preparing images",
  reconstructing: "Reconstructing geometry",
  texturing: "Generating materials",
  optimizing: "Optimising the mesh",
  validating: "Checking the model",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function stageWord(stage: string): string {
  return STAGE_WORDS[stage] ?? stage;
}
