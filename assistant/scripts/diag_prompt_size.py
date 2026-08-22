import asyncio
import json


async def main():
    from assistant.backend.config import settings
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import (
        OllamaClient,
        build_system_prompt,
    )
    from assistant.backend.pipeline.orchestrator import Orchestrator
    from assistant.backend.pipeline.tools import builtin_tools

    store = MemoryStore(settings.database_path)
    llm = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
        keep_alive="5m",
    )
    retr = Retriever(
        store=store,
        llm_client=llm,
        embedding_model=settings.embedding_model,
    )
    orch = Orchestrator.__new__(Orchestrator)
    orch.store = store

    self_ctx = await orch._get_self_context()
    print(f"self_context: {len(self_ctx)} chars (~{len(self_ctx)//4} tok)")

    mc = await retr.retrieve(query="What guitar do I own?", user_id=1)
    fmt = mc.formatted
    print(
        f"memory_context.formatted: {len(fmt)} chars (~{len(fmt)//4} tok), "
        f"frames={len(mc.retrieved_frames)}"
    )
    print("--- first 800 chars of formatted ---")
    print(fmt[:800])
    print("--- last 400 chars ---")
    print(fmt[-400:])

    sp = build_system_prompt(
        memory_context=fmt,
        task_type="introspective",
        planinstructions="[example plan instructions line]",
        self_context=self_ctx,
    )
    print(f"\nfull system_prompt: {len(sp)} chars (~{len(sp)//4} tok)")

    tools = builtin_tools(None)
    tj = json.dumps(tools)
    print(f"tools JSON: {len(tj)} chars (~{len(tj)//4} tok), {len(tools)} tools")

    await store.close()
    await llm.close()


asyncio.run(main())
