import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_feed import generate

ROOT = Path(__file__).resolve().parents[1]
NS = {"a": "http://www.w3.org/2005/Atom"}


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT / "feed.json").read_text())
        for name in ("daily-rss", "blogpost", "daily-investor"):
            repo = self.root / name
            repo.mkdir()
            self.run_git(repo, "init", "-b", "main")
            self.run_git(repo, "config", "user.name", "Feed test")
            self.run_git(repo, "config", "user.email", "feed@example.com")
        self.commit("daily-rss", {
            "2026/10/2026-10-01.md": "# 日报 & 观察\n\n日报正文。",
            "README.md": "# Not an article",
            ".github/workflows/notes.md": "# Not an article",
        }, "2026-10-01T06:00:00+08:00")
        self.commit("blogpost", {
            "文章 空格.md": "# 技术 **文章**\n\n[同目录](other.md) ![图片](images/图.png)\n\n"
                            "| A | B |\n| - | - |\n| 1 | 2 |\n\n"
                            '<script>alert(1)</script><img src="x" onerror="alert(1)">'
                            '<a href="javascript:alert(1)">bad</a>\n',
            "tests/fixtures/fidelity/chinese.md": "# Fixture",
            "docs/translation-fidelity-pilot.md": "# Maintenance",
            "NEW-REPOS-CREATED.md": "# Maintenance",
        }, "2026-10-02T08:00:00+08:00")
        self.commit("daily-investor", {
            "ideas/2026-10-03.md": "# AI 产品创意\n\nIdeas only.",
            "observations/report.md": "# Excluded observation",
            "ideas-old/report.md": "# Excluded sibling",
            "ideas/README.md": "# Excluded index",
        }, "2026-10-03T07:00:00+08:00")

    def run_git(self, repo, *args, env=None):
        return subprocess.check_output(["git", "-C", str(repo), *args],
                                       env=env, stderr=subprocess.PIPE, timeout=10)

    def commit(self, name, files, date):
        repo = self.root / name
        for path, body in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if body is None:
                target.unlink()
            else:
                target.write_text(body, encoding="utf-8")
        self.run_git(repo, "add", ".")
        env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        self.run_git(repo, "commit", "-m", "Fixture document update", env=env)

    def entries(self, config=None):
        output, counts = generate(config or self.config, self.root)
        return ET.fromstring(output).findall("a:entry", NS), output, counts

    def test_sources_scope_and_deterministic_xml(self):
        entries, output, counts = self.entries()
        self.assertEqual(len(entries), 3)
        self.assertEqual(set(counts), {"kejun/daily-rss", "kejun/blogpost", "kejun/daily-investor"})
        self.assertEqual(output, self.entries()[1])
        titles = [e.findtext("a:title", namespaces=NS) for e in entries]
        self.assertEqual(titles, ["[AI 产品创意] AI 产品创意", "[技术文章] 技术 文章", "[技术日报] 日报 & 观察"])
        self.assertEqual(len(set(e.findtext("a:id", namespaces=NS) for e in entries)), 3)
        self.assertNotIn("Excluded", output.decode())
        self.assertNotIn("Maintenance", output.decode())

    def test_edit_keeps_id_and_published_but_updates_content_and_order(self):
        initial, _, _ = self.entries()
        old = initial[-1]
        self.commit("daily-rss", {"2026/10/2026-10-01.md": "# 修订日报\n\n新增正文。"},
                    "2026-10-04T06:00:00+08:00")
        entries, _, _ = self.entries()
        new = entries[0]
        for field in ("id", "published"):
            self.assertEqual(old.findtext(f"a:{field}", namespaces=NS), new.findtext(f"a:{field}", namespaces=NS))
        self.assertEqual(new.findtext("a:updated", namespaces=NS), "2026-10-03T22:00:00Z")
        self.assertIn("新增正文", new.findtext("a:content", namespaces=NS))

    def test_html_links_images_tables_and_unsafe_attributes(self):
        entries, _, _ = self.entries()
        entry = entries[1]
        self.assertIn("%E6%96%87%E7%AB%A0%20", entry.findtext("a:id", namespaces=NS))
        content = entry.findtext("a:content", namespaces=NS)
        self.assertIn("https://github.com/kejun/blogpost/blob/main/other.md", content)
        self.assertIn("https://raw.githubusercontent.com/kejun/blogpost/main/images/%E5%9B%BE.png", content)
        self.assertIn("<table>", content)
        self.assertNotIn("<script", content)
        self.assertNotIn("onerror=", content)
        self.assertNotIn("javascript:", content)

    def test_excluded_changes_do_not_change_feed(self):
        before = self.entries()[1]
        self.commit("blogpost", {"README.md": "# Updated index"}, "2026-10-06T08:00:00+08:00")
        self.commit("daily-investor", {"observations/report.md": "# New observation"},
                    "2026-10-06T09:00:00+08:00")
        self.assertEqual(self.entries()[1], before)

    def test_limit_and_deleted_document(self):
        config = copy.deepcopy(self.config)
        config["max_entries"] = 2
        entries, _, _ = self.entries(config)
        self.assertEqual(len(entries), 2)
        self.commit("blogpost", {"文章 空格.md": None, "replacement.md": "# Replacement"},
                    "2026-10-05T08:00:00+08:00")
        entries, _, _ = self.entries()
        self.assertIn("Replacement", entries[0].findtext("a:title", namespaces=NS))
        self.assertFalse(any("%E6%96%87" in e.findtext("a:id", namespaces=NS) for e in entries))

    def test_failed_source_preserves_existing_output(self):
        output = self.root / "existing.atom"
        output.write_bytes(self.entries()[1])
        previous = output.read_bytes()
        (self.root / "blogpost").rename(self.root / "unavailable-blogpost")
        result = subprocess.run([
            sys.executable, str(ROOT / "scripts/generate_feed.py"),
            "--config", str(ROOT / "feed.json"), "--sources-dir", str(self.root),
            "--output", str(output),
        ], capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), previous)

    def test_shallow_history_and_dirty_sources_are_rejected(self):
        repo = self.root / "blogpost"
        path = repo / "文章 空格.md"
        original = path.read_text()
        path.write_text(original + "Uncommitted")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            self.entries()
        path.write_text(original)
        (repo / ".git/shallow").write_bytes(self.run_git(repo, "rev-parse", "HEAD"))
        with self.assertRaisesRegex(ValueError, "full history"):
            self.entries()


if __name__ == "__main__":
    unittest.main()
