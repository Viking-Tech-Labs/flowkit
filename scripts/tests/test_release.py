"""Run with `python3 -m unittest discover scripts/tests`. Each test builds a throwaway repository with
one plugin, a bare `origin`, a stub scripts/test.sh and a stub `claude` on PATH, copies
release.py into it and runs it there, so nothing reaches this repository, its remote or the
machine's plugins. Global and system git config are switched off for the same reason."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RELEASE = Path(__file__).resolve().parent.parent / "release.py"
PLUGIN_JSON = "plugins/demo/.claude-plugin/plugin.json"


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
                        GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="maintenance.auto", GIT_CONFIG_VALUE_0="false",
                        CLAUDE_LOG=str(self.tmp / "claude.log"), STUB_TESTS_EXIT="0")
        stubs = self.tmp / "stubs"
        stubs.mkdir()
        claude = stubs / "claude"
        claude.write_text('#!/bin/sh\necho "$@" >> "$CLAUDE_LOG"\n')
        claude.chmod(0o755)
        self.env["PATH"] = f"{stubs}{os.pathsep}{self.env['PATH']}"

        self.origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        self.work = self.tmp / "work"
        self.git(self.tmp, "init", "--quiet", "-b", "main", str(self.work))
        self.write(PLUGIN_JSON, json.dumps({"name": "demo", "description": "a demo", "version": "0.1.0",
                                            "author": {"name": "flowkit"}}, indent=2) + "\n")
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "kit", "plugins": []}) + "\n")
        self.write("scripts/test.sh", '#!/bin/sh\necho stub tests\nexit "$STUB_TESTS_EXIT"\n', mode=0o755)
        shutil.copy(RELEASE, self.work / "scripts" / "release.py")
        self.git(self.work, "add", ".")
        self.git(self.work, "commit", "--quiet", "-m", "demo 0.1.0")
        self.git(self.work, "remote", "add", "origin", str(self.origin))
        self.git(self.work, "push", "--quiet", "-u", "origin", "main")
        self.git(self.work, "switch", "--quiet", "-c", "v3")
        self.write("plugins/demo/README.md", "a change\n")
        self.git(self.work, "add", ".")
        self.git(self.work, "commit", "--quiet", "-m", "demo: a change")

    def git(self, where, *args):
        return subprocess.run(["git", "-C", str(where), *args], env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, rel, text, mode=None):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if mode:
            path.chmod(mode)

    def release(self, *args, **env):
        return subprocess.run(["python3", str(self.work / "scripts" / "release.py"), *args],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def state(self):
        """Everything a release may change: refs here and on origin, the files, the machine's plugins."""
        return (self.git(self.work, "for-each-ref", "--format=%(refname) %(objectname)"),
                self.git(self.origin, "for-each-ref", "--format=%(refname) %(objectname)"),
                self.git(self.work, "status", "--porcelain"),
                (self.work / PLUGIN_JSON).read_text(),
                (self.tmp / "claude.log").exists())

    def test_dry_run_prints_every_step_and_changes_nothing(self):
        before = self.state()
        done = self.release("demo", "minor", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        for line in ("[1] checks", "would fetch origin", "0.1.0 -> 0.2.0", "[2] tests", "would run every suite",
                     "[3] bump", '[4] commit: "demo 0.2.0"', "[5] tag: demo-v0.2.0", "[6] fast-forward main to v3",
                     "[7] push: main and demo-v0.2.0", "[8] this machine: claude plugin marketplace update kit",
                     "[9] this machine: claude plugin update demo@kit", "would release demo-v0.2.0"):
            self.assertIn(line, done.stdout)
        self.assertNotIn("stub tests", done.stdout, "a dry run does not run the tests")
        self.assertEqual(self.state(), before)

    def test_release_from_a_branch_bumps_tags_fast_forwards_pushes_and_updates(self):
        before = (self.work / PLUGIN_JSON).read_text()
        done = self.release("demo", "minor")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("stub tests", done.stdout)
        self.assertEqual((self.work / PLUGIN_JSON).read_text(), before.replace('"0.1.0"', '"0.2.0"'),
                         "only the version changes, formatting kept")
        head = self.git(self.work, "rev-parse", "HEAD")
        self.assertEqual(self.git(self.work, "log", "-1", "--format=%s"), "demo 0.2.0")
        self.assertEqual(self.git(self.work, "diff", "--name-only", "HEAD~1", "HEAD"), PLUGIN_JSON)
        self.assertEqual(self.git(self.work, "rev-parse", "demo-v0.2.0^{commit}"), head)
        self.assertEqual(self.git(self.work, "rev-parse", "main"), head)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), head)
        self.assertEqual(self.git(self.origin, "rev-parse", "demo-v0.2.0^{commit}"), head)
        self.assertEqual((self.tmp / "claude.log").read_text(), "plugin marketplace update kit\nplugin update demo@kit\n")
        self.assertEqual(self.git(self.work, "status", "--porcelain"), "")

    def test_patch_step(self):
        done = self.release("demo", "patch")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads((self.work / PLUGIN_JSON).read_text())["version"], "0.1.1")
        self.assertEqual(self.git(self.origin, "tag", "-l"), "demo-v0.1.1")

    def test_release_on_main_needs_no_fast_forward(self):
        self.git(self.work, "switch", "--quiet", "main")
        self.git(self.work, "merge", "--quiet", "--ff-only", "v3")
        self.git(self.work, "branch", "--quiet", "-D", "v3")
        done = self.release("demo", "patch")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("[6] fast-forward: none", done.stdout)
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), self.git(self.work, "rev-parse", "HEAD"))

    def own(self):
        """The branches and tags here, the plugin.json and the tree: what a failed release puts back
        (a fetch may still add a remote-tracking ref)."""
        return (self.git(self.work, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags"),
                *self.state()[1:])

    def assertRefused(self, reason, *args, **env):
        before = self.state()
        done = self.release("demo", "minor", *args, **env)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refused: " + reason, done.stderr)
        self.assertEqual(self.state()[1:], before[1:], "nothing pushed, bumped or updated")
        return done

    def test_refuses_a_dirty_tree(self):
        self.write("stray.txt", "x\n")
        self.assertRefused("the working tree is not clean")

    def test_refuses_red_tests(self):
        before = self.own()
        self.assertRefused("the tests are red", STUB_TESTS_EXIT="1")
        self.assertEqual(self.own(), before, "no commit, no tag")

    def test_refuses_a_branch_behind_its_upstream(self):
        self.git(self.work, "push", "--quiet", "-u", "origin", "v3")
        other = self.tmp / "other"
        self.git(self.tmp, "clone", "--quiet", "-b", "v3", str(self.origin), str(other))
        (other / "more.txt").write_text("more\n")
        self.git(other, "add", ".")
        self.git(other, "commit", "--quiet", "-m", "more")
        self.git(other, "push", "--quiet")
        self.assertRefused("v3 is behind its upstream origin/v3")

    def test_refuses_when_main_moved_on_upstream(self):
        other = self.tmp / "other"
        self.git(self.tmp, "clone", "--quiet", str(self.origin), str(other))
        (other / "more.txt").write_text("more\n")
        self.git(other, "add", ".")
        self.git(other, "commit", "--quiet", "-m", "more")
        self.git(other, "push", "--quiet")
        self.assertRefused("v3 does not contain origin/main")

    def test_refuses_an_existing_tag(self):
        self.git(self.work, "tag", "demo-v0.2.0", "main")
        self.assertRefused("tag demo-v0.2.0 exists already")

    def hook(self, name, script="#!/bin/sh\necho blocked by the hook >&2\nexit 1\n"):
        path = self.work / ".git" / "hooks" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(script)
        path.chmod(0o755)
        return path

    def test_a_blocked_push_puts_everything_back_and_the_rerun_releases_the_same_version(self):
        hook, before = self.hook("pre-push"), self.own()
        done = self.assertRefused("git push")
        self.assertIn("blocked by the hook", done.stderr)
        self.assertIn("nothing released, demo is at 0.1.0 as before", done.stderr)
        self.assertEqual(self.own(), before, "no bump commit, no tag, main where it was")
        hook.unlink()
        again = self.release("demo", "minor")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.git(self.origin, "tag", "-l"), "demo-v0.2.0")
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), self.git(self.work, "rev-parse", "HEAD"))

    def test_a_blocked_push_on_main_puts_main_back(self):
        self.git(self.work, "switch", "--quiet", "main")
        self.git(self.work, "merge", "--quiet", "--ff-only", "v3")
        self.hook("pre-push")
        before = self.own()
        self.assertRefused("git push")
        self.assertEqual(self.own(), before)

    def test_a_blocked_commit_leaves_a_clean_tree(self):
        self.hook("pre-commit")
        before = self.own()
        done = self.assertRefused("git commit")
        self.assertIn("nothing released, demo is at 0.1.0 as before", done.stderr)
        self.assertEqual(self.own(), before)
        self.assertEqual(self.git(self.work, "status", "--porcelain", "--untracked-files=all"), "")

    def test_refuses_while_the_current_versions_tag_never_reached_origin(self):
        self.git(self.work, "tag", "demo-v0.1.0", "main")
        self.assertRefused("tag demo-v0.1.0 is here and not on origin")
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/demo-v0.1.0")
        done = self.release("demo", "minor")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_refuses_tests_that_leave_changes_before_the_bump(self):
        self.write("scripts/test.sh", '#!/bin/sh\necho left > "$(dirname "$0")/../left.txt"\n', mode=0o755)
        self.git(self.work, "commit", "--quiet", "-am", "tests that leave a file")
        before = (self.work / PLUGIN_JSON).read_text()
        done = self.release("demo", "minor")
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("refused: the tests left changes: ?? left.txt", done.stderr)
        self.assertEqual((self.work / PLUGIN_JSON).read_text(), before)
        self.assertEqual(self.git(self.work, "status", "--porcelain", "--untracked-files=all"), "?? left.txt")

    def test_refuses_an_untracked_file_git_was_told_not_to_show(self):
        self.git(self.work, "config", "status.showUntrackedFiles", "no")
        self.write("stray.txt", "x\n")
        self.assertRefused("the working tree is not clean")

    def test_refuses_a_flag_or_a_step_that_does_not_go_with_the_command(self):
        before = self.state()
        for args in (("tags", "--create", "--dry-run"), ("tags", "--dry-run"), ("tags", "patch"),
                     ("demo", "patch", "--create")):
            done = self.release(*args)
            self.assertEqual(done.returncode, 2, args)
            self.assertIn("release.py: error:", done.stderr)
        self.assertEqual(self.state(), before)

    def test_refuses_an_unknown_plugin(self):
        done = self.release("nope", "patch")
        self.assertEqual(done.returncode, 1)
        self.assertIn("refused: no plugin named 'nope'", done.stderr)

    def test_tags_lists_and_creates_the_missing_ones(self):
        first = self.git(self.work, "rev-parse", "main")
        self.git(self.work, "tag", "demo-v0.1.0", first)
        self.git(self.work, "switch", "--quiet", "main")
        for version in ("0.1.1", "0.2.0"):
            path = self.work / PLUGIN_JSON
            path.write_text(path.read_text().replace(json.loads(path.read_text())["version"], version))
            self.git(self.work, "commit", "--quiet", "-am", f"demo {version}")
        bumps = self.git(self.work, "rev-list", "-2", "main").splitlines()[::-1]
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/demo-v0.1.0")
        listed = self.release("tags")
        self.assertEqual(listed.returncode, 1, "a missing tag is a failed check")
        self.assertEqual(listed.stdout, f"demo-v0.1.1 {bumps[0][:7]} missing\ndemo-v0.2.0 {bumps[1][:7]} missing\n")
        self.assertEqual(self.git(self.work, "tag", "-l"), "demo-v0.1.0")
        created = self.release("tags", "--create")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(created.stdout, f"demo-v0.1.1 {bumps[0][:7]} tagged\ndemo-v0.2.0 {bumps[1][:7]} tagged\n"
                                         "push them: git push origin refs/tags/demo-v0.1.1 refs/tags/demo-v0.2.0\n")
        self.assertEqual(self.git(self.work, "rev-parse", "demo-v0.1.1^{commit}"), bumps[0])
        self.assertEqual(self.git(self.work, "rev-parse", "demo-v0.2.0^{commit}"), bumps[1])
        unpushed = self.release("tags")
        self.assertEqual(unpushed.returncode, 1, "a tag that never reached origin is a failed check")
        self.assertEqual(unpushed.stdout, "demo-v0.1.1 not on origin\ndemo-v0.2.0 not on origin\n"
                                          "push them: git push origin refs/tags/demo-v0.1.1 refs/tags/demo-v0.2.0\n")
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/demo-v0.1.1", "refs/tags/demo-v0.2.0")
        again = self.release("tags")
        self.assertEqual((again.returncode, again.stdout), (0, "every version has its tag\n"))

    def test_tags_does_not_create_a_tag_only_origin_has(self):
        self.git(self.work, "tag", "demo-v0.1.0", "main")
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/demo-v0.1.0")
        self.git(self.work, "tag", "--delete", "demo-v0.1.0")
        for args in (("tags",), ("tags", "--create")):
            done = self.release(*args)
            self.assertEqual((done.returncode, done.stdout),
                             (1, "demo-v0.1.0 on origin only: git fetch --tags origin\n"), args)
            self.assertEqual(self.git(self.work, "tag", "-l"), "")


if __name__ == "__main__":
    unittest.main()
