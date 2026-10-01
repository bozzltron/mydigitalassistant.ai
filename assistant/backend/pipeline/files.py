"""File processing pipeline for the cognitive assistant.

Handles extraction of content from various file types (.txt, .csv, .json, .xml, .html)
and integrates with the memory system.
"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FileExtractionResult:
    """Result of file content extraction."""
    text: str  # Plain text extracted from the file
    key_entities: list[str]  # Key entities/concepts found
    open_questions: list[str]  # Potential open questions from the content
    structure_info: dict  # Information about the file structure
    row_data: list[dict] | None = None  # Parsed CSV rows: [{col: value}, ...]


def extract_text_from_txt(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .txt file."""
    try:
        plain_text = content.decode("utf-8", errors="replace")
        return plain_text, [], []
    except Exception:
        return "", [], []


def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str], list[dict] | None]:
    """Extract text from a .csv file.

    Returns: (plain_text, key_entities, open_questions, row_data)
    row_data: List of dicts, one per row {column: value}, or None if not a CSV
    """
    import csv
    from io import StringIO

    try:
        text = content.decode("utf-8", errors="replace")
        lines = [line for line in text.strip().split("\n") if line.strip()]

        if not lines:
            return "", [], [], None

        # Parse using csv module for proper handling of quoted fields
        reader = csv.reader(StringIO(text))
        all_rows = list(reader)

        if not all_rows:
            return "", [], [], None

        headers = [h.strip() for h in all_rows[0]]
        row_data = []

        key_entities = []
        open_questions = []

        for row_vals in all_rows[1:]:
            # Pad row if fewer columns than headers
            row_vals = list(row_vals) + [""] * (len(headers) - len(row_vals))
            row = {headers[i]: row_vals[i].strip() for i in range(len(headers))}
            row_data.append(row)

            # Extract entities
            for header, value in row.items():
                if value and value not in ("NA", "N/A", "", "null", "None"):
                    entity_key = f"{header}_{value}"
                    if entity_key not in key_entities:
                        key_entities.append(entity_key)

        open_questions = []
        if row_data:
            open_questions.append(f"Analyze {len(row_data)} rows for patterns and insights")
            # Detect potential ID columns
            for h in headers:
                if h.lower() in ('id', 'uuid', 'key', 'pk'):
                    open_questions.append(f"Column '{h}' appears to be an identifier")

        # Plain text representation
        plain_text = "\n".join(",".join(r) for r in all_rows)
        return plain_text, key_entities, open_questions, row_data

    except Exception as e:
        logger.warning(f"CSV extraction failed: {e}")
        return "", [], [], None


def extract_text_from_json(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .json file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        data = json.loads(content.decode("utf-8", errors="replace"))

        key_entities = []
        open_questions = []

        # Extract key entities from JSON structure
        def extract_entities(obj, prefix=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_prefix = f"{prefix}.{key}" if prefix else key
                    if isinstance(value, (str, int, float)) and value:
                        entity_key = f"{prefix}_{value}"
                        if entity_key not in key_entities:
                            key_entities.append(entity_key)
                    elif isinstance(value, (dict, list)):
                        extract_entities(value, new_prefix)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_entities(item, f"{prefix}[{i}]")

        extract_entities(data)

        # Open questions (simple heuristics)
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], str) and len(data[key]) > 500:
                    open_questions.append(f"Analyze detailed content for key: {key}")

        # Plain text representation
        plain_text = json.dumps(data, indent=2)[:5000]
        return plain_text, key_entities, open_questions
    except Exception:
        return ""


def extract_text_from_xml(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .xml file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        import xml.etree.ElementTree as ET

        tree = ET.fromstring(content.decode("utf-8", errors="replace"))

        key_entities = []
        open_questions = []

        # Extract tag names and attributes as entities
        def extract_entities_from_element(element, depth=0):
            tag = element.tag
            attrib = element.attrib

            # Add tag name as entity
            if tag:
                entity_key = f"tag_{tag}"
                if entity_key not in key_entities:
                    key_entities.append(entity_key)

            # Add attributes as entities
            for key, value in attrib.items():
                entity_key = f"attr_{key}_{value}"
                if entity_key not in key_entities:
                    key_entities.append(entity_key)

            # Recurse into children
            for child in element:
                extract_entities_from_element(child, depth + 1)

        extract_entities_from_element(tree)

        # Plain text - extract all text content
        plain_text = ET.tostring(tree, encoding="unicode", method="text")

        # Open questions
        if tree is not None:
            open_questions.append("Review XML structure and extract meaningful data")

        return plain_text, key_entities, open_questions
    except Exception:
        return ""


def extract_text_from_html(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .html file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        from html.parser import HTMLParser

        class HTMLTextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._text: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag in ("br", "hr", "p", "div", "li"):
                    self._text.append("\n")
                elif tag in ("a",):
                    self._text.append(" [LINK] ")

            def handle_data(self, data):
                if data.strip():
                    self._text.append(data.strip())

        extractor = HTMLTextExtractor()
        extractor.feed(content.decode("utf-8", errors="replace"))
        plain_text = " ".join(extractor._text)

        key_entities = []
        open_questions = ["Review HTML content for links, forms, and structured data"]

        return plain_text, key_entities, open_questions
    except Exception:
        return []


def extract_text_from_ics(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .ics (iCalendar) file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        import re

        text = content.decode("utf-8", errors="replace")

        # Extract VEVENT components
        events = re.findall(
            r"BEGIN:VEVENT(.*?)END:VEVENT",
            text,
            re.DOTALL,
        )

        event_summaries = []
        key_entities = []
        open_questions = []

        for event in events:
            # Extract summary/title
            summary_match = re.search(r"SUMMARY:(.+?)\n", event)
            summary = summary_match.group(1).strip() if summary_match else "Untitled event"

            # Extract date/time
            dtstart_match = re.search(r"DTSTART:(.+?)\n", event)
            dtend_match = re.search(r"DTEND:(.+?)\n", event)

            event_info = f"Event: {summary}"
            if dtstart_match:
                event_info += f" on {dtstart_match.group(1).strip()}"
            if dtend_match:
                event_info += f" to {dtend_match.group(1).strip()}"

            event_summaries.append(event_info)

            # Extract summary as key entity
            if summary and summary != "Untitled event":
                entity_key = f"event_{summary[:50]}"
                if entity_key not in key_entities:
                    key_entities.append(entity_key)

            # Extract open questions about the event
            open_questions.append(f"Review event: {summary[:50]}")

        # Plain text representation - first few events
        if event_summaries:
            plain_text = "\n\n".join(event_summaries[:5])
        else:
            plain_text = "No iCalendar events found"

        # If no events found, try to extract any meaningful text
        if not event_summaries:
            # Look for other common iCalendar components
            other_lines = [
                line for line in text.split("\n") 
                if not line.strip().startswith(("BEGIN:", "END:"))
            ]
            if other_lines:
                plain_text = " ".join(other_lines[:200])
            else:
                plain_text = "No iCalendar events found"

        return plain_text, key_entities, open_questions
    except Exception:
        return "Error parsing iCalendar file", [], []


async def extract_file_content(
    file_path: str,
    ext: str,
    content: bytes,
) -> FileExtractionResult:
    """Extract content from a file based on its extension.

    Args:
        file_path: Path to the file
        ext: File extension (lowercase)
        content: Raw file bytes

    Returns:
        FileExtractionResult with extracted text, entities, and questions
    """
    # Read file content once
    content_str = content.decode("utf-8", errors="replace")

    # Route to appropriate extractor
    extractors = {
        "txt": extract_text_from_txt,
        "csv": extract_text_from_csv,
        "json": extract_text_from_json,
        "xml": extract_text_from_xml,
        "html": extract_text_from_html,
        "ics": extract_text_from_ics,
    }

    extractor = extractors.get(ext)
    if not extractor:
        # Fallback: treat as plain text
        plain_text = content_str
        key_entities = []
        open_questions = ["Review file content"]
        row_data = None
    else:
        result = extractor(content)
        if len(result) == 4:
            plain_text, key_entities, open_questions, row_data = result
        else:
            plain_text, key_entities, open_questions = result
            row_data = None

    # Structure info
    structure_info = {
        "file_type": ext,
        "file_size": len(content),
        "content_length": len(plain_text),
    }

    return FileExtractionResult(
        text=plain_text,
        key_entities=key_entities,
        open_questions=open_questions,
        structure_info=structure_info,
        row_data=row_data,
    )
