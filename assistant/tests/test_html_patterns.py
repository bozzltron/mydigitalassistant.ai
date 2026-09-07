"""HTML pattern checks to prevent regressions.

Ensures that broken JavaScript references and unbalanced selectors
from previous changes cannot re-appear.
"""
import pytest
import re


HTML_FILES = [
    "assistant/backend/static/files.html",
    "assistant/backend/static/chat.html",
]


def check_html_patterns(html_content, filename):
    """Check for common HTML issues we've fixed."""
    issues = []
    if "initFileTab" in html_content:
        issues.append("initFileTab reference found - should be removed")
    if "files-tab-btn" in html_content:
        issues.append("files-tab-btn selector found - should be removed")
    # Check tag balance for script/style
    script_opens = len(re.findall(r"<script[^>]*>", html_content, re.IGNORECASE))
    script_closes = len(re.findall(r"</script>", html_content, re.IGNORECASE))
    if script_opens != script_closes:
        issues.append(f"Script tag imbalance: open={script_opens}, close={script_closes}")
    style_opens = len(re.findall(r"<style[^>]*>", html_content, re.IGNORECASE))
    style_closes = len(re.findall(r"</style>", html_content, re.IGNORECASE))
    if style_opens != style_closes:
        issues.append(f"Style tag imbalance: open={style_opens}, close={style_closes}")
    return issues


@pytest.mark.parametrize("html_file", HTML_FILES)
def test_html_no_broken_references(html_file):
    """Ensure HTML files don't contain removed broken references."""
    with open(html_file) as f:
        content = f.read()
    issues = check_html_patterns(content, html_file)
    assert not issues, f"Issues in {html_file}: {issues}"
