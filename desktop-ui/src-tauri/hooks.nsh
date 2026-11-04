; B1.3D release hygiene: remove ONLY the AMCA-owned per-user autostart
; registration during uninstall. The value name must stay in sync with the
; tauri-plugin-autostart Builder app_name ("AMCA") set in src/lib.rs.
!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "AMCA"
!macroend

; P1 installer process shutdown (ZC-AMCA-V0.6.1-P1-INSTALLER-PROCESS-SHUTDOWN-FIX-01).
;
; Root cause fixed here: the Tauri NSIS template's CheckIfAppIsRunning kills
; only the desktop binary (amca-desktop.exe) by name; a force-killed desktop
; cannot perform its graceful backend shutdown, so amca-backend.exe survives,
; keeps its executable locked, and the installer fails while replacing files.
;
; Both NSIS_HOOK_PREINSTALL and NSIS_HOOK_PREUNINSTALL run BEFORE the
; template's app check and before any file replacement or deletion, so:
;   1. stop ONLY processes that provably belong to the installed application
;      (an amca*.exe whose image path is under $INSTDIR) — gracefully first
;      via the desktop's --quit single-instance contract, then a bounded,
;      path-verified force pass;
;   2. verify the files about to be replaced/deleted can actually be opened
;      for writing NOW; if any lock survives, stop BEFORE file replacement
;      with an actionable message (fail closed, no blind retry).
; Same-named processes outside $INSTDIR (e.g. a portable copy) are never
; touched, and this hook never deletes anything.
!macro AMCA_VERIFY_UNLOCKED filePath
  ${If} ${FileExists} "${filePath}"
    ClearErrors
    FileOpen $2 "${filePath}" a
    ${If} ${Errors}
      StrCpy $3 "${filePath}"
      ${If} ${Silent}
        System::Call 'kernel32::AttachConsole(i -1)i.r4'
        ${If} $4 != 0
          System::Call 'kernel32::GetStdHandle(i -11)i.r4'
          System::Call 'kernel32::SetConsoleTextAttribute(i r4, i 0x0004)'
          FileWrite $4 "AMCA installation stopped: file is locked: $3$\n"
        ${EndIf}
      ${Else}
        MessageBox MB_ICONEXCLAMATION|MB_OK "AMCA Studio could not be updated because a required file is locked:$\n$\n$3$\n$\nClose AMCA Studio (check the system tray icon) and any program using the installation folder, then run the installer again. If this keeps happening, restart Windows and run the installer before starting AMCA.$\n$\nInstallation was stopped before any file was changed."
      ${EndIf}
      Abort
    ${Else}
      FileClose $2
    ${EndIf}
  ${EndIf}
!macroend

!macro AMCA_STOP_INSTALLED_PROCESSES
  ; makensis compiles with cwd = src-tauri/target/release/nsis/<arch>, so the
  ; helper source is reached relative to that work directory.
  File /oname=$PLUGINSDIR\amca-stop-target-processes.ps1 "..\..\..\..\installer\amca-stop-target-processes.ps1"
  DetailPrint "Stopping AMCA processes installed in $INSTDIR ..."
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\amca-stop-target-processes.ps1" -InstallDir "$INSTDIR"'
  Pop $0
  Pop $1
  ${If} $1 != ""
    DetailPrint $1
  ${EndIf}
  ${If} $0 = 0
    DetailPrint "AMCA installed processes are stopped; verifying file access."
  ${Else}
    DetailPrint "AMCA shutdown helper reported exit code $0; verifying file access."
  ${EndIf}
  !insertmacro AMCA_VERIFY_UNLOCKED "$INSTDIR\amca-desktop.exe"
  !insertmacro AMCA_VERIFY_UNLOCKED "$INSTDIR\amca-backend.exe"
!macroend

; Path-aware process handling note: the Tauri template's CheckIfAppIsRunning
; (utils.nsh, name-only match) would find and force-kill ANY current-user
; process named amca-desktop.exe — including a portable copy running from
; another directory. NSIS does not allow macro redefinition from hooks.nsh,
; so packaging/build_qa_installer_p1.ps1 patches the two call sites in the
; generated installer.nsi to no-ops when producing the QA installer: this
; hook already stopped only path-verified install-directory processes and
; verified the files unlocked (failing closed before any replacement), so
; the name-only check is redundant, and unsafe for portable instances.

; Legacy v1.0.0 upgrade bridge (H2B.2 Part A, ZCODE-AMCA-V1.0.1-H2B.2-UPGRADE-
; BRIDGE-AND-FINAL-EVIDENCE-INTEGRITY-REPAIR-02).
;
; The shipped, immutable AMCA v1.0.0 installer registered as DisplayName "AMCA",
; DisplayVersion "1.0.0" under ...\Uninstall\AMCA, while this v1.0.1 installer
; uses "${PRODUCTNAME}" = "AMCA Studio". The template's reinstall detection
; (SHCTX "${UNINSTKEY}") cannot see that key, so without this bridge a v1.0.1
; install would land side-by-side (two products, two uninstall entries,
; competing desktop/backend instances, port 8765 conflicts).
;
; The bridge helper self-decides with fail-closed corroboration (DisplayName,
; DisplayVersion, InstallLocation, main-binary ProductName/FileVersion) and
; activates ONLY for the known shipped v1.0.0 identity; anything else is
; reported and left untouched. Its removal set is closed and app-owned:
; the registry-recorded legacy install location, the legacy uninstall key,
; and the exact legacy "AMCA.lnk" shortcuts. User data (ai.amca.desktop,
; AppData app-data) is NEVER touched. Runs before AMCA_STOP_INSTALLED_PROCESSES
; so the legacy processes/files are gone before any v1.0.1 file work.
!macro AMCA_LEGACY_V100_BRIDGE
  File /oname=$PLUGINSDIR\amca-legacy-v100-bridge.ps1 "..\..\..\..\installer\amca-legacy-v100-bridge.ps1"
  File /oname=$PLUGINSDIR\amca-stop-target-processes.ps1 "..\..\..\..\installer\amca-stop-target-processes.ps1"
  DetailPrint "Checking for a legacy AMCA v1.0.0 installation (upgrade bridge) ..."
  nsExec::ExecToStack 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\amca-legacy-v100-bridge.ps1" -StopHelperPath "$PLUGINSDIR\amca-stop-target-processes.ps1" -Execute'
  Pop $0
  Pop $1
  ${If} $1 != ""
    DetailPrint $1
  ${EndIf}
  ${If} $0 = 0
    DetailPrint "Legacy v1.0.0 upgrade bridge check complete."
  ${Else}
    DetailPrint "Legacy v1.0.0 upgrade bridge helper reported exit code $0 (no activation without corroboration)."
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro AMCA_LEGACY_V100_BRIDGE
  !insertmacro AMCA_STOP_INSTALLED_PROCESSES
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro AMCA_STOP_INSTALLED_PROCESSES
!macroend
