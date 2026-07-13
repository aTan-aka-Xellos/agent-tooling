# Cursor usage-stats hook

Logs per-turn usage stats (model, cost, tokens) to `log/usage_stats.log` after every agent turn. The `log/` directory sits next to `hooks/` in the repo root (the script resolves it as `../log/` relative to its own location, regardless of the working directory). The log rotates daily: the previous day's file is renamed to `usage_stats_<DDMMYYYY>.log`.

A single Cursor `stop` hook runs [fetch_usage_stats.sh](fetch_usage_stats.sh), which loads credentials from macOS Keychain, then runs [fetch_usage_stats.py](fetch_usage_stats.py) to fetch the usage event for the current conversation from Cursor's dashboard API and write it to the log. Nothing is printed to chat.

> **History:** the original design pushed stats into chat via `followup_message`, with a second `beforeSubmitPrompt` hook blocking the auto-submitted message. Blocked messages trigger an intrusive pop-up in Cursor, so chat output and the block hook were dropped — stats go to logs only.

## Hook registration

`.cursor/hooks.json` (project scope, paths relative to repo root):

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "hooks/fetch_usage_stats.sh",
        "loop_limit": null,
        "timeout": 30
      }
    ]
  }
}
```

The shell wrapper reads the three secrets from Keychain, exports them as environment variables, then execs the Python script. `hooks.json` cannot set env vars itself — the wrapper is what makes them visible to Python via `os.environ`.

## Behavior

On each `stop` event the script:

1. Reads `conversation_id` from stdin (Cursor hook payload).
2. Validates the required environment variables (below).
3. Waits 5 s (the usage event is not available immediately after the turn), then POSTs to
   `https://cursor.com/api/dashboard/get-filtered-usage-events` with a date window of
   now − 1 h … now + 12 h (epoch ms as strings), `page: 1`, `pageSize: 10`.
   If the response has no events, retries once after another 5 s.
4. Picks the latest event (highest `timestamp`) matching `conversationId` and logs it as pretty-printed JSON.

All errors (missing env, HTTP/network failures, no matching event) are logged to the same file; the script always exits 0 and never writes to stdout, so it never affects the chat flow.

Example log entry:

```json
{
  "conversationId": "6f1b2c3d-4e5a-4b7c-8d9e-0a1b2c3d4e5f",
  "timestamp": "1783870921456",
  "timestampUtc": "Jul 12, 03:42:01 PM (UTC)",
  "timestampJst": "Jul 13, 12:42:01 AM (JST)",
  "model": "composer-2.5",
  "cursorTokenFee": 0,
  "requestsCosts": 1.2999999523162842,
  "chargedCents": 5.227043151855469,
  "tokenUsage": {
    "inputTokens": 6302,
    "outputTokens": 1202,
    "cacheReadTokens": 249004,
    "totalCents": 5.388702869415283
  }
}
```

## Environment variables (required)

> **Note:** this setup targets a **team** subscription (the API takes a `teamId`); it has not been tested with an individual subscription.

| Variable | Used for |
|----------|----------|
| `WorkosCursorSessionToken` | `Cookie: WorkosCursorSessionToken=<value>` on API requests |
| `CursorTeamId` | Request body `teamId` (integer) |
| `CursorUserId` | Request body `userId` (integer) |

**Obtain the values:**

1. Open [cursor.com](https://cursor.com) in a browser (logged in).
2. Token: DevTools → Application → Cookies → `https://cursor.com` → `WorkosCursorSessionToken`.
3. `teamId` / `userId`: DevTools → Network → open the dashboard usage page → `get-filtered-usage-events` request body.

**Set (macOS, Keychain — recommended):**

Each secret is stored under a Keychain lookup id: `-a "$USER"` + `-s <service>`. Run once (or again with `-U` to update):

```sh
security add-generic-password -a "$USER" -s cursor-session  -w "put_session_token_here" -U
security add-generic-password -a "$USER" -s cursor-team-id    -w "put_team_id_here"     -U
security add-generic-password -a "$USER" -s cursor-user-id  -w "put_user_id_here"     -U
```

| Keychain service (`-s`) | Env var (set by wrapper) | Value to store |
|-------------------------|--------------------------|----------------|
| `cursor-session`        | `WorkosCursorSessionToken` | session cookie from DevTools |
| `cursor-team-id`        | `CursorTeamId`             | team id (integer as string) |
| `cursor-user-id`        | `CursorUserId`             | user id (integer as string) |

The wrapper [fetch_usage_stats.sh](fetch_usage_stats.sh) reads these and exports them before starting Python:

```sh
export WorkosCursorSessionToken="$(security find-generic-password -a "$USER" -s cursor-session -w)"
export CursorTeamId="$(security find-generic-password -a "$USER" -s cursor-team-id -w)"
export CursorUserId="$(security find-generic-password -a "$USER" -s cursor-user-id -w)"
```

On first run, macOS may prompt once to allow Cursor to access Keychain — click Allow. No need to restart Cursor after updating Keychain entries.

**Set (Windows, persistent user env):**

> **Note:** this is not secure — `setx` stores values as plaintext in the user environment (registry). Any process running as your user can read them. Prefer a secret store (e.g. Windows Credential Manager) if you need better protection.

```powershell
setx WorkosCursorSessionToken "paste-value-here"
setx CursorTeamId "<your-team-id>"
setx CursorUserId "<your-user-id>"
```

Then fully restart Cursor (`setx` does not affect already-running processes). On Windows, point `hooks.json` at Python directly (`python hooks/fetch_usage_stats.py`) instead of the shell wrapper.

**Set (Linux):** add to your shell profile (`~/.bashrc`, `~/.zshrc`, …) and start Cursor from that terminal:

```sh
export WorkosCursorSessionToken="paste-value-here"
export CursorTeamId="<your-team-id>"
export CursorUserId="<your-user-id>"
```

On Linux, use `python hooks/fetch_usage_stats.py` in `hooks.json` (no Keychain wrapper).

**Token expiry:** `WorkosCursorSessionToken` is a session cookie and expires. When the log shows auth errors (HTTP 401/403), re-run the `security add-generic-password … -U` command for `cursor-session` with the new token.

## View logs

Stats are written to `~/.cursor/log/usage_stats.log` (user hooks) or `log/usage_stats.log` next to `hooks/` (project hooks).

To follow the log live, add to `~/.zshrc`:

```sh
alias usage='tail -n 17 -f ~/.cursor/log/usage_stats.log'
```

Then run `usage` in any terminal.

## Security

- Never commit the token or IDs; on macOS store them in Keychain (or set as environment variables on other OSes).
- The script never logs the token or the `Cookie` header — keep it that way.
- `log/` is gitignored.
