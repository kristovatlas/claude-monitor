# 🟢 Claude Monitor

A macOS menu bar app that shows your Claude subscription usage in real time — the same data you see in the Claude desktop app under **Settings → Usage**, always visible at a glance.

```
  🟢 24│11%        ← 5-hour │ 7-day utilization
```

Click the icon for a detailed dropdown:

> **⚠️ Disclaimer:** This tool uses an undocumented Anthropic API endpoint and an internal OAuth client ID observed in Claude Code's network traffic. It is **not affiliated with or endorsed by Anthropic**. The API may change without notice. Review Anthropic's [Terms of Service](https://www.anthropic.com/terms) before use.

```
── ACCOUNT ──
  ●  Personal (Pro)
  ○  Work (Team)

── 5-HOUR WINDOW ──
  █████░░░░░░░░░░░░░░░░░  24.0%
  ⏱  Resets in 3h 12m  (4:35 PM)

── 7-DAY WINDOW ──
  ██░░░░░░░░░░░░░░░░░░░░  11.0%
  ⏱  Resets in 29h 36m  (Wed 6:00 PM)

── 7-DAY OPUS ──
  (no Opus usage)

  📋  Plan: PRO  •  Personal (Pro)
  🕐  Last refresh: 1:54:12 PM  (every 120s)

  🔄  Refresh Now
  ⚙️   Edit Config…
  Quit
```

## Features

- **Real usage data** — calls the same Anthropic API endpoint as the Claude desktop app. No token estimation or guesswork.
- **Zero cost** — reads an account metadata endpoint, not a model inference call. No tokens consumed, nothing billed.
- **Auto-refreshing** — updates every 2 minutes (configurable). Auto-refreshes expired OAuth tokens using your keychain credentials.
- **Multi-account** — switch between personal and enterprise accounts from the dropdown.
- **Color-coded** — 🟢 green (< 50%) → 🟡 yellow (50–80%) → 🔴 red (> 80%). Dot color tracks whichever window is worse.
- **Compact** — shows both 5-hour and 7-day percentages in the menu bar: `🟢 24│11%`

## Requirements

- **macOS** (uses macOS Keychain + native menu bar via PyObjC)
- **Claude Code** installed and logged in (this is where the OAuth token comes from)
- **Python 3.9+**

## Install

```bash
git clone https://github.com/kristovatlas/claude-monitor.git
cd claude-monitor
chmod +x install.sh
./install.sh
```

The installer will:
1. Create a Python virtual environment (pyenv or venv)
2. If using pyenv, ensure Python is built with `--enable-framework` (required for macOS menu bar)
3. Install dependencies (`rumps`, `requests`)
4. Create a `claude-monitor` launcher in `~/.local/bin/`
5. Test your API connection
6. Optionally install a launchd plist for auto-start at login

### Manual install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python claude_menubar.py
```

## Usage

```bash
claude-monitor                                  # launch the menu bar app
claude-monitor --test                           # test API connection & print usage
claude-monitor --add-profile work "Work (Team)" # add a second account
claude-monitor --refresh-profile work           # re-snapshot an expired token
claude-monitor --install                        # print launchd plist for auto-start
claude-monitor --help                           # full usage info
```

### First run

Make sure you're logged into Claude Code:

```bash
claude          # log in if prompted, then exit
claude-monitor  # 🟢 should appear in your menu bar
```

The app reads your OAuth token from the macOS Keychain (where Claude Code stores it) and calls `GET https://api.anthropic.com/api/oauth/usage` to get your real utilization percentages.

### Multi-account setup

You can switch between accounts (e.g., personal Pro and work Enterprise) from the menu bar dropdown.

```bash
# 1. Log into your personal account in Claude Code
claude
# log in, then exit

# 2. Snapshot that token as a profile
claude-monitor --add-profile personal "Personal (Pro)"

# 3. Log into your work account in Claude Code
claude /logout
claude
# log in with work account, then exit

# 4. Snapshot that token too
claude-monitor --add-profile work "Work (Enterprise)"

# 5. Launch — switch profiles from the dropdown
claude-monitor
```

The **Auto (Keychain)** profile always uses whatever account is currently logged into Claude Code — no snapshotting needed.

Snapshotted tokens eventually expire. When one does, log into that account in Claude Code and run:

```bash
claude-monitor --refresh-profile work
```

### Auto-start at login

Either say `y` during `./install.sh`, or:

```bash
claude-monitor --install
# Follow the printed instructions to save the plist and load it
```

To stop the auto-start:

```bash
launchctl unload ~/Library/LaunchAgents/com.claude.menubar-monitor.plist
```

## Configuration

Config lives at `~/.config/claude-monitor/config.json` (created on first run):

```json
{
  "active_profile": "auto",
  "refresh_seconds": 120,
  "profiles": {
    "auto": {
      "label": "Auto (Keychain)",
      "source": "keychain"
    },
    "personal": {
      "label": "Personal (Pro)",
      "source": "profile_keychain",
      "plan": "pro"
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `refresh_seconds` | How often to poll the API (default: 120). Safe to set as low as 30 — the endpoint is free. |
| `active_profile` | Which profile is selected on launch. |
| `profiles.*.source` | `"keychain"` = live from Keychain, `"profile_keychain"` = snapshotted in Keychain. |

Click **⚙️ Edit Config…** in the dropdown to open it directly. **No secrets are stored in the config file** — all tokens live in the macOS Keychain.

## How it works

1. **Token source:** Claude Code stores OAuth credentials in the macOS Keychain under `"Claude Code-credentials"`. The app reads the access token from there.

2. **Usage API:** Calls `GET https://api.anthropic.com/api/oauth/usage` with the OAuth bearer token — the same endpoint the Claude desktop app uses for Settings → Usage. Returns utilization percentages for 5-hour and 7-day windows.

3. **Token refresh:** Access tokens are short-lived. When one expires, the app automatically exchanges the refresh token at `POST https://console.anthropic.com/v1/oauth/token` and writes the new token back to the Keychain.

4. **Menu bar rendering:** Uses [rumps](https://github.com/jaredks/rumps) (Ridiculously Uncomplicated macOS Python Statusbar apps). On launch, the app re-execs itself using the macOS framework Python binary so the system grants it menu bar privileges.

## API response format

The usage endpoint returns:

```json
{
  "five_hour":    { "utilization": 24.0, "resets_at": "2026-02-10T23:59:59Z" },
  "seven_day":    { "utilization": 11.0, "resets_at": "2026-02-12T04:59:59Z" },
  "seven_day_opus":   { "utilization": 0.0, "resets_at": null },
  "seven_day_sonnet": { "utilization": 0.0, "resets_at": null },
  "seven_day_cowork": null,
  "extra_usage": {
    "is_enabled": true,
    "monthly_limit": null,
    "used_credits": 0.0,
    "utilization": null
  }
}
```

All utilization values are percentages (0–100) calculated server-side by Anthropic. No local token counting or limit estimation.

## Troubleshooting

**No icon in menu bar**
- Your menu bar might be full (especially with a notch). Try removing another icon to make room.
- The installer verifies framework Python support. If it shows ✔ for AppKit, the icon should work.

**"Token expired — re-login to Claude Code"**
- The refresh token has expired (happens after ~2 weeks of inactivity). Run `claude`, log in, then try again.

**"No Claude Code credentials found"**
- Make sure Claude Code is installed and you've logged in at least once: `claude`

**Dock icon appears instead of menu bar**
- The app should auto-detect and re-launch with the framework Python binary. If it doesn't, try running directly with Homebrew Python:
  ```bash
  /opt/homebrew/bin/python3 claude_menubar.py
  ```

**Numbers seem wrong**
- The percentages come directly from Anthropic's servers — they're the same numbers shown in the Claude desktop app. If they seem off, check Settings → Usage in Claude to compare.

## Security

- **No secrets in config files.** All OAuth tokens are stored in the macOS Keychain — the config JSON contains only metadata (profile names, plan types).
- **Keychain access uses absolute paths** (`/usr/bin/security`) to prevent PATH injection attacks.
- **Token refresh responses are validated** before being written to the Keychain — malformed responses are rejected.
- **Config file is `chmod 600`** — readable only by the owning user.
- **Undocumented API.** This tool uses an internal Anthropic endpoint and OAuth client ID reverse-engineered from Claude Code's network traffic. It could break if Anthropic changes their internal APIs, and use may be subject to their Terms of Service.

## License

MIT