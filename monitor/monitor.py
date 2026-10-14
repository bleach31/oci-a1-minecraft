#!/usr/bin/env python3
"""Read-only Bedrock dashboard for Linux. Python 3.10+, no pip packages."""

import argparse
import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


PLAYER = re.compile(r"Player (connected|disconnected): (.+?), xuid: (\d+)(?:,|\s*$)")
METRIC_DAYS = 7
HISTORY_DAYS = 90
LOGGER = logging.getLogger("minecraft-monitor")


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metrics (ts REAL PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                cursor TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
                name TEXT, identity TEXT, invocation TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_time ON events(ts);
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY, identity TEXT NOT NULL, name TEXT NOT NULL,
                invocation TEXT NOT NULL, joined REAL NOT NULL, ended REAL, reason TEXT);
            CREATE INDEX IF NOT EXISTS sessions_time ON sessions(joined);
            CREATE UNIQUE INDEX IF NOT EXISTS sessions_online
                ON sessions(identity) WHERE ended IS NULL;
        """)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    def set(self, key, value):
        with self.lock, self.db:
            self._set(key, value)

    def _end_sessions(self, ts, reason):
        self.db.execute("UPDATE sessions SET ended=MAX(joined, ?), reason=? WHERE ended IS NULL",
                        (ts, reason))

    def log_gap(self, ts):
        with self.lock, self.db:
            self._end_sessions(ts, "log_gap")
            self.db.execute("DELETE FROM meta WHERE key IN ('cursor', 'complete_invocation')")

    def ingest(self, record):
        if not isinstance(record, dict):
            return None
        cursor = record.get("__CURSOR")
        message = record.get("MESSAGE", "")
        if not isinstance(cursor, str) or not isinstance(message, str):
            return None
        try:
            ts = int(record["__REALTIME_TIMESTAMP"]) / 1_000_000
        except (KeyError, TypeError, ValueError):
            return None
        invocation = record.get("_SYSTEMD_INVOCATION_ID", "")
        if not isinstance(invocation, str):
            invocation = ""
        match = PLAYER.search(message)
        starting = bool(re.search(r"\bStarting Server\s*$", message))
        kind = ("join" if match[1] == "connected" else "leave") if match else "start"
        event = None
        with self.lock, self.db:
            if match or starting:
                name = match[2] if match else None
                identity = (match[3] if match[3] != "0" else "name:" + name) if match else None
                inserted = self.db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?)",
                                           (cursor, ts, kind, name, identity, invocation)).rowcount
                if inserted:
                    previous = self.get("invocation", "")
                    if (invocation and invocation != previous) or starting:
                        self._end_sessions(ts, "restart")
                        self.db.execute("DELETE FROM meta WHERE key='complete_invocation'")
                    if invocation:
                        self._set("invocation", invocation)
                    if starting and invocation:
                        self._set("complete_invocation", invocation)
                    if kind == "join":
                        self.db.execute("UPDATE sessions SET ended=MAX(joined, ?), reason='reconnect' "
                                        "WHERE identity=? AND ended IS NULL", (ts, identity))
                        self.db.execute("INSERT INTO sessions(identity, name, invocation, joined) "
                                        "VALUES (?, ?, ?, ?)", (identity, name, invocation, ts))
                    elif kind == "leave":
                        self.db.execute("UPDATE sessions SET ended=MAX(joined, ?), reason='leave' "
                                        "WHERE identity=? AND ended IS NULL", (ts, identity))
                    if kind in ("join", "leave"):
                        event = {"kind": kind, "name": name, "ts": ts}
            self._set("cursor", cursor)
        return event

    def reconcile(self, status, ts):
        """Called after journal replay; a missing systemctl result is not a stop."""
        with self.lock, self.db:
            invocation = status.get("invocation", "")
            if status.get("active_state") not in (None, "unknown", "active"):
                self._end_sessions(ts, "stopped")
                self.db.execute("DELETE FROM meta WHERE key='complete_invocation'")
            elif invocation and invocation != self.get("invocation", ""):
                self._end_sessions(ts, "restart")
                self._set("invocation", invocation)
                self.db.execute("DELETE FROM meta WHERE key='complete_invocation'")

    def save_metric(self, sample):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO metrics VALUES (?, ?)",
                            (sample["ts"], json.dumps(sample, allow_nan=False)))

    def prune(self, now):
        with self.lock, self.db:
            self.db.execute("DELETE FROM metrics WHERE ts<?", (now - METRIC_DAYS * 86400,))
            self.db.execute("DELETE FROM events WHERE ts<?", (now - HISTORY_DAYS * 86400,))
            self.db.execute("DELETE FROM sessions WHERE ended IS NOT NULL AND ended<?",
                            (now - HISTORY_DAYS * 86400,))
            # Freed SQLite pages are reused; incremental writes do not grow forever.
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def online_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM sessions WHERE ended IS NULL").fetchone()[0]

    def view(self, hours, now):
        with self.lock:
            step = max(30, hours * 3600 / 480)
            metrics = self.db.execute("""
                SELECT data FROM metrics WHERE ts IN (
                    SELECT MAX(ts) FROM metrics WHERE ts>=? GROUP BY CAST(ts / ? AS INTEGER)
                ) ORDER BY ts
            """, (now - hours * 3600, step)).fetchall()
            online = self.db.execute("SELECT name, joined FROM sessions WHERE ended IS NULL "
                                     "ORDER BY joined").fetchall()
            events = self.db.execute("SELECT ts, kind, name FROM events ORDER BY ts DESC LIMIT 100").fetchall()
            sessions = self.db.execute("SELECT name, joined, ended, reason FROM sessions "
                                       "ORDER BY joined DESC LIMIT 100").fetchall()
            return {"metrics": [json.loads(row[0]) for row in metrics],
                    "online": [dict(row) for row in online],
                    "events": [dict(row) for row in events],
                    "sessions": [dict(row) for row in sessions]}


class NtfyNotifier:
    def __init__(self, url):
        self.url = url
        self.pending = queue.Queue(maxsize=100)
        self.worker = threading.Thread(target=self._run, name="ntfy", daemon=True)
        self.worker.start()

    def send(self, event):
        try:
            self.pending.put_nowait(event)
        except queue.Full:
            LOGGER.warning("ntfy queue is full; dropping %s event", event["kind"])

    def _deliver(self, event):
        action = "参加しました" if event["kind"] == "join" else "退出しました"
        request = Request(self.url, data=f'{event["name"]} が{action}'.encode("utf-8"),
                          headers={"Title": "Minecraft Bedrock",
                                   "Tags": "video_game," + ("heavy_plus_sign" if event["kind"] == "join"
                                                             else "heavy_minus_sign")},
                          method="POST")
        with urlopen(request, timeout=5) as response:
            response.read(1024)

    def _run(self):
        while True:
            event = self.pending.get()
            if event is None:
                return
            try:
                self._deliver(event)
            except OSError as error:
                LOGGER.warning("ntfy delivery failed: %s", error)

    def close(self):
        try:
            self.pending.put_nowait(None)
        except queue.Full:
            return
        self.worker.join(timeout=6)


class JournalReader:
    def __init__(self, store, unit, notifier=None):
        self.store = store
        self.unit = unit
        self.notifier = notifier
        self.health = "starting"
        self.last_success = None

    def poll(self, now):
        cursor = self.store.get("cursor")
        notifications_ready = self.store.get("notifications_ready") == "1"
        since = max(float(self.store.get("journal_until", now - METRIC_DAYS * 86400)),
                    now - METRIC_DAYS * 86400)
        # Very old saved rosters cannot survive a bounded replay window.
        if float(self.store.get("journal_until", now)) < now - METRIC_DAYS * 86400:
            self.store.log_gap(since)
            cursor = None
        for attempt in range(2):
            command = ["journalctl", "--unit=" + self.unit, "--output=json", "--quiet", "--no-pager",
                       "--grep=Player (connected|disconnected):|Starting Server$",
                       "--until=@" + str(now)]
            command += ["--after-cursor=" + cursor] if cursor else ["--since=@" + str(since)]
            # Drain stdout incrementally instead of loading a whole journal into RAM.
            process = None
            timer = None
            error_output = tempfile.TemporaryFile()
            try:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_output,
                                           env=dict(os.environ, LC_ALL="C"))
                timer = threading.Timer(20, process.kill)
                timer.daemon = True
                timer.start()
                while True:
                    line = process.stdout.readline(131073)
                    if not line:
                        break
                    if len(line) > 131072:
                        while line and not line.endswith(b"\n"):
                            line = process.stdout.readline(131073)
                        continue
                    try:
                        event = self.store.ingest(json.loads(line))
                        if event and notifications_ready and self.notifier:
                            self.notifier.send(event)
                    except (ValueError, TypeError):
                        continue
                code = process.wait()
                if code:
                    error_output.seek(0)
                    detail = error_output.read(4096).decode("utf-8", errors="replace")
                    if cursor and attempt == 0 and code > 0 and "cursor" in detail.lower():
                        # Vacuumed cursors: retain history, mark interrupted sessions,
                        # and resume from the last successful time instead of guessing.
                        self.store.log_gap(since)
                        cursor = None
                        continue
                    if code != 1 or detail.strip():
                        raise OSError("journalctl returned " + str(code))
                self.store.set("journal_until", now)
                self.store.set("notifications_ready", "1")
                self.health = "ok"
                self.last_success = now
                return
            except OSError as error:
                LOGGER.warning("Journal collection failed: %s", error)
                self.health = "error"
                return
            finally:
                if timer:
                    timer.cancel()
                if process:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stdout.close()
                error_output.close()


def service_status(unit):
    properties = ("ActiveState", "SubState", "MainPID", "NRestarts", "InvocationID")
    try:
        output = subprocess.run(["systemctl", "show", unit, "--no-pager",
                                 "--property=" + ",".join(properties)],
                                capture_output=True, text=True, timeout=5, check=True).stdout
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        return {"active_state": values.get("ActiveState", "unknown"),
                "sub_state": values.get("SubState", "unknown"),
                "pid": int(values.get("MainPID", 0)),
                "automatic_restarts": int(values.get("NRestarts", 0)),
                "invocation": values.get("InvocationID", "")}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        LOGGER.warning("Service status unavailable: %s", error)
        return {"active_state": "unknown", "sub_state": "unknown", "pid": 0,
                "automatic_restarts": None, "invocation": ""}


class Sampler:
    def __init__(self, install_dir, proc=Path("/proc")):
        self.install_dir = Path(install_dir)
        self.proc = Path(proc)
        self.previous = None
        self.process_previous = None
        self.ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

    def sample(self, status, now=None, monotonic=None):
        now = time.time() if now is None else now
        monotonic = time.monotonic() if monotonic is None else monotonic
        counters = [int(n) for n in (self.proc / "stat").read_text().splitlines()[0].split()[1:9]]
        total, idle = sum(counters), counters[3] + counters[4]
        memory = {}
        for line in (self.proc / "meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
        vm = dict(line.split() for line in (self.proc / "vmstat").read_text().splitlines())
        swap_in, swap_out = int(vm["pswpin"]), int(vm["pswpout"])
        sample = {"ts": now, "cpu_pct": None, "swap_in_bps": None, "swap_out_bps": None,
                  "memory_total": memory["MemTotal"], "memory_available": memory["MemAvailable"],
                  "memory_used": memory["MemTotal"] - memory["MemAvailable"],
                  "swap_total": memory["SwapTotal"], "swap_used": memory["SwapTotal"] - memory["SwapFree"],
                  "mc_rss": None, "mc_cpu_pct": None}
        if self.previous:
            old_total, old_idle, old_in, old_out, old_time = self.previous
            delta = total - old_total
            elapsed = monotonic - old_time
            if delta > 0:
                sample["cpu_pct"] = round(max(0, min(100, (1 - (idle - old_idle) / delta) * 100)), 2)
            if elapsed > 0:
                sample["swap_in_bps"] = max(0, swap_in - old_in) * self.page_size / elapsed
                sample["swap_out_bps"] = max(0, swap_out - old_out) * self.page_size / elapsed
        self.previous = (total, idle, swap_in, swap_out, monotonic)
        usage = shutil.disk_usage(self.install_dir)
        sample.update(disk_total=usage.total, disk_free=usage.free)
        pid = status.get("pid", 0)
        process_data = None
        if pid:
            try:
                fields = (self.proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
                ticks = int(fields[11]) + int(fields[12])
                identity = (pid, fields[19])
                process_data = (identity, ticks, monotonic)
                for line in (self.proc / str(pid) / "status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        sample["mc_rss"] = int(line.split()[1]) * 1024
                if self.process_previous and identity == self.process_previous[0]:
                    elapsed = monotonic - self.process_previous[2]
                    if elapsed > 0:
                        sample["mc_cpu_pct"] = max(0, ticks - self.process_previous[1]) / self.ticks / elapsed * 100
            except (OSError, ValueError, IndexError):
                pass  # A Bedrock restart can race with the /proc read.
        self.process_previous = process_data
        return sample


class Monitor:
    def __init__(self, store, install_dir, unit="minecraft.service", interval=30, notifier=None):
        self.store = store
        self.install_dir = Path(install_dir)
        self.unit = unit
        self.interval = interval
        self.sampler = Sampler(install_dir)
        self.journal = JournalReader(store, unit, notifier)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.current = None
        self.status = {"active_state": "unknown"}
        self.metric_error = False
        self.last_prune = 0

    def collect(self):
        now = time.time()
        self.journal.poll(now)
        status = service_status(self.unit)
        if self.journal.health == "ok":
            self.store.reconcile(status, now)
        sample = None
        try:
            sample = self.sampler.sample(status)
            sample["players"] = (self.store.online_count() if status["active_state"] == "active" else 0)
            if self.journal.health != "ok" or status["active_state"] == "unknown":
                sample["players"] = None
            self.store.save_metric(sample)
        except (OSError, KeyError, ValueError) as error:
            LOGGER.warning("Resource collection failed: %s", error)
        with self.lock:
            self.status = status
            self.current = sample
            self.metric_error = sample is None
        if now - self.last_prune >= 3600:
            self.store.prune(now)
            self.last_prune = now

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.collect()
            except Exception:
                LOGGER.exception("Collection cycle failed")
                with self.lock:
                    self.metric_error = True
                    self.journal.health = "error"
            self.stop.wait(max(1, self.interval - (time.monotonic() - started)))

    def snapshot(self, hours):
        now = time.time()
        with self.lock:
            result = self.store.view(hours, now)
            result.update(current=self.current, service=self.status.copy(), now=now,
                          interval=self.interval, metric_error=self.metric_error,
                          journal={"state": self.journal.health, "last_success": self.journal.last_success},
                          roster_complete=bool(self.status.get("invocation") and
                              self.store.get("complete_invocation") == self.status.get("invocation")))
        result["version"] = None
        try:
            for line in (self.install_dir / "install-manifest.txt").read_text().splitlines():
                if line.startswith("bds_version="):
                    result["version"] = line.split("=", 1)[1][:100]
                    break
        except OSError:
            pass
        return result


def make_handler(monitor, port):
    assets = Path(__file__).parent / "web"
    allowed_hosts = {"localhost:" + str(port), "127.0.0.1:" + str(port)}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def send_content(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self'; connect-src 'self'; img-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Restrict Host as well as bind address to prevent DNS rebinding.
            host = self.headers.get("Host", "").lower()
            origin = self.headers.get("Origin")
            if host not in allowed_hosts or (origin and origin != "http://" + host):
                self.send_content(b"Forbidden", "text/plain", 403)
                return
            path = urlsplit(self.path)
            if path.path == "/api/status":
                try:
                    hours = int(parse_qs(path.query).get("hours", ["6"])[0])
                    if hours not in (1, 6, 24, 168):
                        raise ValueError()
                except ValueError:
                    self.send_content(b"Invalid hours", "text/plain", 400)
                    return
                body = json.dumps(monitor.snapshot(hours), ensure_ascii=False, allow_nan=False).encode()
                self.send_content(body, "application/json; charset=utf-8")
                return
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            if path.path not in files:
                self.send_content(b"Not found", "text/plain", 404)
                return
            filename, mime = files[path.path]
            self.send_content((assets / filename).read_bytes(), mime + "; charset=utf-8")

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=Path("/opt/minecraft"))
    parser.add_argument("--db", type=Path, default=Path("/var/lib/minecraft-monitor/monitor.sqlite3"))
    parser.add_argument("--unit", default="minecraft.service")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--ntfy-url", default=os.environ.get("NTFY_URL", ""))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 10 <= args.interval <= 3600:
        parser.error("port must be 1..65535 and interval must be 10..3600")
    if not Path("/proc/stat").is_file():
        parser.error("this monitor needs Linux /proc and systemd")
    if args.ntfy_url and not args.ntfy_url.startswith(("https://", "http://")):
        parser.error("ntfy URL must use http or https")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    notifier = NtfyNotifier(args.ntfy_url) if args.ntfy_url else None
    monitor = Monitor(store, args.install_dir, args.unit, args.interval, notifier)
    server = HTTPServer(("127.0.0.1", args.port), make_handler(monitor, args.port))
    server.timeout = 1
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: monitor.stop.set())
    worker = threading.Thread(target=monitor.run, name="collector", daemon=True)
    worker.start()
    LOGGER.info("Dashboard listening on http://127.0.0.1:%s", args.port)
    try:
        while not monitor.stop.is_set():
            server.handle_request()
    finally:
        monitor.stop.set()
        server.server_close()
        worker.join(timeout=55)
        if notifier:
            notifier.close()
        if not worker.is_alive():
            store.close()


if __name__ == "__main__":
    main()
