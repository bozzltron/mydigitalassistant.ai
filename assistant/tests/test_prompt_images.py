"""The inline-image candidates offered to the model in the search prompt.

The hero used to be chosen by position — the first result's og:image — which is
why it often showed site chrome. The model now gets the candidate list and places
an image itself, so what matters here is that the list is deduped, capped, and
explicitly closed ("only these URLs"), because an invented image URL is a broken
image and a wasted fetch through the proxy.
"""

from assistant.backend.pipeline.orchestrator import _format_image_candidates
from assistant.backend.pipeline.search import SearchInfo, SearchResult


def _result(title: str, url: str, **kw) -> SearchResult:
    return SearchResult(title=title, url=url, snippet="", engine="brave", **kw)


def _info(*, results: list[SearchResult], images: list[SearchResult] | None = None):
    return SearchInfo(backend="brave", query="q", results=results, image_results=images)


def test_image_index_candidates_come_first():
    block = _format_image_candidates(
        [_result("Page", "https://page", image="https://page/full.jpg")],
        _info(results=[], images=[_result("Pic", "https://src", thumbnail="https://cdn/500.jpg")]),
    )
    assert block.index("https://cdn/500.jpg") < block.index("https://page/full.jpg")


def test_image_index_offers_the_reliable_500px_copy_not_the_source():
    """The source (`properties.url`) is sometimes hotlink-blocked; the CDN copy is not."""
    block = _format_image_candidates(
        [],
        _info(
            results=[],
            images=[
                _result(
                    "Pic",
                    "https://src",
                    thumbnail="https://cdn/500.jpg",
                    image="https://src/full.jpg",
                )
            ],
        ),
    )
    assert "https://cdn/500.jpg" in block
    assert "https://src/full.jpg" not in block


def test_web_result_offers_its_full_image_over_the_200px_preview():
    block = _format_image_candidates(
        [_result("Page", "https://page", thumbnail="https://page/200.jpg",
                 image="https://page/1600.jpg")],
        _info(results=[]),
    )
    assert "https://page/1600.jpg" in block
    assert "https://page/200.jpg" not in block


def test_duplicates_are_dropped_and_the_list_is_capped():
    same = "https://cdn/one.jpg"
    results = [_result(f"P{i}", f"https://p{i}", image=same) for i in range(10)]
    block = _format_image_candidates(results, _info(results=results), limit=4)
    assert block.count(same) == 1
    assert block.count("\n") < 12


def test_no_images_adds_nothing_to_the_prompt():
    assert _format_image_candidates([_result("Page", "https://page")], _info(results=[])) == ""


def test_the_instruction_closes_the_set_and_forbids_invention():
    block = _format_image_candidates(
        [_result("Page", "https://page", image="https://page/a.jpg")],
        _info(results=[]),
    )
    assert "never join, rebuild or guess" in block
    assert "at most two" in block
    assert "not collect images at the end" in block
    # The model cannot see the image, so it must not describe it: a live turn
    # labelled NASA's stock photo "An Anticrepuscular Rainbow Fan".
    assert "cannot see it" in block
    assert "never a description of what the image shows" in block


def test_site_furniture_is_not_offered_as_a_candidate():
    """Measured candidates: an Adobe stock logo and a Facebook default card."""
    block = _format_image_candidates(
        [
            _result("Stock", "https://stock.adobe", image="https://stock.adobe/x/stock_logo_2020.png"),
            _result("Health", "https://emedicine", image="https://img/fb-emh-default.jpg"),
            _result("Real", "https://page", image="https://page/brain-picture.jpg"),
        ],
        _info(results=[]),
    )
    assert "stock_logo_2020" not in block
    assert "fb-emh-default" not in block
    assert "brain-picture.jpg" in block


def test_a_joined_url_is_never_offered():
    """`https://site.comhttps://cdn.site.com/x.png` is not a URL; it 400s."""
    block = _format_image_candidates(
        [
            _result(
                "MRI",
                "https://www.imaios.com/en/e-anatomy/brain/mri-brain",
                image="https://www.imaios.comhttps://cdn1.imaios.com/i/images/x.png",
            )
        ],
        _info(results=[]),
    )
    assert block == ""
