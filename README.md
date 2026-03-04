# Web Traffic Bot

A Python + Selenium bot for personal website **load testing** and **traffic simulation**.  
It opens real browser sessions, scrolls through pages, and simulates user engagement — useful for testing Google Analytics, CDN behaviour, and server load.

---

## Features

- **Web dashboard** — configure and control everything from your browser at `http://localhost:5000`
- **Concurrent sessions** — run multiple browser windows simultaneously (any number, thread-safe)
- **Device fingerprint randomisation** — each session gets a unique user-agent, screen resolution, language, and timezone
- **Proxy-aware timezone** — detects the proxy's exit IP timezone via GeoIP lookup (through the proxy)
- **Multi-page navigation** — 60% of sessions click an internal link for 2+ page views in GA4
- **GA4 engaged sessions** — sessions stay 45 s by default, triggering the `user_engagement` event
- Headless Chrome/Chromium sessions via Selenium
- Configurable total sessions, concurrent sessions, session duration, and total run time
- Optional proxy rotation (round-robin)
- Realistic engagement simulation (scrolling, mouse movement, idle time)
- YAML config file **or** pure CLI flags
- Installable as a system command (`web-traffic-bot`)

---

## Requirements

- Python 3.8+
- Google Chrome or Chromium
- `chromedriver` (auto-managed by `webdriver-manager`)

### Ubuntu / Debian quick setup

```bash
bash scripts/ubuntu_setup.sh
```

---

## Installation

```bash
# Clone the repo
git clone https://github.com/Lugayavu/web-traffic-bot.git
cd web-traffic-bot

# (Recommended) create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the package and all dependencies (including Flask for the dashboard)
pip install -e .
```

After installation the `web-traffic-bot` command is available in your shell.

---

## Web Dashboard

The easiest way to use the bot is through the built-in web dashboard.

```bash
web-traffic-bot --dashboard
```

Then open your browser at **http://localhost:5000**

### Dashboard features

| Section | What you can do |
|---|---|
| **Target URL** | Set the website you want to test |
| **Total Sessions** | How many browser sessions to open in total |
| **Concurrent Sessions** | How many browsers run at the same time (any number) |
| **Session Duration** | How long each session stays on the page (seconds) |
| **Total Duration** | Hard stop after this many seconds |
| **Proxies** | Paste one proxy per line — timezone auto-detected per proxy |
| **Chromium Path** | Custom browser binary path (leave blank to auto-detect) |
| **Headless toggle** | Run silently or with a visible browser window |
| **Start / Stop** | Launch or gracefully stop the bot |
| **Live Log** | Real-time log stream — works from multiple devices simultaneously |
| **Stats cards** | Live counters: ✅ Completed, ❌ Failed, Progress, ⏱ Elapsed |

### Config persistence

Your configuration (URL, sessions, proxies, etc.) is automatically saved to disk when you click **💾 Save**. It survives page refreshes and server restarts — you never have to re-enter your settings.

### Multi-device access

You can open the dashboard from multiple browser tabs or devices at the same time. Each connection gets its own live log stream — no messages are lost or split between clients.

### Custom host / port

```bash
web-traffic-bot --dashboard --host 127.0.0.1 --port 8080
# → http://127.0.0.1:8080
```

---

## CLI Usage (no dashboard)

### Quick start

```bash
web-traffic-bot --url https://yoursite.com --sessions 20 --duration 600
```

### Via YAML config file

```bash
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml
web-traffic-bot --config config/config.yaml
```

### Mix both (CLI flags override config file values)

```bash
web-traffic-bot --config config/config.yaml --sessions 50 --no-headless
```

### All CLI options

```
usage: web-traffic-bot [-h]
                       [--dashboard] [--host HOST] [--port PORT]
                       [--url URL] [--config CONFIG]
                       [--sessions N] [--duration SECS]
                       [--session-duration SECS]
                       [--headless | --no-headless]
                       [--proxy PROXY_URL]

Dashboard:
  --dashboard, -d       Launch the web dashboard
  --host HOST           Dashboard host (default: 0.0.0.0)
  --port PORT           Dashboard port (default: 5000)

Bot (CLI mode):
  --url, --target-url   Target URL to test
  --config, -c          Path to YAML config file
  --sessions N          Number of sessions (default: 10)
  --duration SECS       Total run duration in seconds (default: 600)
  --session-duration S  Duration per session in seconds (default: 45)
  --headless            Run in headless mode (default)
  --no-headless         Run with a visible browser window
  --proxy PROXY_URL     Proxy URL; repeat for multiple proxies
```

---

## Configuration file reference

```yaml
# config/config.yaml
target_url: "https://yoursite.com"
sessions_count: 10          # total number of browser sessions
concurrent_sessions: 3      # how many browsers run at the same time
session_duration: 45        # seconds each session stays on the page
duration_seconds: 600       # hard stop after this many seconds total
proxies:
  - "http://user:password@proxy1.example:8000"
  - "http://user:password@proxy2.example:8000"
headless: true
chromium_path: ""           # leave blank to auto-detect via webdriver-manager
```

---

## Running directly (without installing)

```bash
# Dashboard
python -m bot.cli --dashboard

# CLI mode
python -m bot.cli --url https://yoursite.com --sessions 5
```

---

## Project structure

```
web-traffic-bot/
├── bot/
│   ├── __init__.py
│   ├── config_handler.py       # YAML config loader + attribute accessors
│   ├── logger.py               # Logging setup
│   ├── proxy_manager.py        # Round-robin proxy rotation
│   ├── selenium_driver.py      # Chrome/Chromium WebDriver wrapper
│   ├── session_simulator.py    # Realistic engagement simulation
│   ├── traffic_bot.py          # Main orchestrator
│   ├── cli/
│   │   ├── __init__.py
│   │   └── __main__.py         # CLI entry point (--dashboard or bot flags)
│   └── dashboard/
│       ├── __init__.py
│       ├── app.py              # Flask dashboard app + SSE log stream
│       └── templates/
│           └── index.html      # Dashboard UI
├── config/
│   └── config.example.yaml
├── scripts/
│   └── ubuntu_setup.sh
├── requirements.txt
├── setup.py
└── README.md
```

---

## Deploying on a server

### 1 — Install system dependencies

```bash
# Ubuntu / Debian
bash scripts/ubuntu_setup.sh
```

This installs Python 3, pip, venv, and Chromium.

### 2 — Install the bot

```bash
git clone https://github.com/Lugayavu/web-traffic-bot.git
cd web-traffic-bot
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 3 — Run the dashboard (foreground test)

```bash
# Bind to all interfaces so you can reach it from your browser
web-traffic-bot --dashboard --host 0.0.0.0 --port 5000
```

Access it at `http://<your-server-ip>:5000`

> **Firewall:** make sure port 5000 is open in your server's firewall / security group.

### 4 — Keep it running with systemd (recommended)

Create `/etc/systemd/system/web-traffic-bot.service`:

```ini
[Unit]
Description=Web Traffic Bot Dashboard
After=network.target

[Service]
Type=simple
User=YOUR_LINUX_USER
WorkingDirectory=/path/to/web-traffic-bot
ExecStart=/path/to/web-traffic-bot/venv/bin/web-traffic-bot --dashboard --host 0.0.0.0 --port 5000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable web-traffic-bot
sudo systemctl start  web-traffic-bot
sudo systemctl status web-traffic-bot   # check it's running
```

### 5 — Headless mode on a server

Servers have no display. Always keep **Headless mode ON** (the toggle in the dashboard, or `headless: true` in the config file). The bot uses `--headless=new` which works without a display server.

### 6 — Optional: put Nginx in front (clean URL, no port)

If you want `http://yourserver.com/web-traffic-bot/` instead of `:5000`:

```nginx
# /etc/nginx/sites-available/default  (inside the server {} block)
location /web-traffic-bot/ {
    proxy_pass         http://127.0.0.1:5000/;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_buffering    off;   # required for the live log SSE stream
    proxy_cache        off;
    proxy_read_timeout 3600;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Troubleshooting

### WebDriver initialisation failure

**Symptom:** Bot starts then immediately fails with a WebDriver error in the live log.

---

#### ⚠️ Case 1 — Snap Chromium (Ubuntu 22.04 / 24.04) — MUST FIX

If `chromium --version` shows `... snap`, you have the **snap version**.
**Snap Chromium cannot be used with Selenium** — the snap sandbox prevents chromedriver from launching the browser as a subprocess. This will always fail.

**Fix — replace snap Chromium with the apt version:**

```bash
sudo snap remove chromium
sudo apt update

# Ubuntu 22.04 / 24.04
sudo apt install -y chromium chromium-driver

# Ubuntu 20.04
sudo apt install -y chromium-browser chromium-chromedriver
```

Verify both versions match (same major number):

```bash
chromium --version        # or: chromium-browser --version
chromedriver --version
```

Then restart the dashboard — the bot will work.

> **Shortcut:** just run `bash scripts/ubuntu_setup.sh` — it removes snap Chromium and installs the apt version automatically.

---

#### Case 2 — apt Chromium missing chromedriver

```bash
# Ubuntu 22.04+
sudo apt install -y chromium chromium-driver

# Ubuntu 20.04
sudo apt install -y chromium-browser chromium-chromedriver
```

---

#### Case 3 — Google Chrome

The bot will fall back to `webdriver-manager` automatically if no system `chromedriver` is found. `webdriver-manager` is included in the dependencies.

---

#### Case 4 — Non-standard install path

Set the **Chromium Path** field in the dashboard (or `chromium_path` in the config file) to the full path of the browser binary, e.g. `/usr/bin/chromium`.

---

### webdriver-manager can't find chromedriver for Chrome 145+

**Symptom:** `webdriver-manager is having trouble finding chromedriver for Chrome 145`

**Cause:** `webdriver-manager` doesn't have very new Chrome versions in its database yet.

**Fix (automatic):** The bot now falls back to **Selenium Manager** (built into Selenium 4.6+) which handles any Chrome version. Pull the latest code and it works automatically:

```bash
git pull origin session/agent_a97e6794-982e-42a7-b155-ab0f7c5d894c
```

**Best fix (no download needed):** Install the apt version of Chromium which ships with its own matching chromedriver:

```bash
sudo snap remove chromium
sudo apt install -y chromium chromium-driver
chromium --version && chromedriver --version  # should match
```

---

### Dashboard freezes or config disappears on refresh

**Config disappears:** Click **💾 Save** before starting the bot. The config is saved to `bot/dashboard/config_state.json` and reloaded automatically on every page load.

**Dashboard freezes while bot is running:** This was a bug in older versions (single shared log queue). Pull the latest code — the dashboard now uses a per-client broadcast queue and never freezes.

**Live log stops after refreshing:** The SSE stream reconnects automatically on page load. You will see `--- Log stream connected ---` in the log box and then the last 200 lines of history.

**Accessing from another device:** Open `http://<server-ip>:5000` from any browser. Multiple devices can connect simultaneously — each gets its own full log stream.

---

### Bot crashes silently on a server

Make sure **Headless mode is ON**. Servers have no display — running without headless will crash immediately.

---

### `--no-sandbox` warning

The bot already passes `--no-sandbox` which is required when running as root or in Docker. This is safe for a controlled testing environment.

---

## Disclaimer

This tool is intended **only for testing your own websites**.  
Do not use it against websites you do not own or have explicit permission to test.
