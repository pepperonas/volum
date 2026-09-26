//! The desktop shell.
//!
//! It owns the window and the engine's lifetime and holds no pipeline logic
//! (`docs/architecture.md` §2). The webview is given the engine's address and
//! session token and talks to it directly over HTTP; the shell never proxies,
//! and the webview is never granted the ability to spawn anything.

pub mod engine;

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use tauri::async_runtime::Mutex as AsyncMutex;
use tauri::{Manager, State};

/// Long enough for a cold `uv run` to resolve an environment on a busy
/// machine; short enough that a genuinely stuck engine is reported rather than
/// waited on for ever.
const START_TIMEOUT: Duration = Duration::from_secs(180);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(60);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);

/// In a development build the engine can be run straight out of the checkout.
/// A release build has no such path — it uses its bundled sidecar or fails.
#[cfg(debug_assertions)]
const WORKSPACE_ENGINE_DIR: Option<&str> =
    Some(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../engine"));
#[cfg(not(debug_assertions))]
const WORKSPACE_ENGINE_DIR: Option<&str> = None;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineInfo {
    pub base_url: String,
    /// The webview needs this to call the engine. It exists only in memory, on
    /// both sides, for the life of the session.
    pub token: String,
    pub version: String,
    pub pid: u32,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineFailure {
    pub message: String,
    pub detail: String,
    pub suggestions: Vec<String>,
}

impl EngineFailure {
    fn new(message: impl Into<String>, detail: impl Into<String>, suggestions: &[&str]) -> Self {
        Self {
            message: message.into(),
            detail: detail.into(),
            suggestions: suggestions.iter().map(|s| (*s).to_string()).collect(),
        }
    }
}

#[derive(Default)]
pub struct EngineSupervisor {
    /// The outcome of starting, computed once. Starting twice would leave an
    /// orphaned engine holding the GPU.
    outcome: AsyncMutex<Option<Result<EngineInfo, EngineFailure>>>,
    running: std::sync::Mutex<Option<engine::StartedEngine>>,
}

impl EngineSupervisor {
    pub async fn info(&self) -> Result<EngineInfo, EngineFailure> {
        let mut slot = self.outcome.lock().await;
        if let Some(cached) = slot.as_ref() {
            return cached.clone();
        }
        let outcome = self.launch().await;
        *slot = Some(outcome.clone());
        outcome
    }

    async fn launch(&self) -> Result<EngineInfo, EngineFailure> {
        let command = resolve()?;
        let token = engine::generate_token();

        let started = {
            let command = command.clone();
            let token = token.clone();
            tauri::async_runtime::spawn_blocking(move || {
                engine::start(&command, &token, START_TIMEOUT)
            })
            .await
            .map_err(|join| {
                EngineFailure::new(
                    "VOLUM could not start its engine.",
                    format!("the start task failed: {join}"),
                    &[],
                )
            })?
        }
        .map_err(|error| {
            EngineFailure::new(
                error.message(),
                error.detail(),
                &[
                    "Check that the engine is installed: `uv sync` in the engine directory.",
                    "Start VOLUM from a terminal to see the engine's own output.",
                ],
            )
        })?;

        let info = EngineInfo {
            base_url: started.base_url(),
            token: started.token.clone(),
            version: started.announcement.version.clone(),
            pid: started.announcement.pid,
        };

        // The announcement means the socket is bound; it does not mean the
        // server is accepting yet. The window says "starting" until it is.
        wait_until_healthy(&info.base_url, &info.token, HEALTH_TIMEOUT)
            .await
            .map_err(|detail| {
                EngineFailure::new(
                    "The engine started but did not become ready.",
                    detail,
                    &["Restart VOLUM.", "Check the engine log for the reason."],
                )
            })?;

        if let Ok(mut slot) = self.running.lock() {
            *slot = Some(started);
        }
        Ok(info)
    }

    /// Stop the engine. Safe to call more than once.
    pub fn shutdown(&self) {
        if let Ok(mut slot) = self.running.lock() {
            if let Some(mut engine) = slot.take() {
                engine.shutdown(SHUTDOWN_GRACE);
            }
        }
    }
}

fn resolve() -> Result<engine::EngineCommand, EngineFailure> {
    let explicit = std::env::var(engine::COMMAND_ENV).ok();
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."));
    let workspace = WORKSPACE_ENGINE_DIR.map(PathBuf::from);

    engine::resolve_command(
        explicit.as_deref(),
        &exe_dir,
        workspace.as_deref(),
        &|path| path.exists(),
    )
    .map_err(|error| match error {
        engine::ResolveError::BadExplicit(detail) => EngineFailure::new(
            "VOLUM was told to start its engine in a way it does not understand.",
            detail,
            &["Unset VOLUM_ENGINE_COMMAND to use the bundled engine."],
        ),
        engine::ResolveError::NothingFound => EngineFailure::new(
            "VOLUM could not find its engine.",
            format!(
                "no {} next to {}, and no development checkout",
                engine::sidecar_file_name(),
                exe_dir.display()
            ),
            &["Reinstall VOLUM — the engine is missing from this installation."],
        ),
    })
}

async fn wait_until_healthy(base_url: &str, token: &str, timeout: Duration) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|error| error.to_string())?;
    let deadline = std::time::Instant::now() + timeout;
    let mut last = String::from("no attempt was made");

    while std::time::Instant::now() < deadline {
        match client
            .get(format!("{base_url}/health"))
            .bearer_auth(token)
            .send()
            .await
        {
            Ok(response) if response.status().is_success() => return Ok(()),
            // A 401 here would mean the shell and the engine disagree about the
            // token, which retrying cannot fix.
            Ok(response) if response.status().as_u16() == 401 => {
                return Err("the engine rejected the session token".to_string())
            }
            Ok(response) => last = format!("HTTP {}", response.status()),
            Err(error) => last = error.to_string(),
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Err(format!("gave up after {}s: {last}", timeout.as_secs()))
}

/// Copy one of a job's artifacts to a place the user picked.
///
/// The download happens here rather than in the window because the window has
/// no filesystem permission at all — the capability manifest grants it a file
/// *picker* and nothing else. The destination comes from the system dialog, so
/// it is the user's own choice; the job id and artifact name come from the
/// window, so they are checked before they reach a URL.
#[tauri::command]
async fn save_artifact(
    supervisor: State<'_, EngineSupervisor>,
    job_id: String,
    name: String,
    destination: String,
) -> Result<u64, EngineFailure> {
    if !job_id.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(EngineFailure::new(
            "That job cannot be saved.",
            format!("invalid job id: {job_id}"),
            &[],
        ));
    }
    if !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return Err(EngineFailure::new(
            "That file cannot be saved.",
            format!("invalid artifact name: {name}"),
            &[],
        ));
    }

    let engine = supervisor.info().await?;
    let response = reqwest::Client::new()
        .get(format!(
            "{}/api/jobs/{job_id}/artifacts/{name}",
            engine.base_url
        ))
        .bearer_auth(&engine.token)
        .send()
        .await
        .map_err(|error| {
            EngineFailure::new(
                "VOLUM could not read the file from its engine.",
                error.to_string(),
                &[],
            )
        })?;

    if !response.status().is_success() {
        return Err(EngineFailure::new(
            "That file is no longer available.",
            format!("the engine answered {}", response.status()),
            &["The job directory may have been deleted."],
        ));
    }

    let body = response.bytes().await.map_err(|error| {
        EngineFailure::new(
            "The file could not be read to the end.",
            error.to_string(),
            &[],
        )
    })?;

    std::fs::write(&destination, &body).map_err(|error| {
        EngineFailure::new(
            "VOLUM could not write the file.",
            format!("{destination}: {error}"),
            &["Choose a different location."],
        )
    })?;
    Ok(body.len() as u64)
}

#[tauri::command]
async fn engine_info(supervisor: State<'_, EngineSupervisor>) -> Result<EngineInfo, EngineFailure> {
    supervisor.info().await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineSupervisor::default())
        .invoke_handler(tauri::generate_handler![engine_info, save_artifact])
        .build(tauri::generate_context!())
        .expect("error while building the VOLUM window")
        .run(|app, event| {
            // The engine must not outlive the window. Dropping managed state at
            // exit is not something to rely on, so this is explicit.
            if let tauri::RunEvent::Exit = event {
                app.state::<EngineSupervisor>().shutdown();
            }
        });
}
