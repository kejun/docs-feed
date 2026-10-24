#!/usr/bin/env python3
"""Build a deterministic Atom feed from complete, read-only source checkouts."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import quote, urljoin
import xml.etree.ElementTree as ET

import bleach
import markdown

ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM)
TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code",
    "img", "table", "thead", "tbody", "tr", "th", "td", "del", "s",
    "sup", "sub", "div", "span", "dl", "dt", "dd", "details", "summary",
}
ATTRS = {"*": ["id"], "a": ["href", "title"], "img": ["src", "alt", "title"],
         "th": ["colspan", "rowspan", "align"], "td": ["colspan", "rowspan", "align"]}


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], timeout=90
    ).decode("utf-8")


def xml_text(text):
    # XML 1.0 excludes control characters even when escaped.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def eligible(path, source, config):
    parts = PurePosixPath(path).parts
    return (
        PurePosixPath(path).suffix.lower() in {".md", ".markdown"}
        and path.startswith(source.get("prefix", ""))
        and not any(part.startswith(".") for part in parts)
        and not any(fnmatchcase(path, pattern) for pattern in
                    config.get("exclude", []) + source.get("exclude", []))
    )


def collect_documents(source, config, source_root):
    repo = source_root / source["directory"]
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise ValueError(f"{repo}: full history required (fetch-depth: 0)")
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError(f"{repo}: source checkout has uncommitted changes")
    tracked = git(repo, "ls-files", "--stage", "-z").split("\0")
    paths = set()
    for record in filter(None, tracked):
        meta, path = record.split("\t", 1)
        if meta.split()[0] in {"100644", "100755"} and eligible(path, source, config):
            paths.add(path)
    if not paths:
        raise ValueError(f"{source['repository']}: no matching documents")
    dates = {}
    history = git(repo, "log", "--format=%x1e%ct", "--name-only", "-z",
                  "--no-renames", "--diff-filter=AMT", "HEAD")
    for record in history.split("\x1e")[1:]:
        date, *changed = record.split("\0")
        seconds = int(date.strip())
        for path in changed:
            path = path.lstrip("\n")
            if path in paths:
                first, last = dates.get(path, (seconds, seconds))
                dates[path] = (min(first, seconds), max(last, seconds))
    documents = []
    for path in sorted(paths):
        if path not in dates:
            raise ValueError(f"Missing commit history for {repo}/{path}")
        published, updated = dates[path]
        documents.append({"source": source, "repo": repo, "path": path,
                          "published": published, "updated": updated})
    return documents


class FeedHTML(HTMLParser):
    """Resolve article links/images and collect a readable heading and summary."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        source, path = document["source"], quote(document["path"], safe="/")
        self.link_base = f"https://github.com/{source['repository']}/blob/main/{path}"
        self.image_base = f"https://raw.githubusercontent.com/{source['repository']}/main/{path}"
        self.html, self.text, self.heading = [], [], []
        self.in_heading = False
        self.found_heading = False

    def handle_starttag(self, tag, attrs):
        if tag == "h1" and not self.found_heading:
            self.in_heading = True
            self.found_heading = True
        result = []
        for name, value in attrs:
            if value is None:
                continue
            if name in {"href", "src"}:
                value = quote(urljoin(self.image_base if name == "src" else self.link_base, value),
                              safe="/:?#[]@!$&'()*+,;=%")
            result.append(f' {name}="{escape(value, quote=True)}"')
        self.html.append(f"<{tag}{''.join(result)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_heading = False
        self.html.append(f"</{tag}>")
        if tag in {"p", "li", "h1", "h2", "h3", "tr"}:
            self.text.append(" ")

    def handle_data(self, data):
        self.html.append(escape(data))
        self.text.append(data)
        if self.in_heading:
            self.heading.append(data)


def render_document(document):
    body = (document["repo"] / document["path"]).read_text(encoding="utf-8-sig")
    body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", body, count=1, flags=re.S)
    html = markdown.markdown(xml_text(body), extensions=["tables", "fenced_code", "sane_lists"])
    html = bleach.clean(html, tags=TAGS, attributes=ATTRS, protocols={"http", "https", "mailto"}, strip=True)
    parsed = FeedHTML(document)
    parsed.feed(html)
    parsed.close()
    title = " ".join("".join(parsed.heading).split()) or PurePosixPath(document["path"]).stem
    plain = " ".join("".join(parsed.text).split())
    summary = plain[:300] + ("…" if len(plain) > 300 else "")
    return title, summary, "".join(parsed.html)


def element(parent, name, text=None, **attrs):
    node = ET.SubElement(parent, f"{{{ATOM}}}{name}", attrs)
    if text is not None:
        node.text = xml_text(text)
    return node


def generate(config, source_root):
    documents = []
    for source in config["sources"]:
        documents.extend(collect_documents(source, config, source_root))
    limit = config["max_entries"]
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("max_entries must be a positive integer")
    documents.sort(key=lambda d: (-d["updated"], d["source"]["repository"], d["path"]))
    documents = documents[:limit]
    if not documents:
        raise ValueError("Cannot publish an empty feed")
    feed = ET.Element(f"{{{ATOM}}}feed", {"{http://www.w3.org/XML/1998/namespace}lang": "zh-CN"})
    element(feed, "id", config["home_url"])
    element(feed, "title", config["title"])
    element(feed, "subtitle", config["subtitle"])
    element(feed, "link", href=config["feed_url"], rel="self", type="application/atom+xml")
    element(feed, "link", href=config["home_url"], rel="alternate", type="text/html")
    element(feed, "updated", timestamp(documents[0]["updated"]))
    author = element(feed, "author")
    element(author, "name", config["author"])
    element(author, "uri", f"https://github.com/{config['author']}")
    for document in documents:
        source, path = document["source"], quote(document["path"], safe="/")
        url = f"https://github.com/{source['repository']}/blob/main/{path}"
        title, summary, html = render_document(document)
        entry = element(feed, "entry")
        element(entry, "id", url)
        element(entry, "title", f"[{source['label']}] {title}")
        element(entry, "link", href=url, rel="alternate", type="text/html")
        element(entry, "published", timestamp(document["published"]))
        element(entry, "updated", timestamp(document["updated"]))
        element(entry, "category", term=source["repository"], label=source["label"])
        element(entry, "summary", summary, type="text")
        element(entry, "content", html, type="html")
    ET.indent(feed, space="  ")
    output = ET.tostring(feed, encoding="utf-8", xml_declaration=True) + b"\n"
    ET.fromstring(output)
    counts = Counter(d["source"]["repository"] for d in documents)
    return output, dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("feed.json"))
    parser.add_argument("--sources-dir", type=Path, default=Path(".sources"))
    parser.add_argument("--output", type=Path, default=Path("releases.atom"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    content, counts = generate(config, args.sources_dir)
    if args.output.exists() and args.output.read_bytes() == content:
        print(f"Unchanged: {counts}")
        return
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(args.output)
    print(f"Generated {args.output}: {counts}; {len(content):,} bytes")


if __name__ == "__main__":
    main()
