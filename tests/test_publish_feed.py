import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_feed import generate

ROOT = Path(__file__).resolve().parents[1]


class PublishFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.config = json.loads((ROOT / "feed.json").read_text())
        self.config["sources"] = [{"repository": "example/source", "directory": "source",
                                   "label": "Test", "prefix": ""}]
        self.source = self.root / "sources/source"
        self.source.mkdir(parents=True)
        self.git(self.source, "init", "-b", "main")
        self.identity(self.source)
        self.update_source("Original article")

        self.remote = self.root / "remote.git"
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.writer = self.root / "writer"
        self.writer.mkdir()
        self.git(self.writer, "init", "-b", "main")
        self.identity(self.writer)
        (self.writer / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/generate_feed.py", self.writer / "scripts")
        (self.writer / "feed.json").write_text(json.dumps(self.config))
        (self.writer / ".gitignore").write_text(".sources/\n")
        self.write_feed(self.writer)
        self.git(self.writer, "add", ".")
        self.git(self.writer, "commit", "-m", "Initial feed")
        self.git(self.writer, "remote", "add", "origin", str(self.remote))
        self.git(self.writer, "push", "origin", "main")
        self.runner = self.root / "runner"
        self.git(self.root, "clone", str(self.remote), str(self.runner))
        self.identity(self.runner)
        (self.runner / ".sources").mkdir()
        self.git(self.root, "clone", str(self.source), str(self.runner / ".sources/source"))

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args],
                                       env=self.env, stderr=subprocess.PIPE,
                                       text=True, timeout=10).strip()

    def identity(self, repo):
        self.git(repo, "config", "user.name", "Publication test")
        self.git(repo, "config", "user.email", "test@example.com")

    def update_source(self, title):
        (self.source / "article.md").write_text(f"# {title}\n\nDocument body.\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-m", title)

    def write_feed(self, repo):
        content, _ = generate(self.config, self.root / "sources")
        (repo / "releases.atom").write_bytes(content)

    def publish(self):
        command = [sys.executable, str(ROOT / "scripts/publish_feed.py")]
        with subprocess.Popen(command, cwd=self.runner, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def install_race_hook(self, *, every_push=False, update_source=False):
        hook = self.runner / ".git/hooks/pre-push"
        hook.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""\
            import os
            from pathlib import Path
            import subprocess
            import sys

            marker = Path({str(self.root / 'push-attempts')!r})
            attempt = int(marker.read_text()) + 1 if marker.exists() else 1
            marker.write_text(str(attempt))
            if attempt > 1 and not {every_push!r}:
                sys.exit(0)
            env = dict(os.environ)
            for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"):
                env.pop(key, None)

            def git(repo, *args):
                subprocess.run(["git", "-C", str(repo), *args], env=env,
                               check=True, timeout=10)

            source = Path({str(self.source)!r})
            writer = Path({str(self.writer)!r})
            if {update_source!r}:
                (source / "article.md").write_text("# Latest during push\\n")
                git(source, "add", ".")
                git(source, "commit", "-m", "New source article during publication")
            (writer / "README.md").write_text(f"Concurrent change {{attempt}}\\n")
            git(writer, "add", ".")
            git(writer, "commit", "-m", f"Concurrent change {{attempt}}")
            git(writer, "push", "origin", "main")
            """))
        hook.chmod(0o755)

    def test_stale_run_preserves_newer_feed_and_other_main_changes(self):
        self.update_source("Intermediate article")
        self.git(self.runner / ".sources/source", "pull", "--ff-only", "origin", "main")
        self.write_feed(self.runner)
        self.update_source("Latest article")
        self.write_feed(self.writer)
        (self.writer / "README.md").write_text("Concurrent main change\n")
        self.git(self.writer, "add", ".")
        self.git(self.writer, "commit", "-m", "Publish latest feed and documentation")
        self.git(self.writer, "push", "origin", "main")
        latest = self.git(self.remote, "rev-parse", "main")

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), latest)
        self.assertEqual((self.runner / "releases.atom").read_bytes(),
                         (self.writer / "releases.atom").read_bytes())
        self.assertEqual((self.runner / "README.md").read_text(), "Concurrent main change\n")
        self.assertEqual(self.git(self.runner / ".sources/source", "rev-parse", "HEAD"),
                         self.git(self.source, "rev-parse", "HEAD"))

    def test_unchanged_feed_does_not_create_a_commit(self):
        before = self.git(self.remote, "rev-parse", "main")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No document changes.", result.stdout)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

    def test_push_race_refreshes_sources_and_preserves_concurrent_commit(self):
        self.update_source("Intermediate article")
        self.install_race_hook(update_source=True)

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "push-attempts").read_text(), "2")
        self.git(self.remote, "merge-base", "--is-ancestor",
                 self.git(self.writer, "rev-parse", "HEAD"), "main")
        self.assertEqual(self.git(self.remote, "show", "main:README.md"), "Concurrent change 1")
        self.assertIn("Latest during push", self.git(self.remote, "show", "main:releases.atom"))
        self.assertNotIn("Intermediate article", self.git(self.remote, "show", "main:releases.atom"))
        self.assertEqual(self.git(self.remote, "show", "--format=", "--name-only", "main"),
                         "releases.atom")

    def test_unrelated_push_failure_is_not_retried(self):
        self.update_source("Unpublished article")
        before = self.git(self.remote, "rev-parse", "main")
        hook = self.remote / "hooks/pre-receive"
        hook.write_text(f"#!{sys.executable}\n" + textwrap.dedent(f"""\
            from pathlib import Path
            import sys
            marker = Path({str(self.root / 'rejected-pushes')!r})
            with marker.open("a") as output:
                output.write("rejected\\n")
            print("Publication denied by remote policy", file=sys.stderr)
            sys.exit(1)
            """))
        hook.chmod(0o755)

        result = self.publish()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Publication denied by remote policy", result.stderr)
        self.assertEqual((self.root / "rejected-pushes").read_text(), "rejected\n")
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), before)

    def test_repeated_push_races_stop_after_three_attempts(self):
        self.update_source("Unpublished article")
        original = self.git(self.remote, "show", "main:releases.atom")
        self.install_race_hook(every_push=True)

        result = self.publish()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / "push-attempts").read_text(), "3")
        self.assertEqual(self.git(self.remote, "show", "main:releases.atom"), original)
        self.assertEqual(self.git(self.remote, "show", "main:README.md"), "Concurrent change 3")
        self.assertIn("3 attempts", result.stderr)


if __name__ == "__main__":
    unittest.main()
