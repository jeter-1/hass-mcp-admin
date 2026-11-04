"""HAMCP-303: the release index and docs map cannot silently rot."""

import re
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = DOCS / "RELEASES.md"
DOCS_MAP = DOCS / "README.md"
RELEASE_DOC = re.compile(r".+_(?:RELEASE_NOTES|ACCEPTANCE)\.md")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
CODE_SPAN = re.compile(r"(`+)[^`]*?\1")
LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)
VERSION = re.compile(r"\d+\.\d+\.\d+")


def broken_links(path):
    """Relative Markdown links in path whose targets do not exist."""
    text = CODE_SPAN.sub("", FENCE.sub("", path.read_text(encoding="utf-8")))
    broken = []
    for target in LINK.findall(text):
        if EXTERNAL.match(target):
            continue
        relative = target.split("#", 1)[0].split("?", 1)[0]
        if relative and not (path.parent / relative).exists():
            broken.append(target)
    return broken


def unindexed_release_docs(docs, index_text):
    """Flat release notes and acceptance records that the index does not link."""
    return sorted(
        p.name for p in docs.glob("*.md")
        if RELEASE_DOC.fullmatch(p.name) and f"]({p.name})" not in index_text
    )


def first_heading(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:]
    return ""


class ReleaseIndexTests(unittest.TestCase):
    def test_every_release_document_is_indexed(self):
        self.assertEqual(unindexed_release_docs(DOCS, INDEX.read_text(encoding="utf-8")), [])

    def test_current_version_heads_the_index_and_the_readme(self):
        version = yaml.safe_load(
            (ROOT / "hass_mcp_engineering_beta" / "config.yaml").read_text(encoding="utf-8"))["version"]
        rows = [line for line in INDEX.read_text(encoding="utf-8").split("## Releases", 1)[1].splitlines()
                if line.startswith("| ") and not line.startswith("| Version") and not line.startswith("| ---")]
        self.assertTrue(rows[0].startswith(f"| {version} |"), rows[0])
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        current = readme.split("## Current release", 1)[1].split("\n## ", 1)[0]
        self.assertIn(f"**{version}**", current)

    def test_index_and_map_cannot_be_resolved_as_release_authority(self):
        for path in (INDEX, DOCS_MAP):
            self.assertIsNone(re.fullmatch(r"RC.*_.*\.md", path.name))
            self.assertIsNone(re.fullmatch(r"V\d+_\d+_\d+_RC\d+_.*\.md", path.name))
            self.assertIsNone(RELEASE_DOC.fullmatch(path.name))
            self.assertIsNone(VERSION.search(first_heading(path)), path.name)

    def test_unindexed_release_document_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            for name in ("V9_9_9_BETA1_RELEASE_NOTES.md", "V9_9_9_BETA1_ACCEPTANCE.md", "GUIDE.md"):
                (docs / name).write_text("# synthetic\n", encoding="utf-8")
            index = "[`V9_9_9_BETA1_RELEASE_NOTES.md`](V9_9_9_BETA1_RELEASE_NOTES.md)\n"
            self.assertEqual(unindexed_release_docs(docs, index), ["V9_9_9_BETA1_ACCEPTANCE.md"])


class RelativeLinkTests(unittest.TestCase):
    def test_no_relative_link_is_broken(self):
        paths = [ROOT / "README.md", ROOT / "hass_mcp_engineering_beta" / "README.md",
                 *sorted(DOCS.rglob("*.md"))]
        broken = {str(p.relative_to(ROOT)): links for p in paths if (links := broken_links(p))}
        self.assertEqual(broken, {})

    def test_checker_reports_broken_links_and_ignores_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "present.md").write_text("# present\n", encoding="utf-8")
            doc = root / "doc.md"
            doc.write_text(
                "[ok](present.md#anchor) [gone](missing.md) [web](https://example.invalid/x.md)\n"
                "[self](#top) `[span](span-missing.md)`\n"
                "```\n[fenced](fence-missing.md)\n```\n",
                encoding="utf-8")
            self.assertEqual(broken_links(doc), ["missing.md"])


if __name__ == "__main__":
    unittest.main()
