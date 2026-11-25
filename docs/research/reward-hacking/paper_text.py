"""Fetch the full text of arXiv papers as plain text for reading and searching.

Usage: python3 paper_text.py <directory> <arXiv id> [<arXiv id> ...]

Writes <directory>/<id>.txt from https://arxiv.org/html/<id>. Numbers inside formulas are kept (the LaTeX of
each formula), and so is text inside drawn boxes, where papers often put their prompts; the reference list
is dropped. Lines are wrapped so that the file can be read in parts.
"""

from __future__ import annotations

import html.parser
import pathlib
import re
import sys
import textwrap
import urllib.request

SKIP = ("script", "style", "nav", "header", "footer", "math")
BREAK = ("p", "li", "tr", "figcaption", "div", "section", "table", "br")


class Text(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "math" and not self.skip:
            self.out.append(f" {dict(attrs).get('alttext') or ''} ")
        if tag in SKIP:
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n# ")
        elif tag in BREAK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)


def convert(page: str) -> str:
    parser = Text()
    parser.feed(page)
    lines, skipping = [], False
    for raw in re.sub(r"[ \t ]+", " ", "".join(parser.out)).split("\n"):
        line = raw.strip()
        if re.match(r"^#\s*References\b", line):
            skipping = True
            lines.append("# References (dropped)")
        elif skipping and line.startswith("#"):
            skipping = False
        if line and not skipping:
            lines += textwrap.wrap(line, 230)
    text = "\n".join(lines)
    return text[max(text.find("# Abstract"), 0):]


def main() -> None:
    target = pathlib.Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    for paper in sys.argv[2:]:
        address = f"https://arxiv.org/html/{paper}"
        request = urllib.request.Request(address, headers={"User-Agent": "research-script/1.0"})
        with urllib.request.urlopen(request, timeout=90) as response:
            text = convert(response.read().decode("utf-8", errors="replace"))
        (target / f"{paper}.txt").write_text(text, encoding="utf-8")
        print(paper, len(text), "characters")


if __name__ == "__main__":
    main()
