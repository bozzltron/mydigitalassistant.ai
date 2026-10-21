"""The `.html` writer: a document the model wrote in full, a fragment, or prose.

A bare fragment on disk is renderable but incomplete: no charset (accented text
mojibakes when the file is opened from disk) and no title (the tab and the
download show the filename). Prose is worse — "5 < 10" parses as a tag and
swallows the rest of the line. The writer therefore distinguishes three shapes,
and the document case is kept verbatim because the model may have set its own
lang, charset and styles.
"""

from assistant.backend.pipeline.files import extract_file_content, render_file_bytes

_DOCUMENT = (
    '<!DOCTYPE html>\n<html lang="fr"><head><meta charset="utf-8">'
    "<title>Rapport</title></head><body><h1>Résumé</h1></body></html>"
)


def test_full_document_is_written_verbatim():
    data, err = render_file_bytes("html", _DOCUMENT)
    assert err is None
    assert data.decode("utf-8") == _DOCUMENT


def test_fragment_is_wrapped_in_a_real_document():
    data, err = render_file_bytes("html", "<h1>Morning</h1><p>Two things.</p>")
    assert err is None
    html = data.decode("utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in html
    assert "<title>Morning</title>" in html
    assert "<h1>Morning</h1><p>Two things.</p>" in html


def test_prose_is_escaped_not_parsed_as_markup():
    data, err = render_file_bytes("html", "5 < 10 and 7 > 3")
    assert err is None
    assert "<p>5 &lt; 10 and 7 &gt; 3</p>" in data.decode("utf-8")


async def test_wrapped_fragment_still_extracts_for_memory():
    """The wrapper must not hide the content from the extractor.

    Memory holds what a file IS, but the text still has to be retrievable; a
    wrapper that put the body somewhere the HTML extractor ignores would leave
    the report unreadable to `read_file`.
    """
    data, _ = render_file_bytes("html", "<h1>Morning</h1><p>Two things.</p>")
    result = await extract_file_content("report.html", "html", data)
    assert "Morning" in result.text
    assert "Two things." in result.text
