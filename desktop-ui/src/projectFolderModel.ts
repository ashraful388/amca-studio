/**
 * v1.2.0 - the Project folder.
 *
 * A Project's folder is chosen once, when the Project is created, and AMCA
 * creates the directory if it does not exist. It is NOT a per-chat setting:
 * the message composer no longer offers a folder chooser.
 *
 * The default folder shown in the form comes from the backend, not from a
 * second sanitiser here: the backend owns the naming rule, so the path the
 * user sees is exactly the one that gets created.
 *
 * The picker is a convenience. The text field beside it stays editable, so a
 * failed or cancelled native dialog can never block Project creation.
 */

export interface PickFolderOutcome {
  /** True when a folder was chosen. A cancel is NOT an error. */
  ok: boolean;
  /** The chosen path, or the unchanged current value. */
  path: string;
  /** Present only when the picker itself could not run. */
  reason?: string;
}

const PICKER_UNAVAILABLE =
  "The folder picker is unavailable here. Type the folder path instead.";
const PICKER_FAILED = "The folder picker could not be opened. Type the folder path instead.";

/**
 * Open the native Windows folder chooser.
 *
 * Returns `{ ok: false }` without a reason when the user cancels, and
 * `{ ok: false, reason }` when the dialog could not be shown. The caller keeps
 * the text field in both cases.
 */
export async function pickProjectFolder(current: string): Promise<PickFolderOutcome> {
  type InvokeFn = (command: string, args?: Record<string, unknown>) => Promise<unknown>;
  let invoke: InvokeFn | null = null;
  try {
    const api = await import("@tauri-apps/api/core");
    invoke = api.invoke as unknown as InvokeFn;
  } catch {
    return { ok: false, path: current, reason: PICKER_UNAVAILABLE };
  }
  if (!invoke) {
    return { ok: false, path: current, reason: PICKER_UNAVAILABLE };
  }
  try {
    const chosen = await invoke("pick_project_folder");
    if (typeof chosen !== "string" || !chosen.trim()) {
      return { ok: false, path: current }; // cancelled
    }
    return { ok: true, path: chosen };
  } catch {
    // The native side deliberately returns path-free messages, but a driver or
    // IPC failure can still surface anything. Do not forward raw text to the
    // UI; the editable field is the fallback either way.
    return { ok: false, path: current, reason: PICKER_FAILED };
  }
}
