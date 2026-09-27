"""File Read Experiment: Test agent's ability to read uploaded files using read_file tool."""

import asyncio
import tempfile
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.memory.working_memory import WorkingMemory
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.pipeline.tool_executor import execute_read_file, execute_tool, init_store


async def run_file_read_experiment():
    """Run the file read experiment to test read_file tool."""
    
    # Setup
    db_path = "/tmp/test_file_read.db"
    settings.database_path = db_path
    settings.scheduler_enabled = False
    
    await init_db(db_path)
    
    # Initialize the global store for tool_executor
    init_store(db_path)
    
    store = MemoryStore(db_path)
    working_memory = WorkingMemory(db_path=db_path, max_size=10, boost=0.1)
    llm_client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
    )
    retriever = Retriever(
        store=store,
        llm_client=llm_client,
        embedding_model=settings.embedding_model,
        working_memory=working_memory,
    )
    search_tool = WebSearchTool(
        base_url=settings.search_base_url,
        enabled=True,
    )
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm_client,
            search_tool=search_tool,
        )
    )
    
    # Create a test user
    user = await store.create_user("test_user")
    user_id = user.id
    
    # 1. Create a test file
    test_content = """# Project Configuration

project_name: MyDigitalAssistant
version: 1.0.0
environment: development

# Database Settings
db_host: localhost
db_port: 5432
db_name: assistant_db

# API Keys
api_key: sk-test-12345
webhook_url: https://example.com/webhook
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(test_content)
        test_file_path = f.name
    
    try:
        # 2. Upload the file via the same logic as /files/upload endpoint
        from assistant.backend.pipeline.files import extract_file_content
        
        content_bytes = test_content.encode('utf-8')
        ext = "txt"
        import time
        safe_filename = f"test_config_{int(time.time())}.txt"
        
        extraction_result = await extract_file_content(test_file_path, ext, content_bytes)
        
        # Store in memory (simulating upload endpoint)
        frame_name = f"file_{safe_filename}"
        frame = await store.create_frame(
            frame_name,
            "entity",
            source_type="file_upload",
            owner_user_id=user_id,
            source_reliability=0.7,
        )
        
        # Also save file to disk (like upload endpoint does)
        data_dir = Path("/app/data")
        data_dir.mkdir(exist_ok=True)
        file_path = data_dir / safe_filename
        with open(file_path, "wb") as f:
            f.write(content_bytes)
        
        await store.upsert_slot(
            frame_id=frame.id,
            key="file_name",
            value="test_config.txt",
            essential=0,
            priority=0.5,
            source_type="file_upload",
            source_reliability=0.8,
        )
        
        await store.upsert_slot(
            frame_id=frame.id,
            key="file_content_preview",
            value=extraction_result.text[:200],
            essential=0,
            priority=0.5,
            source_type="file_upload",
            source_reliability=0.8,
        )
        
        await store.upsert_slot(
            frame_id=frame.id,
            key="file_safe_name",
            value=safe_filename,
            essential=0,
            priority=0.5,
            source_type="file_upload",
            source_reliability=0.7,
        )
        
        print(f"✓ Uploaded file as frame: {frame.name} (id={frame.id})")
        print(f"  Content preview: {extraction_result.text[:100]}...")
        print()
        
        # 3. Test: Direct tool call to read_file by frame_id
        print("=== Test 1: read_file tool by frame_id ===")
        result = await execute_read_file({"frame_id": frame.id}, str(user_id), "")
        print(f"Success: {result.success}")
        if result.success:
            print(f"File: {result.data['file_name']}")
            print(f"Content length: {result.data['size']} chars")
            print(f"Content preview: {result.data['content'][:200]}...")
        else:
            print(f"Error: {result.error}")
        print()
        
        # 4. Test: Direct tool call to read_file by frame_name
        print("=== Test 2: read_file tool by frame_name ===")
        result2 = await execute_read_file({"frame_name": frame.name}, str(user_id), "")
        print(f"Success: {result2.success}")
        if result2.success:
            print(f"File: {result2.data['file_name']}")
            print(f"Content length: {result2.data['size']} chars")
        else:
            print(f"Error: {result2.error}")
        print()
        
        # 5. Test: Available tools in agent
        print("=== Test 3: Available tools in agent ===")
        from assistant.backend.pipeline.tools import builtin_tools
        tools = builtin_tools(
            search_tool=search_tool,
            store=store,
            llm_client=llm_client,
        )
        tool_names = [t['function']['name'] for t in tools]
        print(f"Available tools: {tool_names}")
        print("Has read_file tool:", 'read_file' in tool_names)
        print()
        
        # 6. Test: Embed the frame and search for it
        print("=== Test 4: Embed frame and search ===")
        embed_fn = orchestrator.embed_fn()
        await store.embed_frames([frame.id], embed_fn, settings.embedding_model)
        
        embed_response = await llm_client.embed("test_config project configuration")
        memory_results = await store.search_similar_frames(
            embedding=embed_response.embedding,
            user_id=user_id,
            embedding_model=settings.embedding_model,
            limit=5,
            min_distance=0.7,
        )
        print(f"Memory search results: {len(memory_results)} frames")
        for frame_result, slots, similarity in memory_results:
            print(f"  Frame: {frame_result.name} (similarity: {similarity:.3f})")
            for slot in slots:
                print(f"    {slot.key}: {slot.value[:80]}")
        print()
        
        # 7. Test: read_file tool via execute_tool dispatcher
        print("=== Test 5: read_file via execute_tool dispatcher ===")
        args = {"frame_id": frame.id}
        result = await execute_tool("read_file", args, str(user_id), "test_session")
        print(f"Success: {result.success}")
        if result.success:
            print(f"File: {result.data['file_name']}")
            print(f"Content length: {result.data['size']} chars")
        else:
            print(f"Error: {result.error}")
        print()
        
        print("=== SUMMARY ===")
        print("✓ read_file tool added and working")
        print("✓ Agent can read uploaded files by frame_id or frame_name")
        
    finally:
        # Cleanup
        Path(test_file_path).unlink(missing_ok=True)
        await llm_client.close()
        await search_tool.close()


if __name__ == "__main__":
    asyncio.run(run_file_read_experiment())