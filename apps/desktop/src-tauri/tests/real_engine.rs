//! Starting the real engine.
//!
//! The unit tests cover parsing and resolution; this one covers the part that
//! only a real process can prove — that the handshake, the token and the
//! exit-with-parent contract actually hold against the engine as shipped.
//!
//! Opt-in, because it needs `uv` and a synced engine environment:
//!
//!     cargo test --test real_engine -- --ignored --nocapture
//!
//! Marked `#[ignore]` rather than gated on an environment variable so the
//! runner reports it as *ignored*. A test that checks a variable and returns
//! early is reported as **passed**, which is indistinguishable from having
//! actually proved something.

use std::path::{Path, PathBuf};
use std::time::Duration;

use volum_desktop_lib::engine::{self, EngineCommand};

fn engine_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../engine")
        .canonicalize()
        .expect("the repository's engine directory")
}

fn command() -> EngineCommand {
    engine::resolve_command(None, Path::new("/nonexistent"), Some(&engine_dir()), &|p| {
        p.exists()
    })
    .expect("the workspace engine should resolve")
}

/// A data directory of its own, so a test never touches the user's models.
fn isolated(mut command: EngineCommand, data: &Path) -> EngineCommand {
    command.args.push("--data-dir".into());
    command.args.push(data.display().to_string());
    command
}

#[test]
#[ignore = "starts the real engine; needs uv and a synced engine environment"]
fn the_engine_announces_itself_and_answers_only_with_the_token() {
    let data = std::env::temp_dir().join(format!("volum-e2e-{}", std::process::id()));
    let token = engine::generate_token();
    let mut started = engine::start(
        &isolated(command(), &data),
        &token,
        Duration::from_secs(120),
    )
    .unwrap_or_else(|error| panic!("{}: {}", error.message(), error.detail()));

    assert_eq!(started.announcement.host, "127.0.0.1");
    assert!(started.announcement.port > 0);
    assert!(!started.announcement.version.is_empty());

    let base = started.base_url();
    let client = reqwest::blocking::Client::new();

    let ok = client
        .get(format!("{base}/health"))
        .bearer_auth(&token)
        .timeout(Duration::from_secs(20))
        .send()
        .expect("the engine should answer");
    assert_eq!(ok.status().as_u16(), 200);

    let refused = client
        .get(format!("{base}/health"))
        .timeout(Duration::from_secs(20))
        .send()
        .expect("the engine should answer");
    assert_eq!(
        refused.status().as_u16(),
        401,
        "no token must mean no answer"
    );

    let pid = started.announcement.pid;
    started.shutdown(Duration::from_secs(20));
    std::thread::sleep(Duration::from_millis(500));
    assert!(!is_alive(pid), "closing stdin must stop the engine");

    let _ = std::fs::remove_dir_all(&data);
}

#[cfg(unix)]
fn is_alive(pid: u32) -> bool {
    // Signal 0 checks for existence without delivering anything.
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(not(unix))]
fn is_alive(pid: u32) -> bool {
    std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}")])
        .output()
        .map(|out| String::from_utf8_lossy(&out.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}
