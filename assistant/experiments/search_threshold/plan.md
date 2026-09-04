# Experiment: Brave Search Relevance Threshold Tuning (Orchestrator Context)

## Scientific Method Plan

**Question**: What `BRAVE_SEARCH_MIN_RELEVANCE` threshold maximizes extraction quality for typical user queries when using real embeddings within the Orchestrator context?

**Hypothesis**: Threshold 0.25 will balance coverage and quality better than 0.20 (current), yielding fewer but more relevant extracted facts when using real embeddings.

**Variables**:
- Independent: `BRAVE_SEARCH_MIN_RELEVANCE` (0.10, 0.15, 0.20, 0.25, 0.30)
- Dependent: Extraction quality (results passing filter_relevant with real embeddings)
- Controls: Same query terms, same Brave API key, same LLM client (nomic-embed-text via Ollama), same extraction pipeline

**Constraints**:
- Brave API calls: 1 per query (3 total within limits)
- Experiment runs within Orchestrator `_embed_fn()` context for real embeddings
- All experiments run sequentially, same session context

**Success Criteria**:
- Experiment produces differentiable threshold results (not all thresholds pass 100% of results)
- Data is reproducible (same query, same embeddings, same thresholds → same results)
- Findings inform meaningful threshold adjustment decision

**Timeline**: Same day (experiment runs via Orchestrator _embed_fn())

**Success Criteria**:
- 0.25 produces higher quality ratio than 0.20 (current) when using real embeddings
- OR if 0.20 is best, document why (no regression with real embeddings)

## How to Run the Experiment

1. **Within the Orchestrator**: The `_embed_fn()` method produces real embeddings:
   ```python
   async def _embed_fn_test(self):
       async def get_embedding(text):
           resp = await self.llm_client.embed(text)  # nomic-embed-text via Ollama
           return resp.embedding  # 768-dimensional vector
       return get_embedding
   ```
   
2. **Call filter_relevant** with the embed_fn:
   ```python
   filtered = await filter_relevant(results, 'artificial intelligence', embed_fn, min_relevance=threshold)
   ```
   
3. **Observe threshold differentiation**: Different thresholds will pass different numbers of results

4. **Update .env** based on findings:
   - `BRAVE_SEARCH_MIN_RELEVANCE=0.25` for quality-focused
   - `BRAVE_SEARCH_MIN_RELEVANCE=0.15` for coverage-focused
   - Keep at `0.20` for balanced default

**Timeline**: Same day (experiment runs via Orchestrator _embed_fn())
