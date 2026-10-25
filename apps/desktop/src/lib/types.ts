/**
 * The shapes the engine returns.
 *
 * Deliberately a hand-written subset of the Python models rather than a
 * generated mirror: the window uses a fraction of the fields, and a generated
 * mirror would put every internal detail of the core into the UI's vocabulary.
 */

export type Verdict = "runnable" | "marginal" | "blocked" | "unknown";
export type Availability = "available" | "expected" | "unavailable" | "unknown";
export type InstallState = "installed" | "not_installed" | "incomplete";

export type JobStatus =
  | "queued"
  | "preprocessing"
  | "reconstructing"
  | "texturing"
  | "optimizing"
  | "validating"
  | "completed"
  | "failed"
  | "cancelled";

export const TERMINAL: readonly JobStatus[] = ["completed", "failed", "cancelled"];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL.includes(status);
}

export interface GpuInfo {
  name: string;
  vram_bytes: number | null;
  unified_memory: boolean;
}

export interface RuntimeStatus {
  kind: string;
  availability: Availability;
  detail?: string | null;
}

export interface HardwareInfo {
  os: string;
  os_version: string;
  arch: string;
  is_apple_silicon: boolean;
  cpu_name: string;
  cpu_cores_physical: number | null;
  cpu_cores_logical: number | null;
  ram_total_bytes: number | null;
  ram_available_bytes: number | null;
  unified_memory: boolean;
  gpus: GpuInfo[];
  metal: Availability;
  runtimes: RuntimeStatus[];
  disk_free_bytes: number | null;
  disk_total_bytes: number | null;
}

export interface Reason {
  message: string;
  blocking?: boolean;
}

export interface Assessment {
  provider_id: string;
  verdict: Verdict;
  reasons: Reason[];
  can_run?: boolean;
}

export interface DoctorReport {
  volum_version: string;
  hardware: HardwareInfo;
  recommended_runtime: string;
  assessments: Assessment[];
  warnings: string[];
}

export interface LicenseMetadata {
  code_license: string;
  weights_license: string;
  commercial_use: "allowed" | "conditional" | "not_allowed" | "unknown";
  commercial_use_detail?: string | null;
  territorial_restriction?: string | null;
  attribution_required?: string | null;
  dependency_licenses: Record<string, string>;
  source_url?: string | null;
  verified_on?: string | null;
}

export interface Requirements {
  supported_platforms: string[];
  supported_runtimes: string[];
  minimum_memory_bytes: number;
  estimated_peak_memory_bytes: number;
  disk_bytes: number;
  requires_gated_download: boolean;
  numbers_source?: string | null;
}

export interface ProviderMetadata {
  id: string;
  name: string;
  version: string;
  description: string;
  capabilities: string[];
  requirements: Requirements;
  license: LicenseMetadata;
  output_formats: string[];
}

export interface JobError {
  message: string;
  technical: string | null;
  suggestions: string[];
}

export interface InstallTask {
  model_id: string;
  state: "running" | "done" | "failed";
  step: string;
  message: string;
  error: JobError | null;
  started_at: string;
  finished_at: string | null;
}

export interface ModelEntry {
  metadata: ProviderMetadata;
  assessment: Assessment;
  install_state: InstallState;
  disk_bytes: number;
  has_implementation: boolean;
  install: InstallTask | null;
}

/** A preprocessing component. Not a generator — nothing here makes a mesh. */
export interface ToolMetadata {
  id: string;
  name: string;
  description: string;
  stage: string;
  license: LicenseMetadata;
  disk_bytes: number;
  numbers_source: string;
}

export interface ToolEntry {
  metadata: ToolMetadata;
  install_state: InstallState;
  disk_bytes: number;
  install: InstallTask | null;
}

export interface JobProgress {
  stage: JobStatus;
  fraction: number | null;
  message: string;
  at: string;
}

export interface JobRecord {
  id: string;
  status: JobStatus;
  model_id: string;
  model_version: string | null;
  runtime: string | null;
  input_files: string[];
  input_hashes: string[];
  /** What each input was called when it was chosen; the staged copies are UUIDs. */
  input_names: string[];
  parameters: Record<string, unknown>;
  seed: number | null;
  export_formats: string[];
  target_size_mm: number | null;
  single_part: boolean;
  /** Whether the inputs are measured and the best one reconstructed. */
  select_frame: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  artifacts: Record<string, string>;
  progress: JobProgress[];
  error: JobError | null;
}

export interface JobRequest {
  model_id: string;
  images: string[];
  seed?: number | null;
  parameters?: Record<string, unknown>;
  formats?: string[] | null;
  for_print?: boolean;
  target_size_mm?: number | null;
  single_part?: boolean;
  device?: string | null;
  /** `null` follows the setting. Explicit overrides one job, not every later one. */
  remove_background?: boolean | null;
}

/**
 * One candidate frame, measured.
 *
 * `score` and `rank` are properties of the set, not of the image: the same
 * photograph scores differently among different siblings. Everything above them
 * is a measurement of the file alone.
 */
export interface FrameMeasurement {
  name: string;
  path: string;
  /** Position in the submitted list — "frame 3 of 5". */
  index: number;
  width: number;
  height: number;
  stored_width: number;
  stored_height: number;
  size_bytes: number;
  format: string;
  /** Gradient energy at the size a model reads. Comparable within a set only. */
  detail: number;
  contrast: number;
  highlight_clipping: number;
  shadow_clipping: number;
  /** Exact subject share when the image already carries a matte. Never estimated. */
  alpha_coverage: number | null;
  has_exif: boolean;
  exif_orientation: number | null;
  needs_upright: boolean;
  fingerprint: string;
  score: number;
  rank: number;
  near_identical_to: string | null;
  notes: string[];
  /** Set when this one frame could not be measured. The others still were. */
  error: string | null;
}

/** Which frame was reconstructed, and why. */
export interface FrameSelection {
  outcome: "selected" | "only_one" | "failed";
  /** Index into the submitted list — the only thing that says the rest were not used. */
  chosen_index: number;
  chosen: string;
  reason: string;
  note: string;
  working_grid: number;
  frames: FrameMeasurement[];
  duration_seconds: number | null;
}

/** What the preprocessing stage did. Absent on assets made before it existed. */
export interface BackgroundRemoval {
  outcome: "removed" | "already_transparent" | "no_subject_found" | "not_installed" | "failed";
  note: string;
  model_id: string | null;
  model_revision: string | null;
  coverage: number | null;
  duration_seconds: number | null;
}

export interface JobSubmission {
  job: JobRecord;
  warnings: string[];
}

export interface SettingsView {
  data_dir: string;
  data_dir_is_default: boolean;
  hugging_face_token_set: boolean;
  allow_marginal_models: boolean;
  remove_background: boolean;
  /**
   * Whether the segmenter is on disk. This can be false while
   * `remove_background` is true, which is exactly the state worth showing:
   * asked for, not happening.
   */
  background_remover_installed: boolean;
}

export interface SettingsPatch {
  hugging_face_token?: string | null;
  clear_hugging_face_token?: boolean;
  allow_marginal_models?: boolean | null;
  remove_background?: boolean | null;
  data_dir?: string | null;
}
