# Cursor usage-stats hook

Logs per-turn usage stats (model, cost, tokens) to `log/usage_stats.log` after every agent turn. The `log/` directory sits next to `hooks/` in the repo root (the script resolves it as `../log/` relative to its own location, regardless of the working directory). The log rotates daily: the previous day's file is renamed to `usage_stats_<DDMMYYYY>.log`.

A single Cursor `stop` hook runs [fetch_usage_stats.py](fetch_usage_stats.py), which fetches the usage event for the current conversation from Cursor's dashboard API and writes it to the log. Nothing is printed to chat.

> **History:** the original design pushed stats into chat via `followup_message`, with a second `beforeSubmitPrompt` hook blocking the auto-submitted message. Blocked messages trigger an intrusive pop-up in Cursor, so chat output and the block hook were dropped — stats go to logs only.

## Hook registration

`.cursor/hooks.json` (project scope, paths relative to repo root):

```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "python hooks/fetch_usage_stats.py",
        "loop_limit": null,
        "timeout": 30
      }
    ]
  }
}
```

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

**Set (Windows, persistent user env):**

```powershell
setx WorkosCursorSessionToken "paste-value-here"
setx CursorTeamId "<your-team-id>"
setx CursorUserId "<your-user-id>"
```

Then fully restart Cursor (`setx` does not affect already-running processes).

**Set (Linux / macOS):** add to your shell profile (`~/.bashrc`, `~/.zshrc`, …):

```sh
export WorkosCursorSessionToken="paste-value-here"
export CursorTeamId="<your-team-id>"
export CursorUserId="<your-user-id>"
```

Then restart Cursor **from a terminal** that has the variables loaded (an app launched from the desktop/Dock may not inherit shell-profile variables; on macOS, `launchctl setenv NAME value` makes a variable visible to GUI apps until reboot).

**Token expiry:** `WorkosCursorSessionToken` is a session cookie and expires. When the log shows auth errors (HTTP 401/403), refresh the token and restart Cursor.

## Security

- Never commit the token or IDs; set them only as environment variables.
- The script never logs the token or the `Cookie` header — keep it that way.
- `log/` is gitignored.
