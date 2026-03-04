# Web Traffic Bot

A Python + Selenium bot for personal website **load testing** and **traffic simulation**.  
Opens real browser sessions, simulates realistic user engagement, and is designed to be undetectable at high volume (20,000+ sessions/day).

---

## Features

### Core
- **Web dashboard** — configure and control everything from your browser at `http://localhost:5000`
- **Concurrent sessions** — run any number of browser windows simultaneously (thread-safe)
- **Multi-URL support** — distribute sessions across multiple target URLs in round-robin order
- **Per-URL stats** — live breakdown of completed/failed sessions per URL
- **Config persistence** — settings saved to disk, survive page refreshes and server restarts
- **Live log stream** — real-time log visible from multiple browser tabs/devices simultaneously

### Anti-Detection (stealth)
- **JavaScript fingerprint patches** — injected before any page script runs:
  - `navigator.webdriver` → `undefined` (primary Selenium detection flag)
  - `navigator.plugins` → realistic 3-plugin list (empty = headless bot)
  - `window.chrome` → full Chrome runtime object
  - `navigator.permissions` → `default` for notifications (headless returns `denied`)
  - Canvas fingerprint noise — unique per session
  - WebGL vendor/renderer → random real GPU string
  - `navigator.hardwareConcurrency` → random 2/4/6/8/12/16
  - `navigator.deviceMemory` → random 2/4/8 GB
- **Referrer spoofing** — sessions arrive via realistic HTTP Referer headers
  - Custom referrers: add your own sites → ~80% of traffic appears to come from them
  - Built-in pool: Google, Bing, DuckDuckGo, Facebook, Reddit, LinkedIn (~20%)
  - ~20% of sessions arrive direct (no referrer) — matches real-world distribution
- **Cookie persistence** — saves cookies per domain between sessions:
  - Injects GDPR/consent cookies (OneTrust, Cookiebot, generic) — no consent banner
  - Reloads saved cookies on next visit → looks like a returning visitor
- **Device fingerprint randomisation** per session:
  - User-agent: Chrome 144/145, Edge 144/145, Firefox 135/136, Safari 18.x
  - Screen resolution: 8 common sizes
  - Device pixel ratio: 1.0 / 1.25 / 1.5 / 2.0 / 2.25 / 2.5
  - Accept-Language: 7 locales
  - Timezone: matched to proxy exit IP via GeoIP, or random fallback
- **Inter-session jitter** — random 0.5–3s delay between session launches (no uniform timestamps)
- **Proxy-aware timezone** — detects proxy exit IP timezone via GeoIP (through the proxy)

### Engagement simulation
- Scroll through page in 3–7 random steps
- Occasional scroll-back (like a real reader)
- Mouse movement to trigger hover events
- 60% chance of clicking an internal link (2+ page views = engaged session in GA4)
- Idle micro-scrolls throughout session duration
- Sessions stay 45 s by default → triggers GA4 `user_engagement` event

### Infrastructure
- **Auto-install Chromium** — if no browser is found on Ubuntu/Debian, installs it automatically
- **Auto-fix snap Chromium** — detects and replaces incompatible snap Chromium with apt version
- **Thread-safe proxy rotation** — round-robin with a lock (safe for 20+ concurrent sessions)
- **Pre-resolved chromedriver** — downloaded once before the thread pool starts (no race conditions)
- Selenium Manager fallback for Chrome 145+ (no webdriver-manager database entry needed)
- YAML config file **or** pure CLI flags
- Installable as a system command (`web-traffic-bot`)

---

## Requirements

- Python 3.8+
- Google Chrome or Chromium (auto-installed on Ubuntu/Debian if missing)
- `chromedriver` (auto-managed)

### Ubuntu / Debian quick setup

```bash
bash scripts/ubuntu_setup.sh
```

This removes snap Chromium (incompatible with Selenium) and installs the apt version.

---

## Installation

```bash
# Clone the repo
git clone https://github.com/nthebuzz-dot/traffic-bot.git
cd traffic-bot

# (Recommended) create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the package and all dependencies
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

### Dashboard fields

| Field | Description |
|---|---|
| **Target URLs** | One URL per line — sessions distributed in round-robin order |
| **Total Sessions** | How many browser sessions to open in total |
| **Concurrent Sessions** | How many browsers run at the same time |
| **Session Duration** | How long each session stays on the page (seconds) |
| **Total Duration** | Hard stop after this many seconds |
| **Proxies** | One proxy per line — timezone auto-detected per proxy |
| **Referrer URLs** | Your own sites — ~80% of traffic will appear to come from these |
| **Chromium Path** | Custom browser binary path (leave blank to auto-detect) |
| **Cookie Storage Directory** | Where per-domain cookies are saved (leave blank for default) |
| **Headless toggle** | Run silently or with a visible browser window |
| **Start / Stop** | Launch or gracefully stop the bot |
| **Live Log** | Real-time log stream — works from multiple devices simultaneously |
| **Stats cards** | Live counters: ✅ Completed, ❌ Failed, Progress, ⏱ Elapsed |
| **Per-URL table** | Breakdown of completed/failed/success% per URL (shown when 2+ URLs) |

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

### High-volume example (20k sessions/day)

```bash
web-traffic-bot \
  --url https://yoursite.com \
  --sessions 1000 \
  --concurrent 20 \
  --session-duration 45 \
  --duration 86400 \
  --proxy http://user:pass@proxy1:8000 \
  --proxy http://user:pass@proxy2:8000 \
  --referrer https://yourblog.com \
  --referrer https://yourforum.com/thread/123
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
                       [--sessions N] [--concurrent N]
                       [--duration SECS] [--session-duration SECS]
                       [--headless | --no-headless]
                       [--proxy PROXY_URL] [--referrer REFERRER_URL]

Dashboard:
  --dashboard, -d           Launch the web dashboard
  --host HOST               Dashboard host (default: 0.0.0.0)
  --port PORT               Dashboard port (default: 5000)

Bot (CLI mode):
  --url, --target-url       Target URL to test
  --config, -c              Path to YAML config file
  --sessions N              Number of sessions (default: 10)
  --concurrent N            Concurrent sessions (default: 1)
  --duration SECS           Total run duration in seconds (default: 600)
  --session-duration SECS   Duration per session in seconds (default: 45)
  --headless                Run in headless mode (default)
  --no-headless             Run with a visible browser window
  --proxy PROXY_URL         Proxy URL; repeat for multiple proxies
  --referrer REFERRER_URL   Custom referrer URL; repeat for multiple
```

---

## Configuration file reference

```yaml
# config/config.yaml

# Target URLs — sessions distributed in round-robin order
target_urls:
  - "https://yoursite.com"
  - "https://yoursite.com/blog"
  - "https://yoursite.com/contact"

# (Legacy) single URL — used if target_urls is empty
target_url: ""

sessions_count: 1000        # total number of browser sessions
concurrent_sessions: 20     # how many browsers run at the same time
session_duration: 45        # seconds each session stays on the page
duration_seconds: 86400     # hard stop after this many seconds total

# Proxy list — one per line
proxies:
  - "http://user:password@proxy1.example:8000"
  - "http://user:password@proxy2.example:8000"

# Custom referrer URLs — ~80% of sessions will use these as HTTP Referer
# Leave empty to use only built-in search/social referrers
referrers:
  - "https://yourblog.com"
  - "https://yourforum.com/thread/123"

headless: true              # always true on servers (no display)
chromium_path: ""           # leave blank to auto-detect
cookie_dir: ""              # leave blank for default (~/.web-traffic-bot/cookies/)
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
traffic-bot/
├── bot/
│   ├── __init__.py
│   ├── config_handler.py       # YAML config loader + attribute accessors
│   ├── cookie_manager.py       # Per-domain cookie persistence + consent injection
│   ├── logger.py               # Logging setup
│   ├── proxy_manager.py        # Thread-safe round-robin proxy rotation
│   ├── selenium_driver.py      # Chrome/Chromium WebDriver wrapper + stealth patches
│   ├── session_simulator.py    # Realistic engagement simulation
│   ├── traffic_bot.py          # Main orchestrator (concurrent sessions, stats)
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
│   └── ubuntu_setup.sh         # Auto-installs Chromium on Ubuntu/Debian
├── requirements.txt
├── setup.py
└── README.md
```

---

## Deploying on a server

### 1 — Install system dependencies

```bash
# Ubuntu / Debian (removes snap Chromium, installs apt version)
bash scripts/ubuntu_setup.sh
```

### 2 — Install the bot

```bash
git clone https://github.com/nthebuzz-dot/traffic-bot.git
cd traffic-bot
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 3 — Run the dashboard

```bash
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
WorkingDirectory=/path/to/traffic-bot
ExecStart=/path/to/traffic-bot/venv/bin/web-traffic-bot --dashboard --host 0.0.0.0 --port 5000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable web-traffic-bot
sudo systemctl start  web-traffic-bot
sudo systemctl status web-traffic-bot
```

### 5 — Optional: Nginx reverse proxy

```nginx
location /traffic-bot/ {
    proxy_pass         http://127.0.0.1:5000/;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_buffering    off;   # required for the live log SSE stream
    proxy_cache        off;
    proxy_read_timeout 3600;
}
```

---

## How 20,000 sessions/day works

With `concurrent_sessions: 20` and `session_duration: 45s`:
- Each session takes ~45–60 s (including page load + engagement)
- 20 concurrent × 60 sessions/hour = **1,200 sessions/hour**
- 1,200 × 24 hours = **28,800 sessions/day**

Adjust `concurrent_sessions` and `sessions_count` to hit your target.

> **RAM:** each concurrent session uses ~200–400 MB. 20 concurrent = ~4–8 GB RAM.  
> **Proxies:** use at least 1 proxy per 5 concurrent sessions to avoid IP rate limits.

---

## Disclaimer

This tool is intended **only for testing your own websites**.  
Do not use it against websites you do not own or have explicit permission to test.
