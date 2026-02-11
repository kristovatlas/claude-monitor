#!/usr/bin/env python3
"""
Claude Code Menu Bar Monitor
═════════════════════════════
Real-time subscription usage in your macOS menu bar, using the same
endpoint the Claude desktop app uses (Settings → Usage).

Shows exact utilization percentages from Anthropic's servers — no
token estimation or guesswork.

  🟢 12%  →  🟡 65%  →  🔴 87%

Supports multiple accounts (personal / enterprise) with one-click
switching from the menu bar.

SETUP
─────
  pip3 install rumps requests

  # If you have Claude Code installed & logged in, just run it:
  python3 claude_menubar.py

  # It auto-reads your OAuth token from macOS Keychain.

MULTI-ACCOUNT
─────────────
  First run creates ~/.config/claude-monitor/config.json

  To add a second account:
  1. Log into that account in Claude Code:  claude /logout  then  claude
  2. Run:  python3 claude_menubar.py --add-profile work "Work (Enterprise)"
     — this snapshots the current keychain token into a new profile
  3. Switch between profiles from the menu bar dropdown

  Or edit config.json directly (see --help for format).

  Tokens expire, so the app auto-refreshes them from the keychain
  when the active profile is the "auto" (keychain) source.

HOW IT WORKS
────────────
  Claude Code stores OAuth credentials in macOS Keychain under
  "Claude Code-credentials". The token is used to call:

    GET https://api.anthropic.com/api/oauth/usage

  This returns real utilization percentages — the same data shown
  in the Claude desktop app under Settings → Usage.
"""

import rumps
import json
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency.\nRun:  pip3 install rumps requests")
    sys.exit(1)


# ── Config ───────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".config" / "claude-monitor"
CONFIG_FILE = CONFIG_DIR / "config.json"

USAGE_URL    = "https://api.anthropic.com/api/oauth/usage"
REFRESH_URL  = "https://console.anthropic.com/v1/oauth/token"
CLIENT_ID    = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

KEYCHAIN_SVC = "Claude Code-credentials"
CREDS_FILE   = Path.home() / ".claude" / ".credentials.json"

BAR_WIDTH        = 22
YELLOW_THRESHOLD = 50
RED_THRESHOLD    = 80
DEFAULT_REFRESH  = 120   # seconds — API is lightweight but be polite


# ── Keychain / Credentials ───────────────────────────────────────

def _read_raw_creds():
    """
    Read Claude Code OAuth credentials from macOS Keychain,
    falling back to ~/.claude/.credentials.json.
    Returns the full creds dict or None.
    """
    # Try Keychain first
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SVC, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception:
        pass

    # Fallback: credentials file
    try:
        if CREDS_FILE.exists():
            with open(CREDS_FILE) as f:
                return json.load(f)
    except Exception:
        pass

    return None


def _write_keychain_creds(creds):
    """Write updated credentials back to macOS Keychain."""
    try:
        creds_json = json.dumps(creds)
        # Delete old entry first (security won't overwrite)
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SVC],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["security", "add-generic-password",
             "-a", os.environ.get("USER", "claude"),
             "-s", KEYCHAIN_SVC,
             "-w", creds_json],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def _refresh_access_token(refresh_token):
    """
    Exchange a refresh token for a new access token via Anthropic's
    OAuth endpoint — the same flow Claude Code uses internally.
    Returns (new_access_token, new_expires_at) or (None, None).
    """
    try:
        resp = requests.post(REFRESH_URL, json={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }, headers={"Content-Type": "application/json"}, timeout=15)

        if not resp.ok:
            return None, None

        data = resp.json()
        return data.get("access_token"), data.get("expires_at")
    except Exception:
        return None, None


def read_keychain_token():
    """
    Read the OAuth access token. If it's expired, automatically
    refresh it using the refresh token and update the Keychain.
    Returns (access_token, subscription_type) or (None, None).
    """
    creds = _read_raw_creds()
    if not creds:
        return None, None

    oauth = creds.get("claudeAiOauth", {})
    token = oauth.get("accessToken")
    refresh = oauth.get("refreshToken")
    expires = oauth.get("expiresAt", 0)
    plan  = oauth.get("subscriptionType", "unknown")

    if not token:
        return None, None

    # Check if token is expired (expiresAt is in milliseconds)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if expires and now_ms > expires and refresh:
        # Token is expired — try to refresh it
        new_token, new_expires = _refresh_access_token(refresh)
        if new_token:
            oauth["accessToken"] = new_token
            if new_expires:
                oauth["expiresAt"] = new_expires
            creds["claudeAiOauth"] = oauth
            _write_keychain_creds(creds)
            return new_token, plan
        else:
            # Refresh failed — still return the old token to try,
            # the API will tell us if it's truly dead
            return token, plan

    return token, plan


# ── Anthropic Usage API ──────────────────────────────────────────

def fetch_usage(token):
    """
    Call the same endpoint the Claude desktop app uses.
    Returns the raw JSON dict or None on error.
    """
    try:
        resp = requests.get(USAGE_URL, headers={
            "Accept":           "application/json",
            "Content-Type":     "application/json",
            "User-Agent":       "claude-code/2.1.0",
            "Authorization":    f"Bearer {token}",
            "anthropic-beta":   "oauth-2025-04-20",
        }, timeout=15)

        if resp.status_code == 401:
            # Token might have just expired — try a refresh
            creds = _read_raw_creds()
            if creds:
                refresh_tok = creds.get("claudeAiOauth", {}).get("refreshToken")
                if refresh_tok:
                    new_token, new_exp = _refresh_access_token(refresh_tok)
                    if new_token:
                        # Update keychain and retry once
                        creds["claudeAiOauth"]["accessToken"] = new_token
                        if new_exp:
                            creds["claudeAiOauth"]["expiresAt"] = new_exp
                        _write_keychain_creds(creds)

                        resp2 = requests.get(USAGE_URL, headers={
                            "Accept":         "application/json",
                            "Content-Type":   "application/json",
                            "User-Agent":     "claude-code/2.1.0",
                            "Authorization":  f"Bearer {new_token}",
                            "anthropic-beta": "oauth-2025-04-20",
                        }, timeout=15)
                        if resp2.ok:
                            return resp2.json()

            return {"_error": "Token expired — run: claude (and re-login)"}
        if resp.status_code == 403:
            return {"_error": "Access denied (403)"}
        if not resp.ok:
            return {"_error": f"HTTP {resp.status_code}"}

        return resp.json()

    except requests.exceptions.ConnectionError:
        return {"_error": "No network connection"}
    except requests.exceptions.Timeout:
        return {"_error": "Request timed out"}
    except Exception as e:
        return {"_error": str(e)[:60]}


# ── Config Management ────────────────────────────────────────────

def default_config():
    return {
        "active_profile": "auto",
        "refresh_seconds": DEFAULT_REFRESH,
        "profiles": {
            "auto": {
                "label": "Auto (Keychain)",
                "source": "keychain",
            }
        },
    }


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            # Ensure "auto" profile always exists
            cfg.setdefault("profiles", {}).setdefault("auto", {
                "label": "Auto (Keychain)",
                "source": "keychain",
            })
            return cfg
        except Exception:
            pass
    return default_config()


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def add_profile_from_keychain(name, label):
    """Snapshot current keychain token into a named profile."""
    token, plan = read_keychain_token()
    if not token:
        print("❌ No Claude Code credentials found in Keychain.")
        print("   Log into Claude Code first, then retry.")
        sys.exit(1)

    cfg = load_config()
    cfg["profiles"][name] = {
        "label": label,
        "source": "token",
        "token": token,
        "plan": plan,
    }
    save_config(cfg)
    print(f"✅ Profile '{name}' added: {label} ({plan} plan)")
    print(f"   Token snapshotted. If it expires, switch to this account")
    print(f"   in Claude Code and run:  python3 {sys.argv[0]} --refresh-profile {name}")


def refresh_profile_token(name):
    """Re-snapshot the keychain token for a named profile."""
    token, plan = read_keychain_token()
    if not token:
        print("❌ No Claude Code credentials in Keychain.")
        sys.exit(1)
    cfg = load_config()
    if name not in cfg["profiles"]:
        print(f"❌ Profile '{name}' not found.")
        sys.exit(1)
    cfg["profiles"][name]["token"] = token
    cfg["profiles"][name]["plan"] = plan
    save_config(cfg)
    print(f"✅ Token refreshed for '{name}' ({plan})")


# ── Formatting ───────────────────────────────────────────────────

def bar(pct):
    filled = round(pct / 100 * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)

def dot(pct):
    if pct >= RED_THRESHOLD:    return "🔴"
    if pct >= YELLOW_THRESHOLD: return "🟡"
    return "🟢"

def time_until(iso_str):
    """Format an ISO reset time as '2h 14m'."""
    if not iso_str:
        return None
    try:
        resets = datetime.fromisoformat(iso_str)
        remaining = resets - datetime.now(timezone.utc)
        if remaining.total_seconds() <= 0:
            return "reset!"
        total_min = int(remaining.total_seconds() // 60)
        h, m = divmod(total_min, 60)
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return None


def local_time(iso_str):
    """Convert ISO string to local time display."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str).astimezone()
        return dt.strftime("%I:%M %p")
    except Exception:
        return ""


# ── Menu Bar App ─────────────────────────────────────────────────

class ClaudeMenuBar(rumps.App):

    def __init__(self, config):
        super().__init__("⏳", quit_button=None)
        self.config    = config
        self.refresh_s = config.get("refresh_seconds", DEFAULT_REFRESH)
        self.active    = config.get("active_profile", "auto")

        # ── Menu skeleton ────────────────────────────────────────

        # Profile section
        self.hdr_acct     = rumps.MenuItem("── ACCOUNT ──")
        self.profile_items = {}
        for pname, pdata in config["profiles"].items():
            lbl = pdata.get("label", pname)
            item = rumps.MenuItem(
                f"  {'●' if pname == self.active else '○'}  {lbl}",
                callback=self._make_switch(pname),
            )
            self.profile_items[pname] = item

        # 5-hour section
        self.hdr5       = rumps.MenuItem("── 5-HOUR WINDOW ──")
        self.bar5       = rumps.MenuItem("  loading…")
        self.reset5     = rumps.MenuItem("  …")

        # Weekly section
        self.hdrW       = rumps.MenuItem("── 7-DAY WINDOW ──")
        self.barW       = rumps.MenuItem("  loading…")
        self.resetW     = rumps.MenuItem("  …")

        # Opus section (shown if present)
        self.hdr_opus   = rumps.MenuItem("── 7-DAY OPUS ──")
        self.bar_opus   = rumps.MenuItem("  …")
        self.show_opus  = False

        # Footer
        self.plan_line  = rumps.MenuItem("  …")
        self.status     = rumps.MenuItem("  …")
        self.error_line = rumps.MenuItem("")

        self.menu = [
            self.hdr_acct,
            *self.profile_items.values(),
            None,
            self.hdr5,
            self.bar5,
            self.reset5,
            None,
            self.hdrW,
            self.barW,
            self.resetW,
            None,
            self.hdr_opus,
            self.bar_opus,
            None,
            self.plan_line,
            self.status,
            self.error_line,
            None,
            rumps.MenuItem("🔄  Refresh Now", callback=self._refresh),
            rumps.MenuItem("⚙️   Edit Config…", callback=self._open_config),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]

        self._refresh(None)
        self.timer = rumps.Timer(self._refresh, self.refresh_s)
        self.timer.start()

        # Force macOS to render the menu bar icon immediately.
        # Without this, the icon won't appear until something else
        # triggers a menu bar redraw.
        self._kickstart = rumps.Timer(self._force_redraw, 0.5)
        self._kickstart.count = 0
        self._kickstart.start()

    def _force_redraw(self, sender):
        """Nudge the menu bar by toggling the title — forces macOS to render."""
        sender.count += 1
        if sender.count >= 3:
            sender.stop()
            return
        # Append/remove a zero-width space to force a redraw
        if self.title and not self.title.endswith("\u200b"):
            self.title = self.title + "\u200b"
        else:
            self.title = self.title.rstrip("\u200b")

    # ── Profile switching ────────────────────────────────────────

    def _make_switch(self, name):
        def cb(_):
            self.active = name
            for pn, item in self.profile_items.items():
                lbl = self.config["profiles"][pn].get("label", pn)
                item.title = f"  {'●' if pn == self.active else '○'}  {lbl}"
            self.config["active_profile"] = name
            save_config(self.config)
            self._refresh(None)
        return cb

    # ── Get token for active profile ─────────────────────────────

    def _get_token(self):
        profile = self.config["profiles"].get(self.active, {})
        if profile.get("source") == "keychain":
            token, plan = read_keychain_token()
            return token, plan
        else:
            return profile.get("token"), profile.get("plan", "?")

    # ── Refresh ──────────────────────────────────────────────────

    def _refresh(self, _sender):
        now_str = datetime.now().strftime("%I:%M:%S %p")
        profile_label = self.config["profiles"].get(
            self.active, {}
        ).get("label", self.active)

        token, plan = self._get_token()

        if not token:
            self.title = "CC ⚠️"
            self.bar5.title = "  ⚠️  No OAuth token found"
            self.reset5.title = "  Log into Claude Code, then restart."
            self.barW.title = ""
            self.resetW.title = ""
            self.error_line.title = "  Run: claude   (and log in)"
            self.status.title = f"  🕐  {now_str}"
            return

        data = fetch_usage(token)

        if "_error" in data:
            self.title = "CC ⚠️"
            self.bar5.title = f"  ⚠️  {data['_error']}"
            self.reset5.title = ""
            self.barW.title = ""
            self.resetW.title = ""
            self.error_line.title = f"  ❌  {data['_error']}"
            self.status.title = f"  🕐  {now_str}  •  {profile_label}"
            return

        self.error_line.title = ""

        # ── 5-hour window ────────────────────────────────────
        fh = data.get("five_hour")
        sd = data.get("seven_day")
        pct5 = fh.get("utilization", 0) if fh else 0
        pctw = sd.get("utilization", 0) if sd else 0

        # Menu bar: show both — dot color is worst of the two
        worst = max(pct5, pctw)
        self.title = f"{dot(worst)} {pct5:.0f}│{pctw:.0f}%"

        if fh:
            self.bar5.title  = f"  {bar(pct5)}  {pct5:.1f}%"
            remain = time_until(fh.get("resets_at"))
            reset_local = local_time(fh.get("resets_at"))
            if remain:
                self.reset5.title = f"  ⏱  Resets in {remain}  ({reset_local})"
            else:
                self.reset5.title = "  ⏱  Window inactive"
        else:
            self.bar5.title  = f"  {bar(0)}  0%"
            self.reset5.title = "  ⏱  No active window"

        # ── 7-day window ─────────────────────────────────────
        if sd:
            self.barW.title  = f"  {bar(pctw)}  {pctw:.1f}%"
            remain_w = time_until(sd.get("resets_at"))
            reset_w_local = local_time(sd.get("resets_at"))
            if remain_w:
                self.resetW.title = f"  ⏱  Resets in {remain_w}  ({reset_w_local})"
            else:
                self.resetW.title = ""
        else:
            self.barW.title  = f"  {bar(0)}  0%"
            self.resetW.title = ""

        # ── Opus sub-limit ───────────────────────────────────
        opus = data.get("seven_day_opus")
        if opus and opus.get("utilization", 0) > 0:
            pct_o = opus["utilization"]
            self.bar_opus.title = f"  {bar(pct_o)}  {pct_o:.1f}%"
            self.hdr_opus.title = "── 7-DAY OPUS ──"
        else:
            self.hdr_opus.title = "── 7-DAY OPUS ──"
            self.bar_opus.title = "  (no Opus usage)"

        # ── Footer ───────────────────────────────────────────
        self.plan_line.title = f"  📋  Plan: {(plan or '?').upper()}  •  {profile_label}"
        self.status.title    = f"  🕐  Last refresh: {now_str}  (every {self.refresh_s}s)"

    # ── Open config ──────────────────────────────────────────

    def _open_config(self, _sender):
        if not CONFIG_FILE.exists():
            save_config(self.config)
        os.system(f"open '{CONFIG_FILE}'")


# ── CLI ──────────────────────────────────────────────────────────

USAGE_TEXT = """
Claude Code Menu Bar Monitor
═════════════════════════════

Usage:
  python3 claude_menubar.py                              Launch (auto-reads Keychain)
  python3 claude_menubar.py --add-profile NAME "LABEL"   Snapshot current Keychain token as a profile
  python3 claude_menubar.py --refresh-profile NAME       Re-snapshot token for expired profile
  python3 claude_menubar.py --install                    Print launchd plist for auto-start
  python3 claude_menubar.py --test                       Test API connection and print usage

Requires:
  pip3 install rumps requests

Multi-account setup:
  1. Log into your personal account in Claude Code
  2. python3 claude_menubar.py --add-profile personal "Personal (Pro)"
  3. Log into your enterprise account in Claude Code
  4. python3 claude_menubar.py --add-profile work "Work (Enterprise)"
  5. Switch profiles from the menu bar dropdown

  The "Auto (Keychain)" profile always uses whatever account is
  currently logged into Claude Code — no setup needed.

Config: ~/.config/claude-monitor/config.json
"""


def cmd_test():
    print("Reading token from macOS Keychain…")

    creds = _read_raw_creds()
    if not creds:
        print("❌ No Claude Code credentials found.")
        print("   Make sure you're logged into Claude Code: run 'claude' and log in.")
        sys.exit(1)

    oauth = creds.get("claudeAiOauth", {})
    plan  = oauth.get("subscriptionType", "unknown")
    expires = oauth.get("expiresAt", 0)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if expires and now_ms > expires:
        print(f"⚠️  Access token is expired (by {(now_ms - expires)//60000} min)")
        if oauth.get("refreshToken"):
            print("   Attempting auto-refresh…")
            new_token, new_exp = _refresh_access_token(oauth["refreshToken"])
            if new_token:
                oauth["accessToken"] = new_token
                if new_exp:
                    oauth["expiresAt"] = new_exp
                creds["claudeAiOauth"] = oauth
                _write_keychain_creds(creds)
                print("   ✅ Token refreshed and saved to Keychain!")
            else:
                print("   ❌ Refresh failed — you'll need to re-login to Claude Code.")
                sys.exit(1)
        else:
            print("   ❌ No refresh token available — re-login to Claude Code.")
            sys.exit(1)

    token = oauth["accessToken"]
    print(f"✅ Token ready  •  Plan: {plan}")
    print(f"   Calling {USAGE_URL} …\n")

    data = fetch_usage(token)

    if "_error" in data:
        print(f"❌ {data['_error']}")
        sys.exit(1)

    print(json.dumps(data, indent=2))

    fh = data.get("five_hour", {})
    sd = data.get("seven_day", {})
    opus = data.get("seven_day_opus", {})

    print(f"\n{'─' * 44}")
    if fh:
        pct = fh.get("utilization", 0)
        r = time_until(fh.get("resets_at"))
        print(f"  5-hour:  {dot(pct)} {bar(pct)}  {pct:.1f}%  (resets in {r or '—'})")
    if sd:
        pct = sd.get("utilization", 0)
        r = time_until(sd.get("resets_at"))
        print(f"  7-day:   {dot(pct)} {bar(pct)}  {pct:.1f}%  (resets in {r or '—'})")
    if opus and opus.get("utilization", 0) > 0:
        pct = opus["utilization"]
        print(f"  Opus:    {dot(pct)} {bar(pct)}  {pct:.1f}%")
    print()


def cmd_install():
    script = os.path.abspath(__file__)
    python = sys.executable
    plist_path = Path.home() / "Library/LaunchAgents/com.claude.menubar-monitor.plist"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.menubar-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/claude-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/claude-monitor.err</string>
</dict>
</plist>"""
    print(f"\n📄 Save this to:\n   {plist_path}\n")
    print(plist)
    print(f"\nThen run:\n   launchctl load {plist_path}\n")


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(USAGE_TEXT)
        sys.exit(0)

    if "--test" in args:
        cmd_test()
        sys.exit(0)

    if "--install" in args:
        cmd_install()
        sys.exit(0)

    if "--add-profile" in args:
        idx = args.index("--add-profile")
        if idx + 2 >= len(args):
            print("Usage: --add-profile NAME \"LABEL\"")
            sys.exit(1)
        add_profile_from_keychain(args[idx + 1], args[idx + 2])
        sys.exit(0)

    if "--refresh-profile" in args:
        idx = args.index("--refresh-profile")
        if idx + 1 >= len(args):
            print("Usage: --refresh-profile NAME")
            sys.exit(1)
        refresh_profile_token(args[idx + 1])
        sys.exit(0)

    # ── Launch the menu bar app ──────────────────────────────
    cfg = load_config()
    if not CONFIG_FILE.exists():
        save_config(cfg)

    active = cfg.get("active_profile", "auto")
    label  = cfg["profiles"].get(active, {}).get("label", active)

    print("Claude Code Menu Bar Monitor")
    print(f"  Profile:  {label}")
    print(f"  Config:   {CONFIG_FILE}")
    print(f"  Refresh:  every {cfg.get('refresh_seconds', DEFAULT_REFRESH)}s")
    print(f"  Test:     python3 {sys.argv[0]} --test")
    print(f"\nLook for the icon in your menu bar.\n")

    # ── macOS GUI bootstrap ─────────────────────────────────────
    # A venv 'python' is a thin wrapper that macOS doesn't treat as
    # a real GUI process. We need the *framework* Python binary to
    # get a menu bar icon. Re-exec ourselves with it if needed.
    if sys.platform == "darwin" and os.environ.get("_CLAUDE_MONITOR_LAUNCHED") != "1":
        fw_python = _find_framework_python()
        if fw_python and os.path.realpath(sys.executable) != os.path.realpath(fw_python):
            print(f"  Re-launching with framework Python:\n  {fw_python}\n")
            env = os.environ.copy()
            env["_CLAUDE_MONITOR_LAUNCHED"] = "1"
            # Preserve venv site-packages
            import site
            venv_site = [p for p in sys.path if "site-packages" in p]
            if venv_site:
                env["PYTHONPATH"] = ":".join(venv_site) + ":" + env.get("PYTHONPATH", "")
            os.execve(fw_python, [fw_python, os.path.abspath(__file__)] + sys.argv[1:], env)

    # Tell macOS: menu bar accessory, no dock icon
    try:
        from AppKit import NSApplication, NSBundle
        # Set LSUIElement so macOS knows this is a menu-bar-only app
        info = NSBundle.mainBundle().infoDictionary()
        info["LSUIElement"] = "1"
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)  # Accessory
    except ImportError:
        pass

    ClaudeMenuBar(cfg).run()


def _find_framework_python():
    """
    Locate the macOS framework Python binary that can render GUI.
    Works with Homebrew, pyenv --enable-framework, and system Python.
    """
    import sysconfig

    # Check common framework locations
    candidates = []

    # 1. Derive from sys.base_prefix (works for Homebrew + pyenv framework)
    fw = os.path.join(
        sys.base_prefix, "Resources", "Python.app",
        "Contents", "MacOS", "Python"
    )
    candidates.append(fw)

    # 2. sysconfig-based (covers some edge cases)
    prefix = sysconfig.get_config_var("prefix") or ""
    if "Python.framework" in prefix:
        fw2 = os.path.join(prefix, "Resources", "Python.app",
                           "Contents", "MacOS", "Python")
        candidates.append(fw2)

    # 3. Homebrew ARM
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates.append(
        f"/opt/homebrew/Cellar/python@{ver}/{sys.version_info.major}"
        f".{sys.version_info.minor}.{sys.version_info.micro}"
        f"/Frameworks/Python.framework/Versions/{ver}"
        f"/Resources/Python.app/Contents/MacOS/Python"
    )
    # 4. Homebrew Intel
    candidates.append(
        f"/usr/local/Cellar/python@{ver}/{sys.version_info.major}"
        f".{sys.version_info.minor}.{sys.version_info.micro}"
        f"/Frameworks/Python.framework/Versions/{ver}"
        f"/Resources/Python.app/Contents/MacOS/Python"
    )

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


if __name__ == "__main__":
    main()