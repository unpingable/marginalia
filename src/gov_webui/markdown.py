# SPDX-License-Identifier: Apache-2.0
"""Safe, deliberately ordinary Markdown for Marginalia's writing surface."""

from __future__ import annotations

from markdown_it import MarkdownIt


_WRITER_MARKDOWN = MarkdownIt(
    "commonmark",
    {
        "html": False,
        "breaks": True,
        "linkify": False,
        "typographer": False,
    },
).disable("image")


def render_writer_markdown(content: str) -> str:
    """Render CommonMark while keeping model-supplied HTML inert.

    Images are deliberately disabled so a response cannot make the browser
    fetch a model-selected remote resource merely by being displayed.
    """
    return _WRITER_MARKDOWN.render(content)
