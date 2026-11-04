# AMCA Windows desktop packaging

The desktop release consists of one Tauri application plus a bundled,
windowless `amca-backend.exe`. The backend contains the Python runtime, AMCA
package, and benchmark fixtures, so an installed release does not require a
separate Python installation.

The Tauri launcher always binds the service to `127.0.0.1:8765`. Release builds
load `amca-backend.exe` from Tauri's resource directory and pass an explicit
SQLite path returned by `app.path().app_data_dir()`. With the current
`ai.amca.desktop` identifier, Windows normally stores that database at:

```text
%APPDATA%\ai.amca.desktop\amca.sqlite3
```

Uninstalling the application does not intentionally remove this user data.

## Reproducible verification

Run these commands from the `software` directory in Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_backend.ps1
powershell -ExecutionPolicy Bypass -File .\packaging\verify_backend.ps1
Set-Location .\desktop-ui
npm.cmd run build
cargo check --manifest-path .\src-tauri\Cargo.toml --locked
npm.cmd run tauri -- build --bundles nsis,msi
```

The same complete unsigned pipeline is available as one command:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_desktop.ps1
```

Use `-Offline` after the Rust crates are cached, `-Bundle nsis` or
`-Bundle msi` for one installer type, and `-Sign` only when the environment has
already been configured with the project's code-signing identity.

`verify_backend.ps1` uses an isolated temporary SQLite database and a temporary
loopback port. It verifies health and session negotiation, bundled benchmark
fixtures, the dashboard, a complete offline collaboration run, canonical event
persistence, bundled `pypdf` PDF ingestion with exact page locators, bounded
modern Office/HTML/image/audio/video extraction, Chat attachments, the
authorized approvals queue, and the database file. It terminates only the exact
process tree that it
started and deletes the temporary state unless `-KeepDatabase` is supplied.

Expected build inputs and outputs:

```text
packaging/amca-backend.spec
  -> backend-dist/amca-backend.exe
  -> bundled resource amca-backend.exe

desktop-ui/src-tauri/tauri.conf.json
  -> desktop-ui/src-tauri/target/release/bundle/nsis/*.exe
  -> desktop-ui/src-tauri/target/release/bundle/msi/*.msi
```

The MSI build requires the WiX tools used by Tauri. The NSIS build requires its
Tauri-managed NSIS tooling. On a clean machine, the first bundle build may need
network access so those tools can be downloaded.

Optional local media intelligence is installed separately so chat never
downloads executable code or model weights implicitly:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\install_media_tools.ps1
```

The script uses the Windows Package Manager Tesseract package and pins both the
official whisper.cpp `b4938` x64 archive and `ggml-tiny.en.bin` by SHA-256. The
desktop Settings page reports readiness. These third-party runtimes are not
embedded in the AMCA installer.

The desktop, backend package, and Windows bundle version is `0.2.1`.

## Installed-release checks

After installing, launch AMCA and verify:

1. The Overview screen reports `Core connected`.
2. Starting an offline collaboration produces a project and event history.
3. Close AMCA, reopen it, and confirm that the project still appears.
4. Confirm that no console window remains open beside the AMCA window.
5. Confirm `%APPDATA%\ai.amca.desktop\amca.sqlite3` exists and grows only after
   state-changing work.

The launcher owns the backend process tree it starts and terminates that exact
tree when the desktop application exits, including PyInstaller's one-file worker.
If another process is already listening on
port 8765, the launcher does not replace it; the UI's health/session exchange is
therefore still the authoritative compatibility check.

See [VERIFICATION.md](VERIFICATION.md) for the exact artifacts, hashes, and gate
results from the latest verified local build.

The current user-facing deliverables are copied to `dist/AMCA-0.2.1-setup.exe`
and `dist/AMCA-0.2.1-x64.msi`. Both are unsigned packages.
