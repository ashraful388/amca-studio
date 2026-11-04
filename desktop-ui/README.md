# AMCA Desktop

The AMCA desktop client is a React/TypeScript interface over the authoritative
Python core. During development it talks to the loopback-only service in
`amca.service`; the UI never owns canonical project state.

## Development

From the `software` directory, start the Python service:

```powershell
$env:PYTHONPATH = "src"
python -m amca.service
```

Then, from `software/desktop-ui`:

```powershell
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:1420`. The Tauri 2 shell lives under `src-tauri` and is
built after the Windows Rust/MSVC prerequisites are installed.

## Use a real local model

R1.5 adds an offline Chat screen backed by the real AMCA kernel:

1. Start Ollama and make sure at least one model is already installed. AMCA
   never downloads a model automatically.
2. Open AMCA Desktop and choose **Chat**.
3. Choose **Auto Intelligence** or **Manual** in the model selector.
4. Open **Settings → Model Hub → Local Models**, select **Scan Ollama**, then choose an installed model such as
   `qwen3:1.7b`.
5. Enter a message and select **Send**. Enter sends; Shift+Enter adds a line.
6. Select **Stop** to request cancellation. Finished and interrupted desktop
   runs are restored after the application restarts.

The Chat header exposes only **Auto Intelligence** and **Manual**. Auto chooses
the lowest verified expected-cost eligible route after quality, capability,
privacy, reliability, and Goal Contract gates. Manual preserves the exact
logical model set or saved Model Team; AMCA selects the internal collaboration
architecture. Any cloud participant still opens the exact-scope Approvals view
before network access.

Desktop Chat uses one bounded round for Solo and at most two bounded rounds for
Joint so participants can inspect, challenge, and revise shared contributions.
It supports at most eight participants and 4,096 requested output tokens per
call. Local models use Offline privacy and zero API budget. API models use a
minimized Private Hybrid excerpt, model-scoped credentials, conservative cost
reservation from the saved pricing profile, and approvals bounded to the exact
participant and collaboration-round count. A terminal `Partial` label means models returned public responses but
did not satisfy AMCA's stricter evidence-backed Goal Contract; it is not a
transport failure.

## Saved chats

AMCA Desktop now keeps a local ChatGPT-style conversation list in the left
sidebar. **New chat** creates an independent conversation; each chat remembers
its Solo/Joint mode, selected model participants, messages, latest project, and
active run. Use the chat row actions to rename or delete it, and use **Search
chats** to match titles or message text. Closing and reopening AMCA restores the
same list. An interrupted run is marked failed on restart and the conversation
is released instead of remaining permanently busy.

Conversation text is stored in the same local SQLite database as AMCA state but
in separate desktop tables. Credential-shaped content is rejected before any
project or chat write. API secrets remain in the protected Windows credential
vault and are never copied into chat history.

**Settings → Model Hub** is the single model-access configuration surface. It provides:

- AMCA API managed-access status without exposing AWS credentials;
- provider-centric BYOK setup without requiring a default model or pricing;
- hardware-aware local model cards with deterministic green/red compatibility;
- persistent mixed-source Model Teams;
- MAIA endpoint/catalog/pricing intelligence status;
- write-only Save/Replace/Remove controls; and
- a cloud/local team selector for executable Solo and Joint runs.

Credentials are protected by Windows Credential Manager, with a per-user
DPAPI-encrypted fallback file when Credential Manager is unavailable. They are
never returned to the React client or stored in AMCA SQLite. Paste each key
into its matching model card; the input is cleared immediately after a
successful save. Selecting models does not authorize a paid request. The Chat
screen shows configured local and API-backed choices in Solo mode and uses the
same selected participants for Joint mode. API-backed execution always stops at
the Approvals view before network access.

To add cloud access, open **Settings → Model Hub → BYOK Providers → Add
provider**. Save one provider credential; a default model and pricing are not
required. The credential is saved separately in Windows protection, while only
sanitized provider, endpoint, catalog, and pricing metadata may enter SQLite.
Saving the provider performs no inference call and cannot incur an API charge.
Use **Discover models** to make one explicit bounded catalog request with no chat
content, or import fallback model IDs when a provider has no usable list API.
Discovered models appear dynamically under Manual and Model Teams and are also
eligible for Auto Intelligence subject to policy and cost-quality gates.

Installed Ollama models require no profile: press **Scan** in Model Library,
then choose **Use in Chat** by selecting a model card. AMCA never downloads or
deletes Ollama models automatically.
