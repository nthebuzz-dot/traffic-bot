"""
Flask web dashboard for Web Traffic Bot.

Provides a browser UI to configure and run the bot without touching the CLI.
Logs are streamed live to the browser via Server-Sent Events (SSE).

Key design decisions:
- Config is persisted to disk (config_state.json) so it survives page refreshes
  and server restarts.
- Each SSE client gets its own queue (broadcast pattern) so multiple browser
  tabs / devices can all watch the live log simultaneously without stealing
  messages from each other.
- Flask runs with threaded=True so the SSE stream never blocks other requests.
"""

import json
import logging
import os
import queue
import threading
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request

from bot.config_handler import ConfigHandler
from bot.traffic_bot import TrafficBot

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")

# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config_state.json")

_DEFAULT_CONFIG: dict = {
    "target_url": "",
    "sessions_count": 10,
    "concurrent_sessions": 1,
    "session_duration": 45,
    "duration_seconds": 600,
    "proxies": [],
    "headless": True,
    "chromium_path": "",
}


def _load_persisted_config() -> dict:
    """Load config from disk, falling back to defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r") as fh:
                saved = json.load(fh)
            cfg.update(saved)
    except Exception:
        pass
    return cfg


def _save_persisted_config(cfg: dict) -> None:
    """Write config to disk so it survives page refreshes and restarts."""
    try:
        with open(_CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


# Current config — loaded from disk at startup
_current_config: dict = _load_persisted_config()

# ---------------------------------------------------------------------------
# Global bot state
# ---------------------------------------------------------------------------

_bot_thread: Optional[threading.Thread] = None
_bot_instance = None   # live TrafficBot reference for real-time stats
_bot_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Broadcast log system
#
# Instead of a single shared queue (which splits messages between clients),
# we keep a list of per-client queues. Every log line is pushed to ALL of
# them so every connected browser tab sees the full log.
# ---------------------------------------------------------------------------

_sse_clients: list = []          # list of queue.Queue, one per connected client
_sse_clients_lock = threading.Lock()
_log_history: list = []          # last N lines so new clients see recent history
_LOG_HISTORY_MAX = 200


def _broadcast_log(line: str) -> None:
    """Push a log line to every connected SSE client."""
    with _sse_clients_lock:
        _log_history.append(line)
        if len(_log_history) > _LOG_HISTORY_MAX:
            _log_history.pop(0)
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(line)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


class _BroadcastHandler(logging.Handler):
    """Push log records to all connected SSE clients."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _broadcast_log(self.format(record))
        except Exception:
            pass


def _attach_broadcast_handler() -> None:
    """Attach the broadcast handler to the root logger (once)."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, _BroadcastHandler):
            return
    handler = _BroadcastHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.setLevel(logging.DEBUG)
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)


_attach_broadcast_handler()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    proxies_str = "\n".join(_current_config.get("proxies") or [])
    return render_template("index.html", config=_current_config, proxies_str=proxies_str)


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(_current_config)


@app.route("/api/config", methods=["POST"])
def save_config():
    data = request.get_json(force=True)

    _current_config["target_url"] = str(data.get("target_url", "")).strip()
    _current_config["sessions_count"] = int(data.get("sessions_count", 10))
    _current_config["concurrent_sessions"] = max(1, int(data.get("concurrent_sessions", 1)))
    _current_config["session_duration"] = int(data.get("session_duration", 45))
    _current_config["duration_seconds"] = int(data.get("duration_seconds", 600))
    _current_config["headless"] = bool(data.get("headless", True))
    _current_config["chromium_path"] = str(data.get("chromium_path", "")).strip()

    raw_proxies = data.get("proxies", [])
    if isinstance(raw_proxies, str):
        raw_proxies = [p.strip() for p in raw_proxies.replace(",", "\n").splitlines()]
    _current_config["proxies"] = [p for p in raw_proxies if p]

    # Persist to disk so config survives page refreshes and server restarts
    _save_persisted_config(_current_config)

    return jsonify({"status": "ok", "config": _current_config})


@app.route("/api/status", methods=["GET"])
def bot_status():
    running = _bot_thread is not None and _bot_thread.is_alive()
    return jsonify({"running": running})


@app.route("/api/stats", methods=["GET"])
def bot_stats():
    """Return live session counters from the running bot."""
    if _bot_instance is not None:
        return jsonify({
            "completed": _bot_instance.sessions_completed,
            "failed":    _bot_instance.sessions_failed,
            "total":     _bot_instance.config.sessions_count,
        })
    return jsonify({"completed": 0, "failed": 0, "total": 0})


@app.route("/api/start", methods=["POST"])
def start_bot():
    global _bot_thread

    with _bot_lock:
        if _bot_thread is not None and _bot_thread.is_alive():
            return jsonify({"status": "error", "message": "Bot is already running"}), 409

        if not _current_config.get("target_url"):
            return jsonify({"status": "error", "message": "target_url is required"}), 400

        config = ConfigHandler()
        config.config.update(_current_config)

        def _run():
            global _bot_instance
            try:
                bot = TrafficBot(config)
                _bot_instance = bot
                bot.run()
            except Exception as exc:
                logging.getLogger(__name__).error(f"Bot crashed: {exc}")
            finally:
                _bot_instance = None

        _bot_thread = threading.Thread(target=_run, daemon=True, name="bot-worker")
        _bot_thread.start()

    return jsonify({"status": "ok", "message": "Bot started"})


@app.route("/api/stop", methods=["POST"])
def stop_bot():
    running = _bot_thread is not None and _bot_thread.is_alive()
    if not running:
        return jsonify({"status": "ok", "message": "Bot is not running"})

    from bot.traffic_bot import request_stop
    request_stop()
    return jsonify({
        "status": "ok",
        "message": "Stop signal sent — bot will exit after the current session",
    })


@app.route("/api/logs")
def stream_logs():
    """
    Server-Sent Events endpoint.

    Each client gets its own queue pre-filled with recent log history,
    then receives new lines as they arrive. Multiple browser tabs / devices
    can all connect simultaneously without stealing each other's messages.
    """
    # Create a per-client queue and pre-fill with recent history
    client_q: queue.Queue = queue.Queue(maxsize=1000)
    with _sse_clients_lock:
        for line in _log_history:
            try:
                client_q.put_nowait(line)
            except queue.Full:
                break
        _sse_clients.append(client_q)

    def _generate():
        try:
            yield "data: --- Log stream connected ---\n\n"
            while True:
                try:
                    line = client_q.get(timeout=20)
                    safe = line.replace("\n", " ").replace("\r", "")
                    yield f"data: {safe}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            # Remove this client's queue when the connection closes
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(client_q)
                except ValueError:
                    pass

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Entry point (used by `web-traffic-bot --dashboard`)
# ---------------------------------------------------------------------------

def run_dashboard(host: str = "0.0.0.0", port: int = 5000, debug: bool = False) -> None:
    print(f"\n  Web Traffic Bot Dashboard → http://localhost:{port}\n")
    app.run(host=host, port=port, debug=debug, threaded=True)
