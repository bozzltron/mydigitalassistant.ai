"""File processing pipeline for the cognitive assistant.

Handles extraction of content from various file types (.txt, .csv, .json, .xml, .html)
and integrates with the memory system.
"""

import json
from dataclasses import dataclass


@dataclass
class FileExtractionResult:
    """Result of file content extraction."""
    text: str  # Plain text extracted from the file
    key_entities: list[str]  # Key entities/concepts found
    open_questions: list[str]  # Potential open questions from the content
    structure_info: dict  # Information about the file structure

    # Additional: query-aware extraction
    def query_content(self, query: str) -> str:
        """Extract relevant content based on a natural language query."""
        query_lower = query.lower()
        text_lower = self.text.lower()
        
        # Simple keyword-based extraction
        if query_lower in text_lower:
            # Find the sentence containing the query
            sentences = self.text.split('.')
            for sentence in sentences:
                if query_lower in sentence.lower():
                    return sentence.strip() + '.'
            
            # Return first 200 chars if no match
            return self.text[:200]
        
        return self.text


def extract_text_from_txt(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .txt file."""
    try:
        plain_text = content.decode("utf-8", errors="replace")
        return plain_text, [], []
    except Exception:
        return "", [], []


def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .csv file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        text = content.decode("utf-8", errors="replace")
        lines = text.strip().split("\n")

        key_entities = []
        open_questions = []

        # Parse header row if present
        if lines:
            headers = [h.strip() for h in lines[0].split(",")]

            # Extract entities from data rows
            for line in lines[1:]:
                values = [v.strip() for v in line.split(",")]
                for _i, (header, value) in enumerate(zip(headers, values, strict=True)):
                    if value and value not in ("NA", "N/A", "", "null"):
                        entity_key = f"{header}_{value}"
                        if entity_key not in key_entities:
                            key_entities.append(entity_key)

            # Simple open question detection
            if len(lines) > 1:
                open_questions.append("Review data for patterns and insights")

        # Plain text representation
        plain_text = "\n".join(lines)
        return plain_text, key_entities, open_questions
    except Exception:
        return ""


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
    }

    extractor = extractors.get(ext)
    if not extractor:
        # Fallback: treat as plain text
        plain_text = content_str
        key_entities = []
        open_questions = ["Review file content"]
    else:
        plain_text, key_entities, open_questions = extractor(content)

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
    )

def generate_file(content: str, file_type: str, query: str = None) -> str:
    """Generate a file of the specified type with optional query-based filtering.
    
    Args:
        content: The content to write
        file_type: Target file type (.txt, .csv, .json, .xml, .html)
        query: Optional query to filter/relevant content
    
    Returns:
        Path to the generated file
    """
    import tempfile
    from pathlib import Path
    
    import os
    filename = "generated" + "." + file_type.lstrip(".")
    output_path = Path(f"/tmp/{{filename}}")
    with open(output_path, "w") as f:
        f.write(content)
    
    return str(output_path)

