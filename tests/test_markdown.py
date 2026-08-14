"""Writer-facing Markdown rendering and safety regressions."""

from gov_webui.markdown import render_writer_markdown


def test_renders_common_writing_markdown() -> None:
    rendered = render_writer_markdown(
        "## Shuffle All\n\nIt is *almost* time.\n\n- bootlegs\n- imports\n"
    )

    assert "<h2>Shuffle All</h2>" in rendered
    assert "It is <em>almost</em> time." in rendered
    assert "<ul>" in rendered
    assert "<li>bootlegs</li>" in rendered


def test_keeps_generated_html_links_and_images_inert() -> None:
    rendered = render_writer_markdown(
        "<script>alert('no')</script>\n\n"
        "[bad](javascript:alert('no'))\n\n"
        "![tracker](https://example.test/pixel.gif)"
    )

    assert "<script" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "javascript:" in rendered
    assert 'href="javascript:' not in rendered
    assert "<img" not in rendered
