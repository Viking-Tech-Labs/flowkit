"""Run with `python3 -m unittest discover plugins/handoff/tests`. The status line writes under a
config dir of the test's own, and flowkit-usage reads there with a stub `claude` that never answers."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
STATUSLINE = PLUGIN / "scripts" / "statusline.py"
WEEK = 2000000000  # a reset far ahead, so the usage line says when


class StatusLine(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.claude = self.tmp / "claude"
        (self.tmp / "bin").mkdir()
        stub = self.tmp / "bin" / "claude"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
        self.env = {"HOME": str(self.tmp), "CLAUDE_CONFIG_DIR": str(self.claude), "TMPDIR": str(self.tmp),
                    "PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin"}
        self.calls = {}

    def log_in(self, account, env=None):
        env = env or self.env
        home = Path(env.get("CLAUDE_CONFIG_DIR") or env["HOME"])
        home.mkdir(exist_ok=True)
        (home / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"accountUuid": account, "organizationUuid": "org-" + account}}))

    def refresh(self, sid="s1", week=None, five=None, five_ends=None, call=True, reset=WEEK, then=None, env=None,
                **more):
        """One status line refresh of session `sid`; `call` says it made an API call since its last."""
        self.calls[sid] = self.calls.get(sid, 0) + (1 if call else 0)
        data = {"session_id": sid, "model": {"id": "model-a", "display_name": "Model A"}, "effort": {"level": "high"},
                "cost": {"total_cost_usd": 1.5, "total_api_duration_ms": self.calls[sid]},
                "context_window": {"used_percentage": 34.4, "context_window_size": 200000}, **more}
        if week is not None or five is not None:
            data["rate_limits"] = {"seven_day": {"used_percentage": week, "resets_at": reset},
                                   "five_hour": {"used_percentage": five, "resets_at": five_ends}}
        out = subprocess.run(["python3", str(STATUSLINE), *(["--then", then] if then else [])],
                             input=json.dumps(data), capture_output=True, text=True, env=env or self.env)
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        return out.stdout

    def weeks(self):
        return {p.name: json.loads(p.read_text()) for p in (self.claude / "usage-stats").glob("week-*.json")}

    def week(self):
        (found,) = self.weeks().values()
        return found

    def series(self):
        return [json.loads(x) for x in (self.claude / "usage-stats" / "series.jsonl").read_text().splitlines()]

    def usage(self, *args, env=None):
        return subprocess.run(["python3", str(PLUGIN / "bin" / "flowkit-usage"), *args],
                              capture_output=True, text=True, env=env or self.env)

    def test_the_session_file_holds_the_context_size(self):
        self.assertEqual(self.refresh(session_name="a/b").strip(), "Model A high · ctx 34% · $1.50")
        stats = json.loads((self.claude / "session-stats" / "s1.json").read_text())
        self.assertEqual((stats["ctx_pct"], stats["ctx_size"], stats["cost_usd"], stats["api_ms"], stats["model_id"]),
                         (34.4, 200000, 1.5, 1, "model-a"))
        self.assertEqual(json.loads((self.claude / "session-stats" / "by-name" / "a-b.json").read_text()), stats)
        self.assertFalse((self.claude / "usage-stats").exists(), "a refresh without rate limits records no week")

    def test_a_week_file_keeps_the_highest_figures_and_the_series_its_changes(self):
        self.refresh(week=20, five=30, five_ends=500)
        self.assertEqual(list(self.weeks()), [f"week-{WEEK}.json"], "no login to read: no key in the name")
        first = self.week()
        self.assertEqual({k: first[k] for k in first if k != "updated"},
                         {"resets_at": WEEK, "account": None, "seven_day_pct": 20, "five_hour_pct": 30,
                          "five_hour_resets_at": 500, "five_hour_peak_pct": 30, "full_at": None, "reset_at": None,
                          "pre_reset_pct": None})
        self.refresh(week=20, five=30, five_ends=500)
        self.refresh("idle", week=12, five=10, five_ends=500, call=False)
        self.assertEqual((self.week(), len(self.series())), (first, 1), "nothing new: neither file is written")
        self.refresh("s2", week=23, five=41, five_ends=500)
        self.refresh(week=22, five=8, five_ends=900)
        now = self.week()
        self.assertEqual((now["seven_day_pct"], now["five_hour_pct"], now["five_hour_resets_at"],
                          now["five_hour_peak_pct"]), (23, 8, 900, 41), "a later 5-hour window starts over")
        self.refresh("s2", week=23, five=50, five_ends=500, call=False)
        self.assertEqual((self.week()["five_hour_pct"], self.week()["five_hour_peak_pct"]), (8, 50),
                         "a reading of a window that is over only moves the week's peak")
        self.assertEqual([(x["session_id"], x["seven_day_pct"], x["five_hour_pct"]) for x in self.series()],
                         [("s1", 20, 30), ("s2", 23, 41), ("s1", 23, 8), ("s2", 23, 8)])
        self.assertEqual(set(self.series()[0]), {"t", "resets_at", "account", "seven_day_pct", "five_hour_pct",
                                                 "five_hour_resets_at", "session_id", "model_id", "cost_usd"})
        self.assertEqual([p.name for p in (self.claude / "usage-stats").iterdir() if p.name.startswith(".")], [])

    def test_only_a_session_that_called_the_api_sees_a_reset(self):
        self.refresh(week=97)
        self.refresh(week=100)
        full = self.week()["full_at"]
        self.assertTrue(full)
        self.refresh("idle", week=60, call=False)
        self.refresh("new", week=60)
        self.assertEqual((self.week()["seven_day_pct"], self.week()["reset_at"]), (100, None),
                         "an idle session, and one seen for the first time, may show old figures")
        self.refresh(week=97)
        self.assertEqual(self.week()["seven_day_pct"], 100, "under 5 points is no reset")
        self.refresh(week=3)
        week = self.week()
        self.assertEqual((week["seven_day_pct"], week["pre_reset_pct"], bool(week["reset_at"]), week["full_at"]),
                         (3, 100, True, full))
        self.refresh("idle", week=100, call=False)
        self.assertEqual(self.week()["seven_day_pct"], 3, "after a reset an idle session raises nothing")
        self.refresh("new", week=4)
        self.assertEqual(self.week()["seven_day_pct"], 4)

    def test_two_logins_on_one_folder_keep_a_week_each(self):
        other = {k: v for k, v in self.env.items() if k != "CLAUDE_CONFIG_DIR"}
        other["HOME"] = str(self.tmp / "home")
        (self.tmp / "home").mkdir()
        (self.tmp / "home" / ".claude").symlink_to(self.claude)
        self.log_in("one")
        self.log_in("two", other)
        self.refresh(week=40, five=5, five_ends=500)
        self.refresh("s2", week=70, five=6, five_ends=500, env=other)
        self.assertEqual(len(self.weeks()), 2, "one reset time, two files")
        self.assertEqual(sorted((w["account"]["account"], w["seven_day_pct"]) for w in self.weeks().values()),
                         [("one", 40), ("two", 70)])
        self.assertEqual([x["account"]["account"] for x in self.series()], ["one", "two"])
        self.assertIn("Week 40% used", self.usage().stdout)
        self.assertIn("Week 70% used", self.usage(env=other).stdout)
        old = json.loads(self.usage("--json").stdout)
        self.assertEqual((old["source"], old["account"], old["five_hour"]), ("old", None,
                                                                              {"percent": 5, "resets_at": 500}))

    def test_flowkit_usage_reads_a_week_of_no_login_and_breaks_a_tie(self):
        self.log_in("one")
        self.assertEqual(self.usage().returncode, 1)
        weeks = self.claude / "usage-stats"
        weeks.mkdir()
        (weeks / "week-5.json").write_text('{"seven_day_pct": 10, "resets_at": 5, "updated": "2026-01-01T00:00:00Z"}')
        (weeks / "week-4.json").write_text('{"seven_day_pct": 11, "resets_at": 4, "updated": "2026-01-02T00:00:00Z"}')
        (weeks / "week-6.json").write_text(json.dumps({"seven_day_pct": 99, "resets_at": 6, "account": {
            "account": "two", "organization": "org-two"}}))
        for name in ("week-4.json", "week-5.json"):
            os.utime(weeks / name, (1000, 1000))
        self.assertIn("Week 11% used", self.usage().stdout, "one change time: the later `updated`")
        (weeks / "week-5.json").write_text('{"seven_day_pct": 10, "resets_at": 5, "updated": "2026-01-02T00:00:00Z"}')
        os.utime(weeks / "week-5.json", (1000, 1000))
        self.assertIn("Week 10% used", self.usage().stdout, "that too the same: the later name")
        self.refresh(week=12)
        self.assertIn("Week 12% used", self.usage().stdout)

    def test_then_runs_the_users_own_line_after_recording(self):
        self.assertEqual(self.refresh(week=5, then="python3 -c 'import sys, json; print(json.load(sys.stdin)[\"session_id\"])'"),
                         "s1\n")
        self.assertEqual(self.week()["seven_day_pct"], 5)


if __name__ == "__main__":
    unittest.main()
