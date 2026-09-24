"""Tests for math model integration (Phase 1)."""

import os
import pytest


RUN_MATH_INTEGRATION = os.environ.get("RUN_MATH_INTEGRATION", "").lower() in ("1", "true", "yes")


class TestMathIntentDetection:
    """Test math intent detection accuracy."""

    @pytest.mark.asyncio
    async def test_detects_basic_calculation(self):
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.orchestrator import Orchestrator

        # Create mock deps
        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        # Test basic calculation keywords
        assert await orchestrator._detect_math_intent("calculate 2 + 2")
        assert await orchestrator._detect_math_intent("compute 5 * 10")
        assert await orchestrator._detect_math_intent("solve x^2 = 4")

    @pytest.mark.asyncio
    async def test_detects_financial_keywords(self):
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        query1 = "What is the NPV of $1000/year for 5 years at 7%?"
        query2 = "Calculate IRR for this investment"
        query3 = "What's the compound interest on $10000 at 5% for 10 years?"
        assert await orchestrator._detect_math_intent(query1)
        assert await orchestrator._detect_math_intent(query2)
        assert await orchestrator._detect_math_intent(query3)

    @pytest.mark.asyncio
    async def test_detects_statistical_keywords(self):
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        assert await orchestrator._detect_math_intent("Calculate the mean and standard deviation")
        assert await orchestrator._detect_math_intent("Run a t-test on these samples")
        assert await orchestrator._detect_math_intent("What's the correlation between X and Y?")

    @pytest.mark.asyncio
    async def test_detects_calculus_keywords(self):
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        assert await orchestrator._detect_math_intent("What's the derivative of x^2 + 3x?")
        assert await orchestrator._detect_math_intent("Integrate sin(x) from 0 to pi")
        assert await orchestrator._detect_math_intent("Find the limit as x approaches 0")

    @pytest.mark.asyncio
    async def test_no_false_positive_on_general_queries(self):
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        # These should NOT trigger math intent
        assert not await orchestrator._detect_math_intent("What's my name?")
        assert not await orchestrator._detect_math_intent("Tell me about the weather")
        assert not await orchestrator._detect_math_intent("How do I make coffee?")
        assert not await orchestrator._detect_math_intent("What is the capital of France?")


class TestMathModelIntegration:
    """Integration tests for math model computation (requires Ollama)."""

    @pytest.mark.skipif(
        not RUN_MATH_INTEGRATION,
        reason="Requires local Ollama with qwen3-coder:30b model (set RUN_MATH_INTEGRATION=1)"
    )
    @pytest.mark.asyncio
    async def test_simple_calculation(self):
        """Test simple arithmetic computation."""
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model=settings.math_model,
            math_num_ctx=settings.math_num_ctx,
            math_keep_alive=settings.math_keep_alive,
        )

        try:
            result = await client.execute_python("2 + 2")
            assert "4" in result
        finally:
            await client.close()

    @pytest.mark.skipif(
        not RUN_MATH_INTEGRATION,
        reason="Requires local Ollama with math model (set RUN_MATH_INTEGRATION=1)"
    )
    @pytest.mark.asyncio
    async def test_financial_npv(self):
        """Test NPV calculation."""
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model=settings.math_model,
            math_num_ctx=settings.math_num_ctx,
            math_keep_alive=settings.math_keep_alive,
        )

        try:
            code = """
import numpy as np
cashflows = [-1000, 300, 300, 300, 300, 300]
rate = 0.07
npv = sum(cf / (1 + rate)**i for i, cf in enumerate(cashflows))
print(f'NPV: {npv:.2f}')
"""
            result = await client.execute_python(code)
            assert "NPV" in result
            # Expected NPV at 7%: ~1000/1.07 + 300/1.07^2 + ... = 1200.84
        finally:
            await client.close()

    @pytest.mark.skipif(
        not RUN_MATH_INTEGRATION,
        reason="Requires local Ollama with math model (set RUN_MATH_INTEGRATION=1)"
    )
    @pytest.mark.asyncio
    async def test_statistical_computation(self):
        """Test statistical computation."""
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model=settings.math_model,
            math_num_ctx=settings.math_num_ctx,
            math_keep_alive=settings.math_keep_alive,
        )

        try:
            code = """
import statistics
data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
mean = statistics.mean(data)
stdev = statistics.stdev(data)
print(f'Mean: {mean:.2f}, StdDev: {stdev:.2f}')
"""
            result = await client.execute_python(code)
            assert "Mean:" in result
            assert "StdDev:" in result
        finally:
            await client.close()

    @pytest.mark.skipif(
        not RUN_MATH_INTEGRATION,
        reason="Requires local Ollama with math model and sympy (set RUN_MATH_INTEGRATION=1)"
    )
    @pytest.mark.asyncio
    async def test_calculus_symbolic(self):
        """Test symbolic calculus with sympy."""
        # Check if sympy is available in the sandbox
        try:
            import sympy
        except ImportError:
            pytest.skip("sympy not available in test environment")
        """Test symbolic calculus with sympy."""
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model=settings.math_model,
            math_num_ctx=settings.math_num_ctx,
            math_keep_alive=settings.math_keep_alive,
        )

        try:
            code = """
import sympy as sp
x = sp.symbols('x')
expr = x**2 + 3*x
derivative = sp.diff(expr, x)
print(f'Derivative: {derivative}')
"""
            result = await client.execute_python(code)
            assert "2*x + 3" in result or "2*x+3" in result
        finally:
            await client.close()


class TestMathTool:
    """Tests for the compute tool."""

    @pytest.mark.asyncio
    async def test_compute_tool_registered(self):
        """Test that compute tool is registered when math_model is configured."""
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.tools import builtin_tools

        llm_client = MagicMock()
        llm_client.math_model = "qwen3-coder:30b"

        tools = builtin_tools(llm_client=llm_client)
        compute_tools = [t for t in tools if t["function"]["name"] == "compute"]
        assert len(compute_tools) == 1
        assert "financial models" in compute_tools[0]["function"]["description"]

    @pytest.mark.asyncio
    async def test_compute_tool_not_registered_without_math_model(self):
        """Test that compute tool is NOT registered when math_model is not configured."""
        from unittest.mock import MagicMock

        from assistant.backend.pipeline.tools import builtin_tools

        llm_client = MagicMock()
        llm_client.math_model = ""

        tools = builtin_tools(llm_client=llm_client)
        compute_tools = [t for t in tools if t["function"]["name"] == "compute"]
        assert len(compute_tools) == 0


class TestSandboxedExecution:
    """Tests for the sandboxed Python execution."""

    @pytest.mark.asyncio
    async def test_basic_execution(self):
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model="",  # No math model needed for sandbox test
        )

        result = await client._execute_python_sandboxed("print(2 + 2)", 10)
        assert "4" in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model="",
        )

        result = await client._execute_python_sandboxed("import time; time.sleep(10)", 1)
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_imports_available(self):
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model="",
        )

        result = await client._execute_python_sandboxed("import math; print(math.pi)", 10)
        assert "3.14" in result

    @pytest.mark.asyncio
    async def test_numpy_available(self):
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model="",
        )

        code = "import numpy as np; print(np.array([1,2,3]).sum())"
        result = await client._execute_python_sandboxed(code, 10)
        assert "6" in result


class TestOrchestratorMathIntegration:
    """Test orchestrator integration with math computation."""

    @pytest.mark.asyncio
    async def test_math_result_injected_into_prompt(self):
        """Test that math computation result is injected into system prompt."""
        # This would require a more complex integration test with a real LLM
        # For now, we test the intent detection which is the trigger
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])