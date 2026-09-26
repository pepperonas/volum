//! Supervising the engine sidecar.
//!
//! The shell owns the engine's lifetime (`docs/architecture.md` §2): it mints a
//! session token, starts the process, learns the port from the child's stdout,
//! and takes the engine down with it. No pipeline logic lives here.

use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;

/// The engine reads its session token from here. Never a command-line argument:
/// arguments are visible in the process list to every user on the machine.
pub const TOKEN_ENV: &str = "VOLUM_ENGINE_TOKEN";

/// Developer escape hatch: a JSON array naming the command to run instead.
pub const COMMAND_ENV: &str = "VOLUM_ENGINE_COMMAND";

/// Keeps the bundled interpreter from writing bytecode into its own bundle.
///
/// An application bundle is code-signed and sealed. The first run of an
/// unprotected build wrote 1,280 `.pyc` files into `Contents/Resources` and
/// macOS reported the application as damaged from then on. The bundle ships
/// with its bytecode already compiled, so nothing is lost by forbidding it.
pub const NO_BYTECODE_ENV: &str = "PYTHONDONTWRITEBYTECODE";

/// Where the engine finds the `uv` it builds provider environments with.
/// Without this a bundled application would look on `PATH`, which it has no
/// business relying on.
pub const UV_ENV: &str = "VOLUM_UV_BINARY";

/// How much of the engine's stderr to keep for error messages.
const STDERR_KEEP_BYTES: usize = 8 * 1024;

// --- the announcement ------------------------------------------------------

/// The one line the engine prints on stdout once it is listening.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Announcement {
    pub host: String,
    pub port: u16,
    pub pid: u32,
    pub version: String,
}

impl Announcement {
    pub fn base_url(&self) -> String {
        format!("http://{}:{}", self.host, self.port)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum AnnouncementError {
    /// Not JSON at all — the reader treats this as ordinary output and moves on.
    NotJson,
    /// JSON, but not the announcement.
    WrongEvent(String),
    MissingField(&'static str),
    /// A port of 0 is never a listening port.
    InvalidPort(i64),
    /// The engine must bind loopback. Anything else is refused rather than used.
    NotLoopback(String),
}

/// Parse one line of the engine's stdout.
pub fn parse_announcement(line: &str) -> Result<Announcement, AnnouncementError> {
    let value: serde_json::Value =
        serde_json::from_str(line.trim()).map_err(|_| AnnouncementError::NotJson)?;
    let object = value.as_object().ok_or(AnnouncementError::NotJson)?;

    let event = object
        .get("event")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    if event != "listening" {
        return Err(AnnouncementError::WrongEvent(event.to_string()));
    }

    let host = object
        .get("host")
        .and_then(|v| v.as_str())
        .ok_or(AnnouncementError::MissingField("host"))?;
    if !is_loopback(host) {
        return Err(AnnouncementError::NotLoopback(host.to_string()));
    }

    let port = object
        .get("port")
        .and_then(serde_json::Value::as_i64)
        .ok_or(AnnouncementError::MissingField("port"))?;
    if !(1..=65535).contains(&port) {
        return Err(AnnouncementError::InvalidPort(port));
    }

    let pid = object
        .get("pid")
        .and_then(serde_json::Value::as_u64)
        .ok_or(AnnouncementError::MissingField("pid"))?;
    let version = object
        .get("version")
        .and_then(|v| v.as_str())
        .ok_or(AnnouncementError::MissingField("version"))?;

    Ok(Announcement {
        host: host.to_string(),
        port: port as u16,
        pid: pid as u32,
        version: version.to_string(),
    })
}

/// Whether a host is a literal loopback address.
///
/// Literal addresses only — `localhost` is a *name*, and a name is resolved by
/// the machine's host file, which is not something the shell should trust when
/// it is deciding where to send a session token.
fn is_loopback(host: &str) -> bool {
    host.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

// --- the session token -----------------------------------------------------

/// A fresh 256-bit session token as lowercase hex.
///
/// Panics if the operating system cannot provide randomness. That is the right
/// response: the alternative is a weaker token, and an engine guarded by a
/// guessable token is worse than one that refuses to start.
pub fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("the operating system must provide randomness");
    let mut out = String::with_capacity(64);
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

// --- finding the engine ----------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EngineCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
    /// Extra environment for the child, on top of this process's own.
    pub env: Vec<(String, String)>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ResolveError {
    BadExplicit(String),
    NothingFound,
}

/// Decide how to start the engine.
///
/// Order: the explicit override, then the runtime bundled with the
/// application, then — in a development build only — `uv run` in the
/// repository's engine directory. A packaged application that happens to sit
/// inside a checkout must still use its own bundled runtime, which is why
/// bundled comes before the workspace.
///
/// The bundled runtime is a relocatable CPython with the engine installed into
/// it (`docs/adr/0003-packaging-runtime.md`), so it is started as a module
/// rather than through the `volum-engine` console script: a console script
/// carries the absolute shebang written at install time, which points at the
/// machine that built it.
pub fn resolve_command(
    explicit: Option<&str>,
    runtime_dir: Option<&Path>,
    workspace_engine_dir: Option<&Path>,
    exists: &dyn Fn(&Path) -> bool,
) -> Result<EngineCommand, ResolveError> {
    // Always appended, never optional: without it a crashed shell leaves an
    // engine behind, still holding the GPU.
    const EXIT_WITH_PARENT: &str = "--exit-with-parent";

    if let Some(raw) = explicit {
        let parts: Vec<String> = serde_json::from_str(raw).map_err(|_| bad_explicit(raw))?;
        let (program, rest) = parts.split_first().ok_or_else(|| bad_explicit(raw))?;
        let mut args: Vec<String> = rest.to_vec();
        args.push(EXIT_WITH_PARENT.to_string());
        return Ok(EngineCommand {
            program: PathBuf::from(program),
            args,
            cwd: None,
            env: Vec::new(),
        });
    }

    if let Some(runtime) = runtime_dir {
        let python = runtime.join(runtime_python());
        if exists(&python) {
            return Ok(EngineCommand {
                program: python,
                args: vec![
                    "-m".into(),
                    "volum_engine.main".into(),
                    EXIT_WITH_PARENT.into(),
                ],
                cwd: None,
                env: vec![
                    (
                        UV_ENV.to_string(),
                        runtime.join(runtime_uv()).display().to_string(),
                    ),
                    (NO_BYTECODE_ENV.to_string(), "1".to_string()),
                ],
            });
        }
    }

    if let Some(engine_dir) = workspace_engine_dir {
        if exists(engine_dir) {
            return Ok(EngineCommand {
                program: PathBuf::from("uv"),
                args: vec!["run".into(), "volum-engine".into(), EXIT_WITH_PARENT.into()],
                cwd: Some(engine_dir.to_path_buf()),
                env: Vec::new(),
            });
        }
    }

    Err(ResolveError::NothingFound)
}

fn bad_explicit(raw: &str) -> ResolveError {
    ResolveError::BadExplicit(format!(
        "{COMMAND_ENV} must be a non-empty JSON array of strings, \
         for example [\"uv\",\"run\",\"volum-engine\"]. Got: {raw}"
    ))
}

/// The interpreter inside the bundled runtime, relative to the runtime root.
///
/// `python3` rather than `python3.12`: the unversioned link is what survives a
/// bump of the bundled interpreter without a matching change here.
pub fn runtime_python() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from("python.exe")
    } else {
        PathBuf::from("bin").join("python3")
    }
}

/// `uv` inside the bundled runtime, relative to the runtime root.
pub fn runtime_uv() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from("Scripts").join("uv.exe")
    } else {
        PathBuf::from("bin").join("uv")
    }
}

// --- starting it -----------------------------------------------------------

pub struct StartedEngine {
    pub announcement: Announcement,
    pub token: String,
    child: Child,
    /// Held open deliberately: the engine runs with `--exit-with-parent` and
    /// stops when this pipe closes. Dropping it stops the engine.
    stdin: Option<ChildStdin>,
}

#[derive(Debug)]
pub enum StartError {
    Spawn {
        program: String,
        source: std::io::Error,
    },
    Timeout {
        seconds: u64,
        stderr: String,
    },
    Died {
        status: Option<i32>,
        stderr: String,
    },
    Refused {
        reason: AnnouncementError,
        line: String,
    },
}

impl StartError {
    /// What a person is shown. The detail belongs in the log, not here.
    pub fn message(&self) -> String {
        match self {
            StartError::Spawn { program, .. } => {
                format!("VOLUM could not start its engine ({program} could not be run).")
            }
            StartError::Timeout { seconds, .. } => {
                format!("The engine did not start within {seconds} seconds.")
            }
            StartError::Died { .. } => "The engine stopped while starting up.".to_string(),
            StartError::Refused { .. } => {
                "The engine announced itself in a way VOLUM will not accept.".to_string()
            }
        }
    }

    /// The full technical detail, for the log and the diagnostics view.
    pub fn detail(&self) -> String {
        match self {
            StartError::Spawn { program, source } => format!("spawn {program}: {source}"),
            StartError::Timeout { stderr, .. } => stderr.clone(),
            StartError::Died { status, stderr } => match status {
                Some(code) => format!("exit code {code}\n{stderr}"),
                None => format!("terminated by a signal\n{stderr}"),
            },
            StartError::Refused { reason, line } => format!("{reason:?} in: {line}"),
        }
    }
}

impl std::fmt::Display for StartError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message())
    }
}

/// Start the engine and wait for its announcement.
pub fn start(
    command: &EngineCommand,
    token: &str,
    timeout: Duration,
) -> Result<StartedEngine, StartError> {
    let mut builder = Command::new(&command.program);
    builder
        .args(&command.args)
        // The token travels in the environment, never in argv.
        .env(TOKEN_ENV, token)
        .envs(command.env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        // stdin is piped and then held: closing it is how the engine learns
        // that this process is gone.
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(cwd) = &command.cwd {
        builder.current_dir(cwd);
    }

    let mut child = builder.spawn().map_err(|source| StartError::Spawn {
        program: command.program.display().to_string(),
        source,
    })?;

    let stdin = child.stdin.take();
    let stdout = child.stdout.take().expect("stdout was piped");
    let stderr = child.stderr.take().expect("stderr was piped");

    let collected = Arc::new(Mutex::new(String::new()));
    spawn_stderr_collector(stderr, Arc::clone(&collected));

    // The reader keeps draining for the life of the process even once nobody
    // is listening: a full stdout pipe would block the engine mid-job.
    let (lines, incoming) = mpsc::channel::<String>();
    std::thread::Builder::new()
        .name("volum-engine-stdout".into())
        .spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(line) => {
                        let _ = lines.send(line);
                    }
                    Err(_) => break,
                }
            }
        })
        .expect("a thread for the engine's stdout");

    let deadline = std::time::Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(std::time::Instant::now());
        if remaining.is_zero() {
            kill(&mut child);
            return Err(StartError::Timeout {
                seconds: timeout.as_secs(),
                stderr: snapshot(&collected),
            });
        }
        match incoming.recv_timeout(remaining) {
            Ok(line) => match parse_announcement(&line) {
                Ok(announcement) => {
                    return Ok(StartedEngine {
                        announcement,
                        token: token.to_string(),
                        child,
                        stdin,
                    })
                }
                // Model libraries and package managers print to stdout
                // uninvited. Anything that is not the announcement is noise.
                Err(AnnouncementError::NotJson) | Err(AnnouncementError::WrongEvent(_)) => continue,
                // A malformed *announcement* is different: the engine claims to
                // be listening somewhere VOLUM will not talk to.
                Err(reason) => {
                    kill(&mut child);
                    return Err(StartError::Refused { reason, line });
                }
            },
            Err(mpsc::RecvTimeoutError::Timeout) => {
                kill(&mut child);
                return Err(StartError::Timeout {
                    seconds: timeout.as_secs(),
                    stderr: snapshot(&collected),
                });
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                // stdout closed without an announcement: the engine died.
                let status = child.wait().ok().and_then(|status| status.code());
                return Err(StartError::Died {
                    status,
                    stderr: snapshot(&collected),
                });
            }
        }
    }
}

impl StartedEngine {
    pub fn base_url(&self) -> String {
        self.announcement.base_url()
    }

    /// Stop the engine: close its stdin, give it a moment, then kill it.
    ///
    /// The polite half is what `--exit-with-parent` listens for. The impolite
    /// half is what makes "stopped" true even when the engine is wedged inside
    /// a GPU kernel — the same reason a cancelled job escalates to SIGKILL.
    pub fn shutdown(&mut self, grace: Duration) {
        self.stdin.take();
        let deadline = std::time::Instant::now() + grace;
        while std::time::Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
                Err(_) => break,
            }
        }
        kill(&mut self.child);
    }
}

impl Drop for StartedEngine {
    fn drop(&mut self) {
        self.shutdown(Duration::from_secs(5));
    }
}

fn kill(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn snapshot(collected: &Arc<Mutex<String>>) -> String {
    collected
        .lock()
        .map(|text| text.clone())
        .unwrap_or_else(|poisoned| poisoned.into_inner().clone())
}

fn spawn_stderr_collector(stderr: impl Read + Send + 'static, into: Arc<Mutex<String>>) {
    std::thread::Builder::new()
        .name("volum-engine-stderr".into())
        .spawn(move || {
            for line in BufReader::new(stderr).lines() {
                let Ok(line) = line else { break };
                eprintln!("[engine] {line}");
                let Ok(mut buffer) = into.lock() else { break };
                buffer.push_str(&line);
                buffer.push('\n');
                // Keep the tail: the useful part of a crash is the end.
                if buffer.len() > STDERR_KEEP_BYTES {
                    let cut = buffer.len() - STDERR_KEEP_BYTES;
                    // Never split a character in half.
                    let cut = (cut..buffer.len())
                        .find(|i| buffer.is_char_boundary(*i))
                        .unwrap_or(buffer.len());
                    buffer.drain(..cut);
                }
            }
        })
        .expect("a thread for the engine's stderr");
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- parse_announcement ------------------------------------------------

    fn good_line() -> String {
        r#"{"event":"listening","host":"127.0.0.1","port":54321,"pid":4242,"version":"0.1.0"}"#
            .to_string()
    }

    #[test]
    fn it_parses_the_engine_s_announcement() {
        let a = parse_announcement(&good_line()).expect("should parse");
        assert_eq!(a.host, "127.0.0.1");
        assert_eq!(a.port, 54321);
        assert_eq!(a.pid, 4242);
        assert_eq!(a.version, "0.1.0");
        assert_eq!(a.base_url(), "http://127.0.0.1:54321");
    }

    #[test]
    fn unknown_fields_are_accepted() {
        // The contract is "fields are only ever added". A newer engine talking
        // to an older shell must still start.
        let line = r#"{"event":"listening","host":"127.0.0.1","port":1,"pid":2,
                       "version":"9.9.9","runtime":"mps","extra":{"a":1}}"#;
        assert_eq!(parse_announcement(line).expect("should parse").port, 1);
    }

    #[test]
    fn ordinary_output_is_not_an_announcement() {
        // Libraries print to stdout uninvited; the reader must keep going.
        for line in ["", "   ", "Loading weights...", "[INFO] ready"] {
            assert_eq!(parse_announcement(line), Err(AnnouncementError::NotJson));
        }
    }

    #[test]
    fn another_json_event_is_not_an_announcement() {
        let line = r#"{"event":"progress","port":1}"#;
        assert_eq!(
            parse_announcement(line),
            Err(AnnouncementError::WrongEvent("progress".into()))
        );
    }

    #[test]
    fn a_missing_field_is_named() {
        let line = r#"{"event":"listening","host":"127.0.0.1","pid":2,"version":"1"}"#;
        assert_eq!(
            parse_announcement(line),
            Err(AnnouncementError::MissingField("port"))
        );
    }

    #[test]
    fn port_zero_is_refused() {
        let line = r#"{"event":"listening","host":"127.0.0.1","port":0,"pid":2,"version":"1"}"#;
        assert_eq!(
            parse_announcement(line),
            Err(AnnouncementError::InvalidPort(0))
        );
    }

    #[test]
    fn a_non_loopback_host_is_refused_rather_than_used() {
        // The engine binding a routable interface would be a serious defect.
        // The shell refuses to connect instead of quietly accepting it.
        for host in ["0.0.0.0", "192.168.1.5", "example.com", ""] {
            let line = format!(
                r#"{{"event":"listening","host":"{host}","port":1,"pid":2,"version":"1"}}"#
            );
            assert_eq!(
                parse_announcement(&line),
                Err(AnnouncementError::NotLoopback(host.to_string())),
                "host {host} must be refused"
            );
        }
    }

    #[test]
    fn ipv6_loopback_is_accepted() {
        let line = r#"{"event":"listening","host":"::1","port":7,"pid":2,"version":"1"}"#;
        assert_eq!(parse_announcement(line).expect("should parse").host, "::1");
    }

    // --- generate_token ----------------------------------------------------

    #[test]
    fn a_token_is_64_lowercase_hex_characters() {
        let t = generate_token();
        assert_eq!(t.len(), 64, "256 bits as hex");
        assert!(
            t.chars()
                .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c)),
            "not lowercase hex: {t}"
        );
    }

    #[test]
    fn tokens_do_not_repeat() {
        let mut seen = std::collections::HashSet::new();
        for _ in 0..256 {
            assert!(seen.insert(generate_token()), "a token repeated");
        }
    }

    // --- resolve_command ---------------------------------------------------

    fn nothing_exists(_: &Path) -> bool {
        false
    }

    #[test]
    fn an_explicit_command_wins() {
        let cmd = resolve_command(
            Some(r#"["uv","run","volum-engine"]"#),
            Some(Path::new("/bundle/runtime")),
            Some(Path::new("/repo/engine")),
            &|_| true,
        )
        .expect("should resolve");
        assert_eq!(cmd.program, PathBuf::from("uv"));
        // The command is taken verbatim; the exit-with-parent flag is appended,
        // which is why this checks the prefix rather than the whole list.
        assert_eq!(&cmd.args[..2], ["run", "volum-engine"]);
    }

    #[test]
    fn an_explicit_command_must_be_a_json_array_of_strings() {
        // A whitespace-split string would break on any path containing a space,
        // which on macOS is most of them. The error says the expected form.
        for bad in ["uv run volum-engine", "[]", "[1,2]", "{\"a\":1}", "["] {
            match resolve_command(Some(bad), None, None, &nothing_exists) {
                Err(ResolveError::BadExplicit(message)) => {
                    assert!(
                        message.contains('['),
                        "message should show the form: {message}"
                    );
                }
                other => panic!("{bad:?} should be rejected, got {other:?}"),
            }
        }
    }

    #[test]
    fn the_bundled_runtime_is_used_when_present() {
        let runtime = Path::new("/Applications/VOLUM.app/Contents/Resources/runtime");
        let python = runtime.join(runtime_python());
        let cmd = resolve_command(None, Some(runtime), Some(Path::new("/repo/engine")), &|p| {
            p == python
        })
        .expect("should resolve");
        assert_eq!(cmd.program, python);
        assert!(cmd.args.iter().any(|a| a == "--exit-with-parent"));
        assert_eq!(cmd.cwd, None);
    }

    #[test]
    fn the_bundled_runtime_is_started_as_a_module() {
        // Not through the `volum-engine` console script: a console script
        // carries the absolute shebang it was written with at install time,
        // which points at the build machine, not at wherever this was installed.
        let runtime = Path::new("/bundle/runtime");
        let cmd = resolve_command(None, Some(runtime), None, &|_| true).expect("should resolve");
        assert_eq!(&cmd.args[..2], ["-m", "volum_engine.main"]);
    }

    #[test]
    fn the_bundled_runtime_tells_the_engine_where_uv_is() {
        // The Model Manager builds a virtual environment per provider. A
        // bundled application has no business looking for that tool on PATH.
        let runtime = Path::new("/bundle/runtime");
        let cmd = resolve_command(None, Some(runtime), None, &|_| true).expect("should resolve");
        let uv = cmd
            .env
            .iter()
            .find(|(key, _)| key == UV_ENV)
            .expect("the bundled uv should be named");
        assert!(
            Path::new(&uv.1).starts_with(runtime),
            "uv must come from the bundle, got {}",
            uv.1
        );
    }

    #[test]
    fn the_bundled_runtime_may_not_write_into_its_own_bundle() {
        // An application bundle is sealed. Bytecode written into it breaks the
        // signature, and macOS then calls the application damaged — measured:
        // the first run of an unprotected build produced 1,280 complaints from
        // `codesign --verify`. The bundle ships precompiled, so this costs
        // nothing.
        let cmd = resolve_command(None, Some(Path::new("/bundle/runtime")), None, &|_| true)
            .expect("should resolve");
        assert!(
            cmd.env
                .iter()
                .any(|(key, value)| key == NO_BYTECODE_ENV && value == "1"),
            "the bundled runtime must not write bytecode"
        );
    }

    #[test]
    fn the_development_engine_may_write_bytecode() {
        // Nothing is sealed in a checkout, and the cache is worth having.
        let engine_dir = Path::new("/repo/engine");
        let cmd = resolve_command(None, None, Some(engine_dir), &|_| true).expect("should resolve");
        assert!(!cmd.env.iter().any(|(key, _)| key == NO_BYTECODE_ENV));
    }

    #[test]
    fn a_runtime_that_is_not_there_is_not_used() {
        // A resource directory always exists; the runtime inside it might not,
        // and falling through to the checkout is better than failing to start.
        let engine_dir = Path::new("/repo/engine");
        let cmd = resolve_command(
            None,
            Some(Path::new("/bundle/runtime")),
            Some(engine_dir),
            &|p| p == engine_dir,
        )
        .expect("should resolve");
        assert_eq!(cmd.program, PathBuf::from("uv"));
    }

    #[test]
    fn the_workspace_is_only_the_last_resort() {
        let engine_dir = Path::new("/repo/engine");
        let cmd = resolve_command(None, None, Some(engine_dir), &|p| p == engine_dir)
            .expect("should resolve");
        assert_eq!(cmd.program, PathBuf::from("uv"));
        assert_eq!(cmd.cwd.as_deref(), Some(engine_dir));
        assert!(cmd.args.iter().any(|a| a == "volum-engine"));
    }

    #[test]
    fn with_nothing_to_run_the_failure_is_explicit() {
        assert_eq!(
            resolve_command(None, None, None, &nothing_exists),
            Err(ResolveError::NothingFound)
        );
    }

    #[test]
    fn every_resolution_asks_the_engine_to_exit_with_its_parent() {
        // Otherwise a crashed shell leaves an engine holding the GPU.
        let engine_dir = Path::new("/repo/engine");
        let all = [
            resolve_command(Some(r#"["x"]"#), None, None, &nothing_exists),
            resolve_command(None, Some(Path::new("/bundle/runtime")), None, &|_| true),
            resolve_command(None, None, Some(engine_dir), &|_| true),
        ];
        for cmd in all {
            let cmd = cmd.expect("should resolve");
            assert!(
                cmd.args.iter().any(|a| a == "--exit-with-parent"),
                "{:?} must exit with its parent",
                cmd.program
            );
        }
    }
}
