import asyncio
import json
import uuid
from datetime import datetime

from services.search_service import search_google
from services.verify_service import verify_sources
from services.process_service import chunk_documents
from services.rank_service import rank_and_store_chunks
from services.generate_service import generate_report
from services.payment_service import check_credits, deduct_credits
from db.redis_client import get_cache, set_cache
from db.supabase_client import get_supabase_client
from db.sqlite_client import save_research
from db.knowledge_base import (
    recall_similar_research,
    memorize_research,
    compute_quality_score,
    build_memory_context,
)

async def execute_pipeline(query: str, user_id: str, request_format: str = "detailed report"):
    """
    Self-learning orchestrator pipeline.

    On every run the brain:
      1. Recalls semantically similar past research from vector memory
      2. Injects prior knowledge into the LLM context
      3. Searches, verifies, processes, ranks, and generates fresh data
      4. Memorizes the result back into Supabase (with quality score)
      5. Continuously improves source domain trust scores

    Over time it becomes smarter at every topic it has researched before.
    """
    try:
        # ── 0. Credit check ──────────────────────────────────────────────────
        yield json.dumps({"status": "Checking credits...", "stage": "init"})
        has_credits = await check_credits(user_id)
        if not has_credits:
            yield json.dumps({
                "status": "Insufficient credits.",
                "stage": "error",
                "result": "Please upgrade your plan or top up to run more research queries."
            })
            return

        # ── 1. Redis cache check ──────────────────────────────────────────────
        yield json.dumps({"status": "Checking cache...", "stage": "cache_check"})
        cache_key = f"research_{query}_{request_format}".replace(" ", "_").lower()
        cached_result = await get_cache(cache_key)
        if cached_result:
            try:
                data = json.loads(cached_result)
                result_text = data.get("text", "")
                download_url = data.get("url", None)
            except json.JSONDecodeError:
                result_text = cached_result
                download_url = None
            yield json.dumps({
                "status": "⚡ Cache hit! Returning instantly. (No credits deducted)",
                "stage": "done",
                "result": result_text,
                "download_url": download_url
            })
            return

        # ── 2. Memory recall: learn from past research ────────────────────────
        yield json.dumps({"status": "🧠 Searching memory for similar past research...", "stage": "cache_check"})
        memories = await recall_similar_research(query, user_id, threshold=0.82)
        memory_context = build_memory_context(memories)
        if memories:
            yield json.dumps({
                "status": f"🧠 Found {len(memories)} similar past research entries — injecting prior knowledge into LLM context.",
                "stage": "cache_check"
            })
        else:
            yield json.dumps({"status": "No similar past research found. Starting fresh.", "stage": "cache_check"})

        # ── 3. Web search ─────────────────────────────────────────────────────
        yield json.dumps({"status": f"🌐 Searching for live sources for '{query}'...", "stage": "search"})
        search_results = await search_google(query, num_results=5)

        # ── 4. Verify ─────────────────────────────────────────────────────────
        yield json.dumps({"status": f"✓ Verifying {len(search_results)} sources with NLI transformer...", "stage": "verify"})
        verified_data = await verify_sources(search_results, query)

        # ── 5. Process ────────────────────────────────────────────────────────
        yield json.dumps({"status": "📄 Chunking and tokenizing documents...", "stage": "process"})
        processed_chunks = await chunk_documents(verified_data, max_tokens=500)

        # ── 6. Rank ───────────────────────────────────────────────────────────
        yield json.dumps({"status": f"📊 Vectorizing {len(processed_chunks)} chunks and ranking by relevance...", "stage": "rank"})
        ranked_data = await rank_and_store_chunks(processed_chunks, query)

        # ── 7. Generate with memory-augmented context ─────────────────────────
        yield json.dumps({"status": "🤖 Generating report (with prior knowledge injected)...", "stage": "generate"})

        # Prepend memory context to the query so the LLM has prior knowledge
        augmented_query = query
        if memory_context:
            augmented_query = f"{memory_context}\n\nCurrent Query: {query}"

        final_report = await generate_report(augmented_query, request_format, ranked_data)

        # ── 8. Save to cloud + cache ──────────────────────────────────────────
        yield json.dumps({"status": "☁️ Saving report to cloud storage...", "stage": "format"})

        file_url = None
        try:
            sb = get_supabase_client()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"report_{user_id}_{timestamp}_{uuid.uuid4().hex[:6]}.md"
            file_bytes = final_report.encode("utf-8")

            def _upload():
                sb.storage.from_("reports").upload(
                    path=filename,
                    file=file_bytes,
                    file_options={"content-type": "text/markdown"}
                )
                return sb.storage.from_("reports").get_public_url(filename)

            file_url = await asyncio.to_thread(_upload)
        except Exception as e:
            print(f"Supabase upload failed: {e}")

        # Redis cache
        report_data = {"text": final_report, "url": file_url}
        await set_cache(cache_key, json.dumps(report_data), expire=86400)

        # ── 9. Self-learn: memorize this result ───────────────────────────────
        quality = compute_quality_score(ranked_data, final_report)
        yield json.dumps({"status": f"🧠 Memorizing result (quality score: {quality:.2f})...", "stage": "cleanup"})

        # Fire-and-forget: both saves run in background, never blocking the user
        asyncio.create_task(memorize_research(
            user_id, query, request_format, final_report, ranked_data, quality
        ))
        asyncio.create_task(save_research(user_id, query, request_format, final_report, file_url))

        # ── 10. Deduct credits ─────────────────────────────────────────────────
        await deduct_credits(user_id)

        # ── Done ──────────────────────────────────────────────────────────────
        yield json.dumps({
            "status": "Complete",
            "stage": "done",
            "result": final_report,
            "download_url": file_url,
            "quality_score": quality,
            "memories_used": len(memories)
        })

    except Exception as e:
        yield json.dumps({
            "status": "Pipeline failed",
            "stage": "error",
            "result": str(e)
        })
