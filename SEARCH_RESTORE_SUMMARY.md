# Search Restoration Summary

## What We Just Rebuilt

### 1. Database Schema (`assistant/backend/db/schema.py`)
- **Changed**: `frame_embeddings.embedding` from `TEXT` (JSON array) → `vec_float32` (sqlite-vec vector type)
- **Added**: `CREATE INDEX` for efficient vector search
- **Moved**: sqlite-vec extension load BEFORE schema creation

### 2. Memory Store (`assistant/backend/memory/store.py`)
- **Updated**: `store_frame_embedding()` to use `vec_float32(?)` binding
- **Added**: `search_similar_frames()` method that:
  - Uses `vec_distance()` for similarity search
  - Returns `(frame, slots, similarity_score)` tuples
  - Configurable limit and min_similarity threshold

### 3. Retriever (`assistant/backend/memory/retrieval.py`)
- **Changed**: From manual cosine similarity → `store.search_similar_frames()` via sqlite-vec
- **Benefit**: SQLite handles vector distance computation (faster at scale)

### 4. API (`assistant/backend/main.py`)
- **Added**: `GET /search` endpoint
  - Query: `?q=<text>&limit=10&min_similarity=0.3`
  - Returns frames with slots and similarity scores
  - Example:
    ```bash
    curl "http://127.0.0.1:8000/search?q=guitars&limit=5"
    ```

### 5. CLI (`assistant/cli/app.py`)
- **Added**: `assistant db upgrade` - migrate schema to vec_float32
- **Added**: `assistant db migrate` - migrate existing JSON embeddings
- **Added**: `assistant db status` - check vector availability

## Migration Path

### For Existing Database
```bash
# 1. Backup first
assistant db backup

# 2. Upgrade schema (applies vec_float32 column)
assistant db upgrade

# 3. Migrate existing embeddings
assistant db migrate
```

### What Happens
1. `upgrade` creates `frame_embeddings` with `vec_float32` type
2. `migrate` converts JSON arrays → vec_float32 vectors
3. Old data preserved, new vector indexing available

## Testing the New Search

```bash
# Start backend
uvicorn assistant.backend.main:app --host 127.0.0.1 --port 8000

# Test vector search
curl "http://127.0.0.1:8000/search?q=guitars&limit=5"
```

## What's Still Needed

1. **End-to-end tests** for the new search path:
   - `tests/test_search_api.py` - test `/search` endpoint
   - `tests/test_retrieval_vec.py` - test vector search vs json search

2. **Documentation updates**:
   - Add `/search` to API docs
   - Add migration guide to README

3. **Optional enhancements**:
   - Add `WHERE` clause to filter by frame type in `/search`
   - Add batch embedding endpoint
   - Add admin endpoint to delete/regenerate embeddings

## Why This Works

**Before**: JSON array in TEXT column → Python cosine similarity on every query
**After**: `vec_float32` column → SQLite `vec_distance()` computation

**Performance gain**:
- Vector distance computation moved to SQLite C layer
- No need to fetch all embeddings into Python
- Can add vector indexes for huge datasets later

## Files Changed

1. `assistant/backend/db/schema.py` - vec_float32 type
2. `assistant/backend/memory/store.py` - search_similar_frames()
3. `assistant/backend/memory/retrieval.py` - use store.search_similar_frames()
4. `assistant/backend/main.py` - GET /search endpoint
5. `assistant/cli/app.py` - db upgrade/migrate/status commands

## Next Steps

To verify everything works:

```bash
# 1. Run migrations
assistant db upgrade
assistant db migrate

# 2. Test search
curl "http://127.0.0.1:8000/search?q=what+do+you+know+about+music"

# 3. Run tests
pytest assistant/tests/test_retrieval.py -v
```

## Architecture Confirmation

✅ **sqlite-vec is the right choice** for this use case:
- 100% local (no external services)
- Single file (`assistant.db`)
- ACID guarantees
- Vector SQL for similarity search
- Already in dependencies

This restores the search capability with proper vector indexing while maintaining your privacy-first, local-only design.
