// A release build is a GUI application: it must NOT be a console-subsystem
// executable.
//
// Without this attribute the shipped amca-desktop.exe owns a console. That
// console window is visible behind the app, and it RE-RAISES itself every
// time the app or one of its children writes to stdout/stderr — so a CMD
// window appears on launch and keeps popping up over the UI during ordinary
// use. The attribute makes the release binary a pure GUI process with no
// console at all. Debug builds keep the console on purpose: the backend's
// stderr is inherited there so a crash leaves a traceback in the tauri dev
// log.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    amca_desktop_lib::run();
}
