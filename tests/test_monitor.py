import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer
from unittest.mock import patch

import monitor


def record(cursor, ts, message, invocation="a"):
    return {"__CURSOR": cursor, "__REALTIME_TIMESTAMP": str(int(ts * 1_000_000)),
            "MESSAGE": "[2026-10-08 00:00:00:000 INFO] " + message,
            "_SYSTEMD_INVOCATION_ID": invocation}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.store = monitor.Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_join_leave_duration_and_duplicate_replay(self):
        start = record("s", 10, "Starting Server")
        join = record("j", 20, "Player connected: Alex, comma, xuid: 123, pfid: abc")
        leave = record("l", 80, "Player disconnected: Alex, comma, xuid: 123, pfid: abc")
        for entry in (start, join, leave, start, join, leave):
            self.store.ingest(entry)
        view = self.store.view(1, 90)
        self.assertEqual(len(view["events"]), 3)
        self.assertEqual(view["online"], [])
        self.assertEqual(view["sessions"][0], {"name": "Alex, comma", "joined": 20,
                                               "ended": 80, "reason": "leave"})
        self.assertEqual(self.store.get("complete_invocation"), "a")

    def test_restart_with_and_without_start_log(self):
        self.store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        self.store.ingest(record("s", 30, "Starting Server", "b"))
        self.assertEqual(self.store.view(1, 40)["sessions"][0]["reason"], "restart")
        self.store.ingest(record("j2", 40, "Player connected: Alex, xuid: 123", "b"))
        self.store.ingest(record("j3", 50, "Player connected: Steve, xuid: 456", "c"))
        self.assertEqual([p["name"] for p in self.store.view(1, 60)["online"]], ["Steve"])
        self.assertIsNone(self.store.get("complete_invocation"))

    def test_service_stop_and_failed_status_query(self):
        self.store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        self.store.reconcile({"active_state": "unknown"}, 30)
        self.assertEqual(self.store.online_count(), 1)
        self.store.reconcile({"active_state": "inactive"}, 40)
        self.assertEqual(self.store.online_count(), 0)
        self.assertEqual(self.store.view(1, 50)["sessions"][0]["reason"], "stopped")

    def test_reconnect_and_clock_rollback_never_make_negative_duration(self):
        self.store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        self.store.ingest(record("j2", 10, "Player connected: Alex, xuid: 123"))
        view = self.store.view(1, 30)
        self.assertEqual(len(view["online"]), 1)
        closed = next(s for s in view["sessions"] if s["ended"] is not None)
        self.assertGreaterEqual(closed["ended"], closed["joined"])

    def test_log_gap_preserves_history_but_ends_roster(self):
        self.store.ingest(record("s", 10, "Starting Server"))
        self.store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        self.store.log_gap(25)
        self.assertIsNone(self.store.get("cursor"))
        self.assertIsNone(self.store.get("complete_invocation"))
        self.assertEqual(self.store.view(1, 30)["sessions"][0]["reason"], "log_gap")
        self.assertEqual(len(self.store.view(1, 30)["events"]), 2)

    def test_bounded_retention_and_metric_downsampling(self):
        self.store.ingest(record("old", 10, "Player disconnected: Alex, xuid: 1"))
        self.store.ingest(record("new", 9_999_000, "Player connected: Steve, xuid: 2"))
        for ts in range(0, 20000, 30):
            self.store.save_metric({"ts": ts})
        self.assertLessEqual(len(self.store.view(168, 20000)["metrics"]), 481)
        self.store.prune(10_000_000)
        self.assertEqual(len(self.store.view(1, 10_000_000)["events"]), 1)
        self.assertEqual(self.store.view(168, 10_000_000)["metrics"], [])
        self.assertEqual(self.store.online_count(), 1)

    def test_malformed_messages_and_names_without_xuid(self):
        self.store.ingest([1, 2, 3])
        self.store.ingest({"__CURSOR": "bad", "MESSAGE": [65, 66]})
        self.store.ingest(record("not-player", 10, "Player connected: nobody"))
        self.store.ingest(record("zero", 20, "Player connected: Alex, xuid: 0"))
        self.store.ingest(record("zero2", 20, "Player connected: Steve, xuid: 0"))
        self.assertEqual(self.store.online_count(), 2)

    def test_persistence_across_monitor_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = monitor.Store(path)
            store.ingest(record("s", 10, "Starting Server"))
            store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
            store.set("journal_until", 30)
            store.close()
            resumed = monitor.Store(path)
            try:
                self.assertEqual(resumed.get("cursor"), "j")
                self.assertEqual(resumed.get("complete_invocation"), "a")
                self.assertEqual(resumed.online_count(), 1)
                resumed.ingest(record("l", 40, "Player disconnected: Alex, xuid: 123"))
                self.assertEqual(resumed.view(1, 50)["sessions"][0]["ended"], 40)
            finally:
                resumed.close()

    def test_public_snapshot_omits_xuid_and_database_cursor(self):
        self.store.ingest(record("j", 20, "Player connected: Alex, xuid: 1234567890"))
        snapshot = monitor.Monitor(self.store, Path(".")).snapshot(1)
        self.assertNotIn("1234567890", json.dumps(snapshot))
        self.assertNotIn("identity", json.dumps(snapshot))
        self.assertNotIn("cursor", json.dumps(snapshot))


class ResourceTests(unittest.TestCase):
    def test_proc_cpu_swap_rates_process_identity_and_first_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "meminfo").write_text("MemTotal: 1024000 kB\nMemAvailable: 256000 kB\nSwapTotal: 4194304 kB\nSwapFree: 4000000 kB\n")
            (root / "stat").write_text("cpu 100 0 100 800 0 0 0 0 100 0\n")
            (root / "vmstat").write_text("pswpin 10\npswpout 20\n")
            process = root / "123"
            process.mkdir()

            def write_process(ticks, start):
                fields = ["S"] + ["0"] * 21
                fields[11], fields[12], fields[19] = str(ticks), "0", str(start)
                (process / "stat").write_text("123 (bedrock server) " + " ".join(fields))
                (process / "status").write_text("VmRSS: 200000 kB\n")

            write_process(100, 1)
            sampler = monitor.Sampler(root, proc=root)
            sampler.ticks, sampler.page_size = 100, 4096
            first = sampler.sample({"pid": 123}, now=100, monotonic=10)
            self.assertIsNone(first["cpu_pct"])
            self.assertIsNone(first["mc_cpu_pct"])
            self.assertEqual(first["mc_rss"], 200000 * 1024)
            (root / "stat").write_text("cpu 200 0 100 900 0 0 0 0 200 0\n")
            (root / "vmstat").write_text("pswpin 20\npswpout 40\n")
            write_process(200, 1)
            second = sampler.sample({"pid": 123}, now=80, monotonic=20)
            self.assertEqual(second["cpu_pct"], 50)
            self.assertEqual(second["swap_in_bps"], 4096)
            self.assertEqual(second["swap_out_bps"], 8192)
            self.assertEqual(second["mc_cpu_pct"], 10)
            write_process(1000, 2)
            self.assertIsNone(sampler.sample({"pid": 123}, now=120, monotonic=30)["mc_cpu_pct"])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        class FakeMonitor:
            def snapshot(self, hours):
                return {"hours": hours, "name": "<script>alert('x')</script>"}

        self.server = HTTPServer(("127.0.0.1", 0), monitor.make_handler(FakeMonitor(), 8080))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def get(self, path, host="localhost:8080", origin=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": host}
        if origin:
            headers["Origin"] = origin
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        status, body, csp = response.status, response.read(), response.getheader("Content-Security-Policy")
        connection.close()
        return status, body, csp

    def test_api_and_assets(self):
        status, body, csp = self.get("/api/status?hours=24")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["hours"], 24)
        self.assertIn("frame-ancestors 'none'", csp)
        for path in ("/", "/app.js", "/style.css"):
            self.assertEqual(self.get(path)[0], 200)
        index = self.get("/")[1].decode()
        self.assertIn('href="style.css"', index)
        self.assertIn('src="app.js"', index)
        self.assertNotIn('href="/style.css"', index)

    def test_rebinding_cross_origin_bad_range_and_path_traversal(self):
        self.assertEqual(self.get("/api/status", host="attacker.example:8080")[0], 403)
        self.assertEqual(self.get("/api/status", origin="http://attacker.example")[0], 403)
        self.assertEqual(self.get("/api/status?hours=999999")[0], 400)
        self.assertEqual(self.get("/../monitor.py")[0], 404)
        self.assertEqual(self.get("/api/status", origin="http://localhost:8080")[0], 200)


class FakeProcess:
    def __init__(self, lines, code):
        import io
        self.stdout = io.BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in lines))
        self.code = code

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        self.code = -9


class JournalTests(unittest.TestCase):
    def test_empty_filtered_poll_is_healthy(self):
        store = monitor.Store(":memory:")
        self.addCleanup(store.close)
        reader = monitor.JournalReader(store, "minecraft.service")
        with patch("monitor.subprocess.Popen", return_value=FakeProcess([], 1)):
            reader.poll(30)
        self.assertEqual(reader.health, "ok")
        self.assertEqual(store.get("journal_until"), "30")
        self.assertEqual(store.get("notifications_ready"), "1")

    def test_initial_replay_is_silent_then_new_events_notify(self):
        class FakeNotifier:
            def __init__(self):
                self.events = []

            def send(self, event):
                self.events.append(event)

        store = monitor.Store(":memory:")
        self.addCleanup(store.close)
        notifier = FakeNotifier()
        reader = monitor.JournalReader(store, "minecraft.service", notifier)
        join = record("j", 20, "Player connected: Alex, xuid: 123")
        leave = record("l", 40, "Player disconnected: Alex, xuid: 123")
        with patch("monitor.subprocess.Popen", side_effect=[FakeProcess([join], 0),
                                                            FakeProcess([leave], 0)]) as run:
            reader.poll(30)
            reader.poll(50)
        self.assertEqual(notifier.events, [{"kind": "leave", "name": "Alex", "ts": 40}])
        self.assertIn("--grep=Player (connected|disconnected):|Starting Server$",
                      run.call_args_list[0].args[0])

    def test_ntfy_request_contains_player_event(self):
        notifier = object.__new__(monitor.NtfyNotifier)
        notifier.url = "https://ntfy.example/topic"
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        with patch("monitor.urlopen", return_value=response) as send:
            notifier._deliver({"kind": "join", "name": "Alex", "ts": 20})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.example/topic")
        self.assertEqual(request.data.decode(), "Alex が参加しました")
        self.assertEqual(request.get_header("Title"), "Minecraft Bedrock")

    def test_cursor_vacuum_fallback_no_double_sessions(self):
        store = monitor.Store(":memory:")
        self.addCleanup(store.close)
        join = record("j", 20, "Player connected: Alex, xuid: 123")
        store.ingest(join)
        store.set("journal_until", 30)
        reader = monitor.JournalReader(store, "minecraft.service")
        leave = record("l", 40, "Player disconnected: Alex, xuid: 123")
        processes = iter([FakeProcess([], 1), FakeProcess([join, leave], 0)])

        def spawn(*args, **kwargs):
            process = next(processes)
            if process.code:
                kwargs["stderr"].write(b"Failed to seek to cursor: Invalid argument")
            return process

        with patch("monitor.subprocess.Popen", side_effect=spawn) as run:
            reader.poll(50)
        self.assertIn("--after-cursor=j", run.call_args_list[0].args[0])
        self.assertIn("--since=@30.0", run.call_args_list[1].args[0])
        self.assertEqual(reader.health, "ok")
        self.assertEqual(store.online_count(), 0)
        self.assertEqual(len(store.view(1, 60)["sessions"]), 1)
        self.assertEqual(store.get("cursor"), "l")

    def test_unavailable_journal_does_not_clear_existing_roster(self):
        store = monitor.Store(":memory:")
        self.addCleanup(store.close)
        store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        reader = monitor.JournalReader(store, "minecraft.service")
        with patch("monitor.subprocess.Popen", side_effect=OSError("not installed")):
            reader.poll(30)
        self.assertEqual(reader.health, "error")
        self.assertEqual(store.online_count(), 1)

    def test_permission_failure_does_not_invalidate_cursor_or_sessions(self):
        store = monitor.Store(":memory:")
        self.addCleanup(store.close)
        store.ingest(record("j", 20, "Player connected: Alex, xuid: 123"))
        reader = monitor.JournalReader(store, "minecraft.service")
        with patch("monitor.subprocess.Popen", return_value=FakeProcess([], 2)) as run:
            reader.poll(30)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(reader.health, "error")
        self.assertEqual(store.get("cursor"), "j")
        self.assertEqual(store.online_count(), 1)


if __name__ == "__main__":
    unittest.main()
