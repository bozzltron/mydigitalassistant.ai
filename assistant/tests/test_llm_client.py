from assistant.backend.pipeline.llm_client import build_system_prompt


def test_build_system_prompt_functional():
    prompt = build_system_prompt("frame: guitar, slot: strings=6", "functional")
    assert "structured memory system" in prompt
    assert "guitar" in prompt
    assert "ground" not in prompt.lower() or "introspective" not in prompt.lower()


def test_build_system_prompt_introspective():
    prompt = build_system_prompt("frame: guitar", "introspective")
    assert (
        "ground" in prompt.lower()
        or "honest" in prompt.lower()
        or "do not hallucinate" in prompt.lower()
    )
    assert "guitar" in prompt
    # Introspective prompt should be more constrained
    assert "memory state" in prompt.lower() or "frames" in prompt.lower()
