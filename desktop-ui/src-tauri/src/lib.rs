use std::{
    io::{Read, Write},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WindowEvent};
use tauri_plugin_autostart::ManagerExt;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(debug_assertions)]
const BACKEND_PORT: u16 = 8877;
#[cfg(not(debug_assertions))]
const BACKEND_PORT: u16 = 8765;
#[cfg(debug_assertions)]
const SOURCE_APP_ID: &str = "ai.amca.desktop.source";
#[cfg(debug_assertions)]
const SOURCE_APP_NAME: &str = "AMCA Studio Source Dev";
#[cfg(debug_assertions)]
const SOURCE_WINDOW_TITLE: &str = "AMCA 1.1.21 — Source Dev (8877)";
const BACKEND_PROTOCOL_VERSION: u16 = 15;
/// 28B: packaged build identity. The release/validation build sets
/// AMCA_BUILD_SHA so the desktop can require that the backend it talks to
/// belongs to the same build. Unset (dev) means "identity unavailable".
const DESKTOP_BUILD_SHA: &str = match option_env!("AMCA_BUILD_SHA") {
    Some(value) => value,
    None => "",
};
/// Bounded grace period for the backend's graceful shutdown before the
/// process-tree fallback fires.
const QUIT_GRACE: Duration = Duration::from_secs(8);

#[derive(Debug, PartialEq, Eq)]
pub enum CloseAction {
    Hide,
    Allow,
}

/// Pure B1.3A lifecycle decision: window close initiates graceful shutdown;
/// CloseAction::Allow is returned so the window and application terminate cleanly.
pub fn close_request_action(quitting: bool) -> CloseAction {
    let _ = quitting;
    CloseAction::Allow
}

#[derive(Debug, PartialEq, Eq)]
pub enum LaunchMode {
    Normal,
    Background,
}

/// Pure B1.3C launch-reason decision: the autostart entry launches the app
/// with an explicit `--background` argument; heuristics are never used.
pub fn launch_mode(args: &[String]) -> LaunchMode {
    if args.iter().any(|arg| arg == "--background") {
        LaunchMode::Background
    } else {
        LaunchMode::Normal
    }
}

/// P1 installer-facing shutdown contract: `amca-desktop.exe --quit` asks the
/// running installed instance to stop gracefully (desktop plus its backend).
/// The flag must match exactly — heuristics like `--quitting` never count.
pub fn quit_requested(args: &[String]) -> bool {
    args.iter().any(|arg| arg == "--quit")
}

struct BackendProcess(Mutex<Option<Child>>);

/// P1 installer shutdown: kernel-level backend ownership. The job object is
/// created by the desktop process and holds the backend child with
/// JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so the operating system terminates the
/// backend whenever the desktop dies — graceful exit, crash, or an external
/// force-kill from an installer. No user-mode cleanup can be bypassed.
/// The managed state exists purely for its Drop-at-teardown side effect.
#[cfg(target_os = "windows")]
#[allow(dead_code)]
struct BackendJob(Mutex<Option<windows_job::BackendJobHandle>>);

#[cfg(target_os = "windows")]
mod windows_job {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// Owns the job handle; closing it (explicitly or at desktop process
    /// death) makes the kernel terminate every process in the job. The raw
    /// handle is only ever passed to kernel32 calls, never dereferenced, so
    /// moving it across threads is safe.
    pub struct BackendJobHandle(HANDLE);

    unsafe impl Send for BackendJobHandle {}
    unsafe impl Sync for BackendJobHandle {}

    /// Binds a freshly spawned backend to a kill-on-close job owned by this
    /// desktop process. Returns None only if the OS refuses job creation or
    /// assignment; the NSIS installer-side shutdown remains the fail-closed
    /// backstop in that case.
    pub fn bind_backend_to_desktop_job(child: &Child) -> Option<BackendJobHandle> {
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                CloseHandle(job);
                return None;
            }
            if AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE) == 0 {
                CloseHandle(job);
                return None;
            }
            Some(BackendJobHandle(job))
        }
    }

    impl Drop for BackendJobHandle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

/// v1.2.0: the native Windows folder chooser for a new project's folder.
///
/// The picker is a convenience, never a gate: the caller always keeps an
/// editable text field, so if COM refuses to start or the user cancels, the
/// project can still be created by typing a path. `None` means "the user
/// cancelled" and is distinct from `Err`, which means the picker itself
/// could not run.
#[cfg(target_os = "windows")]
pub mod project_folder_picker {
    use windows::core::{PCWSTR, PWSTR};
    use windows::Win32::Foundation::{ERROR_CANCELLED, HWND};
    use windows::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoTaskMemFree, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows::Win32::UI::Shell::{
        FileOpenDialog, IFileDialog, SIGDN_FILESYSPATH, FOS_FORCEFILESYSTEM, FOS_PATHMUSTEXIST,
        FOS_PICKFOLDERS,
    };

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// An HRESULT the picker can report without leaking any path or OS
    /// internals into the UI. The raw COM error is deliberately not shown.
    fn safe_picker_error(context: &str) -> String {
        format!("{context}. You can type the folder path instead.")
    }

    /// Releases the apartment on every exit path, including a panic.
    struct ComApartment;

    impl Drop for ComApartment {
        fn drop(&mut self) {
            unsafe { CoUninitialize() };
        }
    }

    /// A Shell-allocated PWSTR that frees itself with the Shell's allocator.
    ///
    /// The buffer belongs to COM task memory, so freeing it with the Rust
    /// allocator would corrupt the heap. A guard means a panic between the
    /// allocation and the conversion cannot leak it either.
    struct ShellString(PWSTR);

    impl ShellString {
        fn to_path(&self) -> Result<String, ()> {
            // SAFETY: the PWSTR came from IShellItem::GetDisplayName and is
            // still owned by this guard, so it is a live, NUL-terminated
            // buffer for the duration of this read.
            unsafe { self.0.to_string() }.map_err(|_| ())
        }
    }

    impl Drop for ShellString {
        fn drop(&mut self) {
            unsafe { CoTaskMemFree(Some(self.0.0 as *const core::ffi::c_void)) };
        }
    }

    /// Show the folder chooser owned by `owner` and return the chosen path.
    pub fn pick(owner: Option<HWND>) -> Result<Option<String>, String> {
        // A COM apartment must be initialized on the thread that uses COM, and
        // the Shell dialog requires a single-threaded apartment.
        let init = unsafe {
            CoInitializeEx(
                None,
                COINIT(COINIT_APARTMENTTHREADED.0 | COINIT_DISABLE_OLE1DDE.0),
            )
        };
        // S_OK and S_FALSE both mean the apartment is initialized and each
        // requires exactly one balancing CoUninitialize. RPC_E_CHANGED_MODE
        // means the thread is already in a different apartment model, which is
        // not something to force around - the text field remains the way in.
        if init.is_err() {
            return Err(safe_picker_error("The folder picker could not start"));
        }
        // From here the uninitialize is owned by the guard, so an early return
        // or a panic still balances the CoInitializeEx above.
        let _apartment = ComApartment;

        pick_in_apartment(owner)
    }

    fn pick_in_apartment(owner: Option<HWND>) -> Result<Option<String>, String> {
        unsafe {
            let dialog: IFileDialog = CoCreateInstance(
                &FileOpenDialog,
                None::<&windows::core::IUnknown>,
                CLSCTX_INPROC_SERVER,
            )
            .map_err(|_| safe_picker_error("The folder picker could not be opened"))?;

            // FOS_PICKFOLDERS is the whole point; PATHMUSTEXIST keeps a
            // mistyped path from being silently accepted as a new folder.
            dialog
                .SetOptions(FOS_PICKFOLDERS | FOS_PATHMUSTEXIST | FOS_FORCEFILESYSTEM)
                .map_err(|_| safe_picker_error("The folder picker could not be configured"))?;

            let title = wide("Choose a project folder");
            let _ = dialog.SetTitle(PCWSTR(title.as_ptr()));

            match dialog.Show(owner) {
                Ok(()) => {}
                // Cancelling is a normal outcome, not a failure.
                Err(error) if error.code() == hresult_from_win32(ERROR_CANCELLED.0) => {
                    return Ok(None)
                }
                Err(_) => return Err(safe_picker_error("The folder picker could not be shown")),
            }

            let item = dialog
                .GetResult()
                .map_err(|_| safe_picker_error("The chosen folder could not be read"))?;
            // The Shell allocates this string with CoTaskMemAlloc, so the
            // Shell's own free is required; the guard guarantees it happens
            // exactly once, including if the conversion below panics.
            let name = ShellString(
                item.GetDisplayName(SIGDN_FILESYSPATH)
                    .map_err(|_| safe_picker_error("The chosen folder could not be read"))?,
            );
            let path = name
                .to_path()
                .map_err(|_| safe_picker_error("The chosen folder could not be read"))?;
            if path.trim().is_empty() {
                return Ok(None);
            }
            Ok(Some(path))
        }
    }

    /// `hresult_from_win32(ERROR_CANCELLED)` - the value the Shell returns when
    /// the user closes the dialog without choosing.
    const fn hresult_from_win32(error: u32) -> windows::core::HRESULT {
        windows::core::HRESULT(((error & 0x0000_FFFF) as i32) | (0x8007_0000u32 as i32))
    }

    #[cfg(test)]
    mod tests {
        use super::{safe_picker_error, hresult_from_win32};

        #[test]
        fn cancel_error_maps_to_the_shell_cancel_code() {
            // ERROR_CANCELLED is 1223 (0x4C7), so HRESULT_FROM_WIN32 of it is
            // 0x800704C7 - the exact value the Shell returns on cancel.
            assert_eq!(hresult_from_win32(1223).0, 0x8007_04C7u32 as i32);
        }

        #[test]
        fn picker_error_is_path_free_and_actionable() {
            let message = safe_picker_error("The folder picker could not start");
            assert!(message.contains("type the folder path"));
            for forbidden in ["c:\\", "d:\\", "users\\", "appdata", "0x80"] {
                assert!(!message.to_lowercase().contains(forbidden), "leaked: {forbidden}");
            }
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub mod project_folder_picker {
    /// No native chooser on this platform; the caller falls back to its text
    /// field. Reported as "cancelled" so the UI simply leaves the field alone.
    pub fn pick() -> Result<Option<String>, String> {
        Ok(None)
    }
}

/// v1.2.0: open the native folder chooser and return the chosen path.
#[cfg(target_os = "windows")]
#[tauri::command]
fn pick_project_folder(app: AppHandle) -> Result<Option<String>, String> {
    let owner = app
        .get_webview_window("main")
        .and_then(|window| window.hwnd().ok());
    project_folder_picker::pick(owner)
}

/// Non-Windows builds have no native chooser; the text field remains the way
/// to set a folder, so this reports "cancelled" rather than an error.
#[cfg(not(target_os = "windows"))]
#[tauri::command]
fn pick_project_folder(_app: AppHandle) -> Result<Option<String>, String> {
    project_folder_picker::pick()
}

/// Round 26: open an http(s) URL in the operator's default system browser.
///
/// The panel's embedded preview cannot render sites that refuse framing;
/// this hands the SAME address to the real browser. Only http/https are
/// allowed, and the URL goes to `explorer` as a single argument (no shell
/// interpretation). `explorer` is used instead of `cmd /C start` so URL
/// characters can never reach a command-line parser.
#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    let url = url.trim().to_string();
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err("only http(s) URLs can be opened".into());
    }
    if url.len() > 2048 || url.chars().any(|c| c.is_control()) {
        return Err("invalid URL".into());
    }
    std::process::Command::new("explorer")
        .arg(&url)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

/// M5.4: ephemeral high-entropy session token generated by the desktop
/// shell at startup, handed to the backend via the environment and to the
/// Studio webview via a Tauri command. The token never travels over
/// unauthenticated HTTP.
struct SessionToken(Mutex<String>);
fn generate_session_token() -> String {
    // uuid v4 is backed by the operating system CSPRNG; two joined simple
    // UUIDs give 244 bits of entropy, comfortably above the 128-bit floor.
    format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    )
}

// A manually managed backend may share its ephemeral token through the
// launch environment, just as a desktop-owned backend does. Never obtain
// another process's token over unauthenticated HTTP.
fn launch_session_token() -> String {
    std::env::var("AMCA_SESSION_TOKEN")
        .ok()
        .filter(|value| {
            value.len() >= 32
                && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        })
        .unwrap_or_else(generate_session_token)
}

fn backend_accepts_session(token: &str) -> bool {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), BACKEND_PORT);
    backend_accepts_session_at(token, address)
}

fn backend_accepts_session_at(token: &str, address: SocketAddr) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_secs(2)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let request = format!("GET /api/dashboard HTTP/1.1\r\nHost: 127.0.0.1:{BACKEND_PORT}\r\nX-AMCA-Session: {token}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() { return false; }
    let mut response = String::new();
    if stream.take(1024 * 1024).read_to_string(&mut response).is_err() { return false; }
    response.starts_with("HTTP/1.0 200 ") || response.starts_with("HTTP/1.1 200 ")
}

/// Explicit-quitting flag: once Quit begins, the window-close handler stops
/// intercepting closes so the application can actually exit.
struct Quitting(Mutex<bool>);

/// Shared durable audit trail (B1.3D): one bounded JSONL file beside the
/// desktop database, written by both the Python backend and this shell.
struct AuditPath(Mutex<PathBuf>);

fn append_audit(path: &Path, event: &str, success: bool, detail: Option<&str>) {
    use std::io::Write;
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let at_epoch_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_millis())
        .unwrap_or(0);
    let mut value = serde_json::json!({
        "event": event,
        "at_epoch_ms": at_epoch_ms,
        "success": success,
    });
    if let Some(detail) = detail {
        value["detail"] =
            serde_json::Value::String(detail.chars().take(200).collect());
    }
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{value}");
    }
}

fn terminate_backend(child: &mut Child) {
    #[cfg(target_os = "windows")]
    {
        // A PyInstaller one-file executable uses a supervising bootloader
        // process. Killing only that PID can leave its Python worker alive, so
        // terminate the exact process tree owned by the child we spawned.
        let mut taskkill = Command::new("taskkill.exe");
        taskkill
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let _ = taskkill.status();
    }

    let _ = child.kill();
    let _ = child.wait();
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        if let Ok(child) = self.0.get_mut() {
            if let Some(child) = child.as_mut() {
                terminate_backend(child);
            }
        }
    }
}

fn backend_is_reachable() -> bool {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), BACKEND_PORT);
    TcpStream::connect_timeout(&address, Duration::from_millis(180)).is_ok()
}

fn response_has_compatible_protocol(response: &str) -> bool {
    response.contains("HTTP/1.0 200")
        && response.contains(&format!("\"protocol_version\":{BACKEND_PROTOCOL_VERSION}"))
}

/// One parsed /api/health identity observation (28B).
#[derive(Debug, PartialEq, Eq)]
pub struct BackendIdentity {
    pub protocol_ok: bool,
    pub app_version: Option<String>,
    pub build_sha: Option<String>,
    pub pid: Option<u32>,
}

fn parse_health_identity(response: &str) -> BackendIdentity {
    fn json_string(body: &str, key: &str) -> Option<String> {
        let needle = format!("\"{key}\":\"");
        let start = body.find(&needle)? + needle.len();
        let rest = &body[start..];
        let end = rest.find('"')?;
        Some(rest[..end].to_string())
    }
    fn json_number(body: &str, key: &str) -> Option<u32> {
        let needle = format!("\"{key}\":");
        let start = body.find(&needle)? + needle.len();
        let rest = &body[start..];
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        digits.parse().ok()
    }
    BackendIdentity {
        protocol_ok: response_has_compatible_protocol(response),
        app_version: json_string(response, "app_version"),
        build_sha: json_string(response, "build_sha"),
        pid: json_number(response, "pid"),
    }
}

fn probe_backend_identity() -> Option<BackendIdentity> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), BACKEND_PORT);
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(300)).ok()?;
    let timeout = Some(Duration::from_millis(500));
    stream.set_read_timeout(timeout).ok()?;
    stream.set_write_timeout(timeout).ok()?;
    let request = format!(
        "GET /api/health HTTP/1.1
Host: 127.0.0.1:{BACKEND_PORT}
Connection: close

"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    Some(parse_health_identity(&response))
}

/// Require the current version and protocol; if this desktop publishes a
/// build SHA, a missing or different backend SHA fails closed.
fn identity_matches_desktop(identity: &BackendIdentity) -> bool {
    if !identity.protocol_ok {
        return false;
    }
    let expected_version = env!("CARGO_PKG_VERSION");
    if identity.app_version.as_deref() != Some(expected_version) {
        return false;
    }
    if !DESKTOP_BUILD_SHA.is_empty()
        && identity.build_sha.as_deref() != Some(DESKTOP_BUILD_SHA)
    {
        return false;
    }
    true
}

/// Best-effort safe replacement of a stale AMCA backend occupying the port:
/// only a process whose image name is exactly amca-backend.exe and whose
/// health identity proves it is an AMCA backend is terminated.
#[cfg(not(debug_assertions))]
fn try_replace_stale_backend(identity: &BackendIdentity) -> bool {
    let Some(pid) = identity.pid else {
        return false;
    };
    let output = Command::new("tasklist.exe")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .creation_flags(0x0800_0000)
        .output();
    let Ok(output) = output else { return false };
    let listing = String::from_utf8_lossy(&output.stdout).to_ascii_lowercase();
    if !listing.contains("amca-backend.exe") {
        return false;
    }
    let killed = Command::new("taskkill.exe")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(0x0800_0000)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !killed {
        return false;
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if !backend_is_reachable() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn hidden_command(executable: &Path) -> Command {
    let mut command = Command::new(executable);
    #[cfg(target_os = "windows")]
    command.creation_flags(0x0800_0000);
    // Dev builds inherit the console so backend crashes leave a traceback in
    // the tauri dev log instead of vanishing; release stays hidden.
    if cfg!(debug_assertions) {
        command.stdout(Stdio::inherit()).stderr(Stdio::inherit());
    } else {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }
    command
}

#[cfg(debug_assertions)]
fn development_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("src-tauri must be nested under software/desktop-ui")
        .to_path_buf()
}

fn persistent_database(app: &AppHandle) -> Result<PathBuf, String> {
    let mut app_data = app.path().app_data_dir().map_err(|error| error.to_string())?;
    #[cfg(debug_assertions)]
    {
        // 1.1.8 source isolation: dev runs use their own data directory and
        // database so the installed product's data is never touched.
        app_data.push("source-dev");
    }
    std::fs::create_dir_all(&app_data).map_err(|error| {
        format!(
            "failed to create AMCA application data directory {}: {error}",
            app_data.display()
        )
    })?;
    Ok(app_data.join("amca.sqlite3"))
}

fn spawn_backend(
    app: &AppHandle,
    session_token: &str,
) -> Result<Option<Child>, BackendStartError> {
    // 1.1.8 source isolation (debug builds only): the source dev desktop
    // NEVER inspects, reuses, or terminates another AMCA backend. If anything
    // occupies the source port, diagnose and exit; the installed product on
    // 8765 stays untouched.
    #[cfg(debug_assertions)]
    if backend_is_reachable() {
        return Err(BackendStartError::PortOccupied);
    }

    #[cfg(not(debug_assertions))]
    if backend_is_reachable() {
        match probe_backend_identity() {
            Some(identity) if identity_matches_desktop(&identity) => {
                if backend_accepts_session(session_token) {
                    return Ok(None);
                }
                // Same build, different session: another AMCA instance owns
                // this backend. Never attach silently — clear diagnostic.
                return Err(BackendStartError::VersionMismatch);
            }
            Some(identity) => {
                // 28B: an AMCA backend from a different build/version is
                // safely replaced after verifying the image name; if that is
                // not provably our own backend, diagnose instead of attaching.
                if !try_replace_stale_backend(&identity) {
                    return Err(BackendStartError::VersionMismatch);
                }
                // Port is free again; fall through to spawn our backend.
            }
            None => return Err(BackendStartError::PortOccupied),
        }
    }

    let database = persistent_database(app).map_err(|_| BackendStartError::DataDirectoryUnavailable)?;

    // 28B: the development-tree launch path is compiled out of release
    // builds entirely, so the installed product can only ever run its own
    // packaged backend.
    #[cfg(debug_assertions)]
    {
        let root = development_root();
        let python = std::env::var_os("AMCA_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(".venv/Scripts/python.exe"));
        let mut command = hidden_command(Path::new(&python));
        command
            .arg("-m")
            .arg("amca.service")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(BACKEND_PORT.to_string())
            .arg("--root")
            .arg(&root)
            .arg("--database")
            .arg(&database)
            .current_dir(&root)
            .env("PYTHONPATH", root.join("src"))
            .env("AMCA_SESSION_TOKEN", session_token)
            // Defense in depth (security review): strip any ambient value so
            // the backend always launches in human-approval posture.
            .env_remove("AMCA_DESKTOP_AUTOMATIC_CLOUD");
        // AMCA_DESKTOP_AUTOMATIC_CLOUD is deliberately NOT set here: governed
        // cloud transmissions must reach the human approve/deny step. The
        // backend decides the chat-approval posture from its own policy; the
        // shell never grants it implicitly (Gate-2 fix order, decision 1).
        return command.spawn().map(Some).map_err(BackendStartError::from_spawn_error);
    }

    #[cfg(not(debug_assertions))]
    {
        let backend = match app.path().resource_dir() {
            Ok(dir) => dir.join("amca-backend.exe"),
            Err(_) => return Err(BackendStartError::BackendMissing),
        };
        if !backend.is_file() {
            return Err(BackendStartError::BackendMissing);
        }
        let mut command = hidden_command(&backend);
        command
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg(BACKEND_PORT.to_string())
            .arg("--database")
            .arg(&database)
            .env("AMCA_SESSION_TOKEN", session_token)
            // Defense in depth (security review): strip any ambient value so
            // the backend always launches in human-approval posture, and the
            // debug and release launch paths behave identically.
            .env_remove("AMCA_DESKTOP_AUTOMATIC_CLOUD");
        // AMCA_DESKTOP_AUTOMATIC_CLOUD is deliberately not granted here —
        // the debug and release launch paths must behave identically: a
        // governed Transmit-Excerpt request stays PENDING until a human
        // approves or denies it (Gate-2 fix order, decisions 1 and K).
        command.spawn().map(Some).map_err(BackendStartError::from_spawn_error)
    }
}

/// 28B P1: supervise the spawned backend. If it exits unexpectedly while the
/// desktop is running, respawn it (bounded attempts) so an ordinary backend
/// crash never forces the user to fully exit AMCA. Normal quit stops the
/// supervisor and terminates the process tree as before.
fn supervise_backend(app: AppHandle) {
    std::thread::spawn(move || {
        let mut consecutive_failures: u32 = 0;
        loop {
            std::thread::sleep(Duration::from_secs(3));
            let quitting = app
                .try_state::<Quitting>()
                .map(|flag| flag.0.lock().map(|guard| *guard).unwrap_or(true))
                .unwrap_or(true);
            if quitting {
                return;
            }
            let exited = app
                .try_state::<BackendProcess>()
                .map(|state| {
                    state
                        .0
                        .lock()
                        .map(|mut guard| match guard.as_mut() {
                            Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                            None => false,
                        })
                        .unwrap_or(false)
                })
                .unwrap_or(false);
            if !exited || consecutive_failures >= 5 {
                continue;
            }
            let token = app
                .try_state::<SessionToken>()
                .and_then(|state| state.0.lock().map(|guard| guard.clone()).ok())
                .unwrap_or_default();
            match spawn_backend(&app, &token) {
                Ok(child) => {
                    if let Some(state) = app.try_state::<BackendProcess>() {
                        if let Ok(mut guard) = state.0.lock() {
                            *guard = child;
                        }
                    }
                    #[cfg(target_os = "windows")]
                    {
                        if let (Some(state), Some(job)) = (
                            app.try_state::<BackendProcess>(),
                            app.try_state::<BackendJob>(),
                        ) {
                            if let (Ok(guard), Ok(mut job_guard)) =
                                (state.0.lock(), job.0.lock())
                            {
                                *job_guard = guard
                                    .as_ref()
                                    .and_then(windows_job::bind_backend_to_desktop_job);
                            }
                        }
                    }
                    consecutive_failures = 0;
                    if let Some(path) = app.try_state::<AuditPath>() {
                        if let Ok(guard) = path.0.lock() {
                            append_audit(&guard, "backend_respawned", true, None);
                        }
                    }
                }
                Err(error) => {
                    consecutive_failures += 1;
                    let message = backend_start_error_message(&error);
                    if let Some(path) = app.try_state::<AuditPath>() {
                        if let Ok(guard) = path.0.lock() {
                            append_audit(
                                &guard,
                                "backend_respawn_failed",
                                false,
                                Some(&message.replace('\n', " ")),
                            );
                        }
                    }
                }
            }
        }
    });
}

/// Classified backend startup failure. `PortOccupied` is user-actionable (a
/// visible native dialog). The remaining variants carry SAFE, fixed,
/// path-free descriptions: raw OS/IO errors are never embedded, so neither
/// the dialog nor the audit can leak usernames, AppData paths, install
/// paths, credentials, or environment values (P1.17 sanitization).
#[derive(Debug, PartialEq, Eq)]
pub enum BackendStartError {
    PortOccupied,
    /// 28B: an AMCA backend of a different build/version answered on the
    /// port and could not be safely replaced; the user gets a clear
    /// diagnostic instead of a silent attach.
    VersionMismatch,
    /// The persistent data directory could not be created/used.
    DataDirectoryUnavailable,
    /// The packaged backend executable is missing (resource check failed).
    BackendMissing,
    /// The backend process could not be spawned; classified by OS error kind.
    SpawnFailed(&'static str),
}

impl BackendStartError {
    /// Conservative classification of a spawn failure: only the stable OS
    /// error kind name is kept. Unknown kinds collapse to a generic reason.
    fn from_spawn_error(error: std::io::Error) -> BackendStartError {
        let kind = match error.kind() {
            std::io::ErrorKind::NotFound => "the program file was not found",
            std::io::ErrorKind::PermissionDenied => "access was denied by the operating system",
            std::io::ErrorKind::OutOfMemory => "the system is out of memory",
            _ => "the operating system refused to start the program",
        };
        BackendStartError::SpawnFailed(kind)
    }
}

/// Show a blocking native error dialog on Windows. Returns true when a dialog
/// was actually presented.
#[cfg(target_os = "windows")]
fn show_startup_error_dialog(message: &str) -> bool {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

    fn wide(value: &str) -> Vec<u16> {
        std::ffi::OsStr::new(value)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }
    let text = wide(message);
    let caption = wide("AMCA Studio");
    let result = unsafe {
        MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), MB_OK | MB_ICONERROR)
    };
    result != 0
}

#[cfg(not(target_os = "windows"))]
fn show_startup_error_dialog(_message: &str) -> bool {
    false
}

/// User-facing message mandated by ZC-AMCA-V0.6.1-PORT-CONFLICT-UX-HOTFIX-13.
pub fn backend_start_error_message(error: &BackendStartError) -> String {
    match error {
        BackendStartError::PortOccupied => format!(
            "AMCA Studio cannot start because port {BACKEND_PORT} is already in use by \
             another program or an incompatible AMCA backend.\n\n\
             Close the conflicting program (or the other AMCA instance) and start AMCA again."
        ),
        BackendStartError::VersionMismatch => format!(
            "AMCA Studio detected a local service from a different AMCA build              on port {BACKEND_PORT}.

             Close the other AMCA version completely, then start AMCA Studio again."
        ),
        BackendStartError::DataDirectoryUnavailable => format!(
            "AMCA Studio cannot prepare its local data folder.\n\n\
             Make sure your Windows user profile is available and writable, \
             then start AMCA again."
        ),
        BackendStartError::BackendMissing => format!(
            "AMCA Studio is missing a required program component (the local service).\n\n\
             Reinstall AMCA Studio, then start it again."
        ),
        BackendStartError::SpawnFailed(reason) => format!(
            "AMCA Studio could not start its local service.\n\n\
             Reason: the operating system reported that {reason}.\n\n\
             Close other programs and start AMCA again."
        ),
    }
}


/// Best-effort authenticated handshake: fetch the session token, then ask the
/// canonical backend to shut itself down gracefully. Any failure simply
/// returns false and leaves the bounded fallback in charge.
fn request_backend_shutdown(session_token: &str) -> bool {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), BACKEND_PORT);
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(1500));
    if stream.set_read_timeout(timeout).is_err() || stream.set_write_timeout(timeout).is_err() {
        return false;
    }
    let request = format!(
        "POST /api/shutdown HTTP/1.1
Host: 127.0.0.1:{BACKEND_PORT}
X-AMCA-Session: {session_token}
Content-Length: 0
Connection: close

"
    );
    stream.write_all(request.as_bytes()).is_ok()
}

fn wait_for_backend_exit(deadline: Instant) -> bool {
    while Instant::now() < deadline {
        if !backend_is_reachable() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    !backend_is_reachable()
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn begin_graceful_quit(app: &AppHandle) {
    if let Ok(mut quitting) = app.state::<Quitting>().0.lock() {
        *quitting = true;
    }
    if let Ok(audit) = app.state::<AuditPath>().0.lock() {
        append_audit(&audit, "backend_shutdown_requested", true, None);
    }
    // Only the Studio instance that owns the backend asks it to stop; a
    // second window reusing an existing backend must leave it alone.
    let owned_child = app
        .state::<BackendProcess>()
        .0
        .lock()
        .ok()
        .and_then(|mut guard| guard.take());
    if owned_child.is_some() {
        let token = app
            .state::<SessionToken>()
            .0
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default();
        let _ = request_backend_shutdown(&token);
        let _ = wait_for_backend_exit(Instant::now() + QUIT_GRACE);
    }
    // Final bounded fallback: whatever we still own gets torn down.
    if let Some(child) = owned_child {
        let mut child = child;
        if backend_is_reachable() {
            if let Ok(audit) = app.state::<AuditPath>().0.lock() {
                append_audit(
                    &audit,
                    "backend_shutdown_forced",
                    false,
                    Some("graceful shutdown timed out"),
                );
            }
            terminate_backend(&mut child);
        } else {
            let _ = child.wait();
        }
    }
    app.exit(0);
}

/// M5.4: out-of-band session-token transfer to the Studio webview. Only
/// invocable from inside the Tauri webview; arbitrary web pages cannot
/// reach Tauri commands, and the token never travels over unauthenticated
/// HTTP.
#[tauri::command]
fn session_token(state: tauri::State<SessionToken>) -> String {
    state.0.lock().map(|guard| guard.clone()).unwrap_or_default()
}

#[tauri::command]
fn backend_status(state: tauri::State<SessionToken>) -> bool {
    let Ok(token) = state.0.lock() else { return false };
    probe_backend_identity()
        .is_some_and(|identity| identity_matches_desktop(&identity))
        && backend_accepts_session(&token)
}

/// Save an exported artifact to the user's Downloads folder.
///
/// The webview's own Blob download is not delivered by the WebView2 host
/// (the transfer starts with the right name and size and is then cancelled),
/// so artifact exports are written through this command instead. The filename
/// is reduced to a single safe component: no separators, no traversal, no
/// reserved characters.
#[tauri::command]
fn save_export(app: AppHandle, filename: String, contents: String) -> Result<String, String> {
    let cleaned: String = filename
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' | '\0' => '_',
            other if other.is_control() => '_',
            other => other,
        })
        .collect();
    let cleaned = cleaned.trim().trim_matches('.').to_string();
    if cleaned.is_empty() {
        return Err("a filename is required".into());
    }
    let directory = app
        .path()
        .download_dir()
        .or_else(|_| app.path().document_dir())
        .map_err(|error| error.to_string())?;
    std::fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let mut target = directory.join(&cleaned);
    // Never silently overwrite an existing export.
    if target.exists() {
        let stem = target
            .file_stem()
            .map(|value| value.to_string_lossy().to_string())
            .unwrap_or_else(|| "export".into());
        let extension = target
            .extension()
            .map(|value| value.to_string_lossy().to_string());
        for index in 2..1000 {
            let candidate = match &extension {
                Some(ext) => directory.join(format!("{stem} ({index}).{ext}")),
                None => directory.join(format!("{stem} ({index})")),
            };
            if !candidate.exists() {
                target = candidate;
                break;
            }
        }
    }
    std::fs::write(&target, contents.as_bytes()).map_err(|error| error.to_string())?;
    Ok(target.to_string_lossy().to_string())
}

#[tauri::command]
fn autostart_status(app: AppHandle) -> Result<bool, String> {
    // Truthful OS registration state, never a preference-file mirror.
    app.autolaunch().is_enabled().map_err(|error| error.to_string())
}

#[tauri::command]
fn autostart_enable(app: AppHandle) -> Result<bool, String> {
    let manager = app.autolaunch();
    if manager.is_enabled().map_err(|error| error.to_string())? {
        return Ok(true); // idempotent repeat enable
    }
    if let Err(error) = manager.enable() {
        if let Ok(audit) = app.state::<AuditPath>().0.lock() {
            append_audit(&audit, "autostart_change_failed", false, Some(&error.to_string()));
        }
        return Err(error.to_string());
    }
    // Report the ACTUAL registered state so a failed registration can never
    // render as enabled.
    let registered = manager.is_enabled().map_err(|error| error.to_string())?;
    if registered {
        if let Ok(audit) = app.state::<AuditPath>().0.lock() {
            append_audit(&audit, "autostart_enabled", true, None);
        }
    }
    Ok(registered)
}

#[tauri::command]
fn autostart_disable(app: AppHandle) -> Result<bool, String> {
    let manager = app.autolaunch();
    if !manager.is_enabled().map_err(|error| error.to_string())? {
        return Ok(false); // idempotent repeat disable
    }
    if let Err(error) = manager.disable() {
        if let Ok(audit) = app.state::<AuditPath>().0.lock() {
            append_audit(&audit, "autostart_change_failed", false, Some(&error.to_string()));
        }
        return Err(error.to_string());
    }
    let registered_still = !manager.is_enabled().map_err(|error| error.to_string())?;
    if registered_still {
        if let Ok(audit) = app.state::<AuditPath>().0.lock() {
            append_audit(&audit, "autostart_disabled", true, None);
        }
    }
    Ok(registered_still)
}

#[tauri::command]
fn send_native_notification(app: AppHandle, title: String, body: String) -> Result<bool, String> {
    use tauri_plugin_notification::NotificationExt;
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map(|_| true)
        .map_err(|error| error.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // 1.1.8 source-only dev isolation (debug builds): distinct app identity,
    // data directory, and window title. Release builds keep the production
    // identity exactly as before. Applied before plugin registration so
    // single-instance, autostart, and tray entries can never collide with
    // the installed 1.1.7 product.
    let context = tauri::generate_context!();
    #[cfg(debug_assertions)]
    let context = {
        let mut context = context;
        let config = context.config_mut();
        config.identifier = SOURCE_APP_ID.into();
        config.product_name = Some(SOURCE_APP_NAME.into());
        if let Some(window) = config.app.windows.get_mut(0) {
            window.title = SOURCE_WINDOW_TITLE.into();
        }
        context
    };
    let builder = tauri::Builder::default()
        // Single-instance MUST register before other plugins: a secondary
        // launch forwards activation to the primary and exits immediately.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // P1: `--quit` forwarded from a second launch is the
            // installer-facing request to gracefully stop this instance.
            if quit_requested(&argv) {
                begin_graceful_quit(app);
                return;
            }
            show_main_window(app);
        }))
        .plugin({
            let autostart = tauri_plugin_autostart::Builder::new()
                .app_name(if cfg!(debug_assertions) { "AMCA Source Dev" } else { "AMCA" })
                .args(["--background"]);
            autostart.build()
        })
        .plugin(tauri_plugin_notification::init());
    builder
        .setup(|app| {
            // P1: a primary launched with `--quit` means no other instance
            // existed to receive the forwarded flag; exit before spawning a
            // backend or creating the tray. A secondary launch never reaches
            // this point (the single-instance plugin exits it during init).
            if quit_requested(&std::env::args().collect::<Vec<String>>()) {
                std::process::exit(0);
            }

            let session_token = launch_session_token();
            // P1.13 hotfix: a failed backend start (port already in use by a
            // foreign program or an incompatible AMCA backend, missing backend
            // executable, spawn failure) must NEVER fail silently. Show the
            // user-facing reason in a native dialog, then exit cleanly.
            let child = match spawn_backend(&app.handle().clone(), &session_token) {
                Ok(child) => child,
                Err(error) => {
                    let message = backend_start_error_message(&error);
                    if let Ok(database) = persistent_database(&app.handle().clone()) {
                        append_audit(
                            &database
                                .parent()
                                .unwrap_or_else(|| Path::new("."))
                                .join("background-lifecycle.jsonl"),
                            "backend_start_failed",
                            false,
                            Some(&message.replace('\n', " ")),
                        );
                    }
                    for window in app.webview_windows().values() {
                        let _ = window.hide();
                    }
                    let _ = show_startup_error_dialog(&message);
                    std::process::exit(1);
                }
            };
            #[cfg(target_os = "windows")]
            let backend_job = child
                .as_ref()
                .and_then(windows_job::bind_backend_to_desktop_job);
            app.manage(SessionToken(Mutex::new(session_token)));
            app.manage(BackendProcess(Mutex::new(child)));
            #[cfg(target_os = "windows")]
            app.manage(BackendJob(Mutex::new(backend_job)));
            app.manage(Quitting(Mutex::new(false)));
            if let Ok(database) = persistent_database(&app.handle().clone()) {
                app.manage(AuditPath(Mutex::new(
                    database
                        .parent()
                        .unwrap_or_else(|| Path::new("."))
                        .join("background-lifecycle.jsonl"),
                )));
            }

            // Autostarted launches go straight to tray residency.
            let argv: Vec<String> = std::env::args().collect();
            if launch_mode(&argv) == LaunchMode::Background {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }

            let open =
                MenuItem::with_id(app, "open", "Open AMCA Studio", true, None::<&str>)?;
            let quit =
                MenuItem::with_id(app, "quit", "Quit AMCA Studio", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            TrayIconBuilder::with_id("amca-tray")
                .tooltip("AMCA Studio — automations keep running")
                .icon(app.default_window_icon().cloned().ok_or("missing window icon")?)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                })
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main_window(app),
                    "quit" => begin_graceful_quit(app),
                    _ => {}
                })
                .build(app)?;
            supervise_backend(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            session_token,
            backend_status,
            save_export,
            pick_project_folder,
            open_external,
            autostart_status,
            autostart_enable,
            autostart_disable,
            send_native_notification
        ])
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. } => {
                let quitting = window
                    .app_handle()
                    .state::<Quitting>()
                    .0
                    .lock()
                    .map(|guard| *guard)
                    .unwrap_or(true);
                if close_request_action(quitting) == CloseAction::Hide {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    begin_graceful_quit(window.app_handle());
                }
            }
            _ => {}
        })
        .run(context)
        .expect("error while running the AMCA desktop application");
}

#[cfg(test)]
mod tests {
    use super::{close_request_action, response_has_compatible_protocol, CloseAction};

    #[cfg(debug_assertions)]
    #[test]
    fn source_dev_identity_mutex_port_and_csp_are_isolated() {
        let production: tauri::Config = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let source: tauri::Config = serde_json::from_str(include_str!("../tauri.conf.dev.json")).unwrap();
        assert_eq!(source.identifier, super::SOURCE_APP_ID);
        // tauri-plugin-single-instance's Windows implementation names its
        // mutex "{identifier}-sim" (optionally adding a version before -sim).
        // Distinct identifiers isolate both naming modes before plugin init.
        assert_ne!(format!("{}-sim", source.identifier), format!("{}-sim", production.identifier));
        assert_eq!(super::BACKEND_PORT, 8877);
        assert_eq!(source.app.windows[0].title, super::SOURCE_WINDOW_TITLE);
        let source_csp = source.app.security.csp.unwrap().to_string();
        assert!(source_csp.contains("http://127.0.0.1:8877"));
        assert!(!source_csp.contains(":8765"));
        assert!(production.app.security.csp.unwrap().to_string().contains(":8765"));
    }

    #[test]
    fn backend_reuse_requires_authenticated_success() {
        use std::io::{Read, Write};
        for status in ["200 OK", "401 Unauthorized", "403 Forbidden", "302 Found"] {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let server = std::thread::spawn(move || {
                let (mut connection, _) = listener.accept().unwrap();
                let mut request = [0; 1024];
                let size = connection.read(&mut request).unwrap();
                let request = std::str::from_utf8(&request[..size]).unwrap();
                assert!(request.starts_with("GET /api/dashboard HTTP/1.1\r\n"));
                assert!(request.contains("X-AMCA-Session: acceptance-fixture\r\n"));
                write!(connection, "HTTP/1.1 {status}\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}").unwrap();
            });
            assert_eq!(super::backend_accepts_session_at("acceptance-fixture", address), status == "200 OK");
            server.join().unwrap();
        }
    }

    #[test]
    fn port_occupied_error_uses_the_mandated_user_message() {
        use super::{backend_start_error_message, BackendStartError};
        let message = backend_start_error_message(&BackendStartError::PortOccupied);
        assert!(
            message.contains(
                &format!("port {} is already in use by another program or an incompatible AMCA backend", super::BACKEND_PORT)
            ),
            "message must classify the conflict accurately: {message}"
        );
        assert!(
            message.to_lowercase().contains("close the conflicting program"),
            "message must tell the user how to recover: {message}"
        );
        assert!(message.starts_with("AMCA Studio cannot start"));
    }

    /// Forbidden content for dialog and audit messages (P1.17 sanitization):
    /// no usernames, profiles, AppData, install paths, credentials, tokens,
    /// environment values, multiline text, or raw OS internals.
    fn assert_no_forbidden_content(message: &str) {
        let lowered = message.to_lowercase();
        for forbidden in [
            "appdata", "userprofile", "users\\", "users/", "program files",
            "md. ashraful", "ashraful", "c:\\", "d:\\", "\\\\", "\\n", "qwen",
            "token", "bearer", "secret", "api_key", "authorization",
            "%appdata%", "%userprofile%", "amca-backend.exe", ".sqlite3",
            "backtrace", "panic", "panicked at",
        ] {
            assert!(!lowered.contains(forbidden), "forbidden content leaked: {forbidden}");
        }
        // Multiline constraint: paragraph breaks (\n\n) are allowed dialog
        // formatting; every rendered line must be bounded and free of raw
        // error content. The audit call site flattens newlines to single
        // spaces, so the flattened form must round-trip deterministically.
        for line in message.split('\n') {
            assert!(line.chars().count() <= 300, "overlong error line leaked: {line}");
        }
        let audit_form = message.replace('\n', " ");
        assert_eq!(audit_form.split_whitespace().count(), message.split_whitespace().count());
    }

    #[test]
    fn data_directory_failure_is_path_free_and_actionable() {
        use super::{backend_start_error_message, BackendStartError};
        let message = backend_start_error_message(&BackendStartError::DataDirectoryUnavailable);
        assert_no_forbidden_content(&message);
        assert!(message.contains("local data folder"));
        assert!(message.contains("start AMCA again"));
    }

    #[test]
    fn backend_missing_failure_is_path_free_and_actionable() {
        use super::{backend_start_error_message, BackendStartError};
        let message = backend_start_error_message(&BackendStartError::BackendMissing);
        assert_no_forbidden_content(&message);
        assert!(message.contains("missing a required program component"));
        assert!(message.contains("Reinstall AMCA Studio"));
    }

    #[test]
    fn spawn_failures_expose_only_safe_os_kinds() {
        use super::{backend_start_error_message, BackendStartError};
        for kind in [
            "the program file was not found",
            "access was denied by the operating system",
            "the system is out of memory",
            "the operating system refused to start the program",
        ] {
            let message = backend_start_error_message(&BackendStartError::SpawnFailed(kind));
            assert_no_forbidden_content(&message);
            assert!(message.contains(kind), "expected kind text in: {message}");
        }
    }

    #[test]
    fn port_occupied_message_never_drifts_from_the_approved_text() {
        use super::{backend_start_error_message, BackendStartError};
        let message = backend_start_error_message(&BackendStartError::PortOccupied);
        assert_no_forbidden_content(&message);
        assert_eq!(
            message,
            format!(
                "AMCA Studio cannot start because port {} is already in use by \
                 another program or an incompatible AMCA backend.\n\n\
                 Close the conflicting program (or the other AMCA instance) and start AMCA again.",
                super::BACKEND_PORT
            )
        );
    }

    #[test]
    fn quit_flag_requires_exact_match() {
        use super::quit_requested;
        let none: Vec<String> = vec!["amca-desktop.exe".into()];
        assert!(!quit_requested(&none));
        let quit: Vec<String> = vec!["amca-desktop.exe".into(), "--quit".into()];
        assert!(quit_requested(&quit));
        let lookalike: Vec<String> = vec!["amca-desktop.exe".into(), "--quitting".into()];
        assert!(!quit_requested(&lookalike));
        let positional: Vec<String> = vec!["amca-desktop.exe".into(), "--quit=x".into()];
        assert!(!quit_requested(&positional));
        let among_others: Vec<String> = vec![
            "amca-desktop.exe".into(),
            "--other".into(),
            "--quit".into(),
        ];
        assert!(quit_requested(&among_others));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn job_close_terminates_the_bound_child() {
        use super::windows_job::bind_backend_to_desktop_job;
        use std::os::windows::process::CommandExt;
        use std::process::{Command, Stdio};
        use std::time::{Duration, Instant};

        // A long-lived stand-in for the backend: ping runs for ~30 seconds,
        // far beyond the kill window asserted below.
        let mut command = Command::new("cmd.exe");
        command
            .args(["/c", "ping", "-n", "30", "127.0.0.1"])
            .creation_flags(0x0800_0000)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let mut child = command.spawn().expect("spawn stand-in child");
        let job = bind_backend_to_desktop_job(&child)
            .expect("job creation and assignment must succeed");
        drop(job);

        let deadline = Instant::now() + Duration::from_secs(5);
        let exited = loop {
            match child.try_wait().expect("wait on stand-in child") {
                Some(_) => break true,
                None if Instant::now() >= deadline => break false,
                None => std::thread::sleep(Duration::from_millis(100)),
            }
        };
        assert!(
            exited,
            "closing the desktop-owned job must terminate the backend process"
        );
    }

    #[test]
    fn accepts_only_the_current_desktop_protocol() {
        assert!(response_has_compatible_protocol(
            "HTTP/1.0 200 OK\r\n\r\n{\"status\":\"ok\",\"protocol_version\":15}"
        ));
        assert!(!response_has_compatible_protocol(
            "HTTP/1.0 200 OK\r\n\r\n{\"status\":\"ok\"}"
        ));
        assert!(!response_has_compatible_protocol(
            "HTTP/1.0 200 OK\r\n\r\n{\"status\":\"ok\",\"protocol_version\":14}"
        ));
        assert!(!response_has_compatible_protocol(
            "HTTP/1.0 200 OK\r\n\r\n{\"status\":\"ok\",\"protocol_version\":3}"
        ));
    }

    #[test]
    fn closing_initiates_clean_shutdown() {
        assert_eq!(close_request_action(false), CloseAction::Allow);
        assert_eq!(close_request_action(true), CloseAction::Allow);
    }

    #[test]
    fn launch_mode_is_argument_driven_only() {
        use super::launch_mode;
        use super::LaunchMode;
        let normal: Vec<String> = vec!["amca.exe".into()];
        assert_eq!(launch_mode(&normal), LaunchMode::Normal);
        let background: Vec<String> = vec!["amca.exe".into(), "--background".into()];
        assert_eq!(launch_mode(&background), LaunchMode::Background);
        let lookalike: Vec<String> = vec!["amca.exe".into(), "--backgrounded".into()];
        assert_eq!(launch_mode(&lookalike), LaunchMode::Normal);
        let extra_before: Vec<String> =
            vec!["amca.exe".into(), "--other".into(), "--background".into()];
        assert_eq!(launch_mode(&extra_before), LaunchMode::Background);
    }
}
