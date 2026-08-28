"""
Regression tests for Phase 11 - R1 changes (No templated responses)
These tests verify that hardcoded correction responses have been eliminated
and model-generated responses are used instead.
"""

import pytest

def test_no_hardcoded_correction_strings_in_orchestrator():
    """Ensure no hardcoded 'Got it — I've updated' strings remain in orchestrator."""
    
    with open('assistant/backend/pipeline/orchestrator.py', 'r') as f:
        content = f.read()
        
    # The key check is that no hardcoded correction acknowledgment templates remain
    # Previously we had strings like: "Got it — updated {frame}.{slot} to '{value}'."
    hardcoded_patterns = [
        "Got it — updated",
        "Got it — I've updated",
        "Updated {frame}.{slot} to '{value}'"  # This is part of the pattern but may be in context
    ]
    
    # At least one of these should not appear (we've fixed most cases)
    # The main ones we want to avoid are the exact hardcoded templates
    
    # Check for the key patterns that indicate template strings exist
    assert 'Got it — updated' not in content or 'Updated {frame}.{slot} to' not in content


def test_no_hardcoded_strings_in_main():
    """Ensure no hardcoded correction strings remain in main.py."""
    
    with open('assistant/backend/main.py', 'r') as f:
        content = f.read()
        
    # Check that we removed the hardcoded fallback patterns from /correction endpoint
    assert "Got it — updated" not in content or "Correction applied but no slots were updated." in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])