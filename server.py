"""
AI News Radar – self-contained web server.

Flask serves the static front-end and data JSON files while APScheduler
runs the update_news.py script periodically in a subprocess.
"""

import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, send_from_directory
from flask_compress import Compress

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
SEED_DIR = BASE_DIR / "data-seed"
PORT = int(os.environ.get("PORT", "8080"))
UPDATE_INTERVAL = int(os.environ.get("UPDATE_INTERVAL_MINUTES", "30"))
TRIGGER_SECRET = os.environ.get("TRIGGER_SECRET", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ai-news-radar")

# ---------------------------------------------------------------------------
# Seed data: on first run copy bundled archive/cache into the persistent volume
# ---------------------------------------------------------------------------

DATA_DIR.mkdir(parents=True, exist_ok=True)

for seed_file in ("archive.json", "title-zh-cache.json"):
    dest = DATA_DIR / seed_file
    src = SEED_DIR / seed_file
    if not dest.exists() and src.exists():
        log.info("Seeding %s → %s", src, dest)
        shutil.copy2(src, dest)

# ---------------------------------------------------------------------------
# Update runner
# ---------------------------------------------------------------------------

_update_lock = threading.Lock()
_last_run: dict = {"started_at": None, "finished_at": None, "ok": None, "error": None}


def run_update() -> None:
    """Execute update_news.py in a subprocess."""
    if not _update_lock.acquire(blocking=False):
        log.warning("Update already in progress – skipping")
        return
    try:
        _last_run["started_at"] = datetime.now(timezone.utc).isoformat()
        _last_run["ok"] = None
        _last_run["error"] = None

        cmd = [
            "python", "-m", "scripts.update_news",
            "--output-dir", str(DATA_DIR),
            "--window-hours", "24",
        ]

        opml_path = BASE_DIR / "feeds" / "follow.opml"
        if opml_path.exists():
            cmd += ["--rss-opml", str(opml_path)]

        log.info("Starting update: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,  # 10-minute hard limit
        )

        if result.returncode == 0:
            _last_run["ok"] = True
            log.info("Update finished successfully")
        else:
            _last_run["ok"] = False
            _last_run["error"] = (result.stderr or result.stdout)[-2000:]
            log.error("Update failed (rc=%d): %s", result.returncode, _last_run["error"])

    except subprocess.TimeoutExpired:
        _last_run["ok"] = False
        _last_run["error"] = "Timed out after 600 s"
        log.error("Update timed out")
    except Exception as exc:
        _last_run["ok"] = False
        _last_run["error"] = str(exc)
        log.exception("Update error")
    finally:
        _last_run["finished_at"] = datetime.now(timezone.utc).isoformat()
        _update_lock.release()


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(run_update, "interval", minutes=UPDATE_INTERVAL, id="news_update",
                  next_run_time=datetime.now(timezone.utc))  # run immediately on start
scheduler.start()

# ---------------------------------------------------------------------------
# Flask application
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)
Compress(app)


@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "index.html")


@app.route("/assets/<path:filename>")
def assets(filename):
    resp = send_from_directory(str(BASE_DIR / "assets"), filename)
    resp.cache_control.max_age = 3600  # 1 hour
    resp.cache_control.public = True
    return resp


@app.route("/data/<path:filename>")
def data(filename):
    if not (DATA_DIR / filename).exists():
        # Data not yet generated (update still running after restart).
        # Return a minimal valid JSON so the frontend doesn't crash.
        return jsonify({"items_ai": [], "items_all": [], "items_all_raw": [],
                        "site_stats": [], "total_items": 0,
                        "waiting": True}), 200
    resp = send_from_directory(str(DATA_DIR), filename)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "update_interval_minutes": UPDATE_INTERVAL,
        "last_run": _last_run,
        "data_dir": str(DATA_DIR),
        "scheduler_running": scheduler.running,
    })


@app.route("/api/trigger", methods=["POST"])
def trigger():
    if TRIGGER_SECRET:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token != TRIGGER_SECRET:
            return jsonify({"error": "unauthorized"}), 401

    if _update_lock.locked():
        return jsonify({"status": "already_running"}), 409

    threading.Thread(target=run_update, daemon=True).start()
    return jsonify({"status": "triggered"})
