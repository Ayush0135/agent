import asyncio
import json
import uuid
import time
from datetime import datetime

from services.search_service import search_google, fetch_images
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

def _fmt(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 1:
        return f"{int(seconds*1000)}ms"
    return f"{seconds:.1f}s"

async def execute_pipeline(query: str, user_id: str, request_format: str = "detailed report"):
    """
    Self-learning orchestrator pipeline — with per-stage timing.

    Emits timing for every stage so you can see exactly where time is spent.
    Final 'done' message includes a full execution summary table.
    """
    pipeline_start = time.perf_counter()
    timings: dict[str, float] = {}   # stage_name -> elapsed seconds

    def _elapsed(stage_start: float) -> float:
        return time.perf_counter() - stage_start

    try:
        # ── 0. Credit check ──────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": "Checking credits...", "stage": "init"})
        has_credits = await check_credits(user_id)
        timings["credits"] = _elapsed(t)

        if not has_credits:
            yield json.dumps({
                "status": "Insufficient credits.",
                "stage": "error",
                "result": "Please upgrade your plan or top up to run more research queries."
            })
            return

        # ── 1. Redis cache check ──────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": "⚡ Checking cache...", "stage": "cache_check"})
        cache_key = f"research_{query}_{request_format}".replace(" ", "_").lower()
        cached_result = await get_cache(cache_key)
        timings["cache"] = _elapsed(t)

        if cached_result:
            try:
                data = json.loads(cached_result)
                result_text  = data.get("text", "")
                download_url = data.get("url", None)
            except json.JSONDecodeError:
                result_text  = cached_result
                download_url = None

            total = time.perf_counter() - pipeline_start
            yield json.dumps({
                "status": f"⚡ Cache hit! Returned in {_fmt(total)} (no credits deducted)",
                "stage": "done",
                "result": result_text,
                "download_url": download_url,
                "timings": timings,
                "total_time": round(total, 2),
            })
            return

        # ── 2. Memory recall ──────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": "🧠 Searching memory for similar past research...", "stage": "cache_check"})
        memories = await recall_similar_research(query, user_id, threshold=0.82)
        memory_context = build_memory_context(memories)
        timings["memory_recall"] = _elapsed(t)

        mem_msg = (
            f"🧠 Injecting {len(memories)} memory entries into context..."
            if memories else
            "🧠 No similar past research in memory. Starting fresh."
        )
        yield json.dumps({"status": mem_msg, "stage": "cache_check"})

        # ── 3. Web search ─────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": f"🌐 Fetching 10 live sources for '{query}'...", "stage": "search"})
        search_results, image_results = await asyncio.gather(
            search_google(query, num_results=10),
            fetch_images(query, num_results=5)
        )
        timings["search"] = _elapsed(t)
        yield json.dumps({
            "status": f"🌐 Found {len(search_results)} sources in {_fmt(timings['search'])}",
            "stage": "search"
        })
        
        # Extract images found while scraping those sources
        scraped_images = []
        for res in search_results:
            if "scraped_images" in res:
                scraped_images.extend(res["scraped_images"])
        
        # Prioritize scraped images over search images as they are more contextual
        all_images = scraped_images + image_results
        
        # Keep only unique URLs to avoid duplicates
        unique_images, seen_urls = [], set()
        for img in all_images:
            if img["url"] not in seen_urls:
                unique_images.append(img)
                seen_urls.add(img["url"])
        
        image_results = unique_images[:8] # Send more for richer visuals

        # ── 4. Verify ─────────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({
            "status": f"✓ Verifying {len(search_results)} sources with NLI transformer...",
            "stage": "verify"
        })
        verified_data = await verify_sources(search_results, query)
        timings["verify"] = _elapsed(t)
        yield json.dumps({
            "status": f"✓ Verified {len(verified_data)} credible sources in {_fmt(timings['verify'])}",
            "stage": "verify"
        })

        # ── 5. Process ────────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": "📄 Chunking and tokenizing documents...", "stage": "process"})
        processed_chunks = await chunk_documents(verified_data, max_tokens=800)
        timings["process"] = _elapsed(t)
        yield json.dumps({
            "status": f"📄 {len(processed_chunks)} chunks created in {_fmt(timings['process'])}",
            "stage": "process"
        })

        # ── 6. Rank ───────────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({
            "status": f"📊 Vectorizing & ranking {len(processed_chunks)} chunks...",
            "stage": "rank"
        })
        ranked_data = await rank_and_store_chunks(processed_chunks, query)
        timings["rank"] = _elapsed(t)
        yield json.dumps({
            "status": f"📊 Ranked top {len(ranked_data)} chunks in {_fmt(timings['rank'])}",
            "stage": "rank"
        })

        # ── 7. Generate ───────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({
            "status": "🤖 Generating report via HF Mistral → Groq fallback...",
            "stage": "generate"
        })

        augmented_query = query
        if memory_context:
            augmented_query = f"{memory_context}\n\nCurrent Query: {query}"

        final_report = await generate_report(augmented_query, request_format, ranked_data, image_results)
        timings["generate"] = _elapsed(t)
        yield json.dumps({
            "status": f"🤖 Report generated in {_fmt(timings['generate'])}",
            "stage": "generate"
        })

        # ── 8. Save ───────────────────────────────────────────────────────────
        t = time.perf_counter()
        yield json.dumps({"status": "☁️ Saving to cloud & cache...", "stage": "format"})

        file_url = None
        try:
            sb = get_supabase_client()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename  = f"report_{user_id}_{timestamp}_{uuid.uuid4().hex[:6]}.md"
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

        await set_cache(cache_key, json.dumps({"text": final_report, "url": file_url}), expire=86400)
        timings["save"] = _elapsed(t)

        # ── 9. Self-learn: memorize ───────────────────────────────────────────
        t = time.perf_counter()
        quality = compute_quality_score(ranked_data, final_report)
        yield json.dumps({
            "status": f"🧠 Memorizing result (quality: {quality:.2f})...",
            "stage": "cleanup"
        })
        asyncio.create_task(memorize_research(
            user_id, query, request_format, final_report, ranked_data, quality
        ))
        asyncio.create_task(save_research(user_id, query, request_format, final_report, file_url))
        timings["memorize"] = _elapsed(t)

        # ── 10. Deduct credits ────────────────────────────────────────────────
        await deduct_credits(user_id)

        # ── Done ─────────────────────────────────────────────────────────────
        total = time.perf_counter() - pipeline_start
        timings["total"] = round(total, 2)

        # Print timing table to server logs
        print("\n" + "─"*52)
        print(f" ⏱  Pipeline Timing — '{query[:40]}'")
        print("─"*52)
        stages = [
            ("Credits",         "credits"),
            ("Cache check",     "cache"),
            ("Memory recall",   "memory_recall"),
            ("Web search",      "search"),
            ("NLI verify",      "verify"),
            ("Chunk process",   "process"),
            ("Vector rank",     "rank"),
            ("LLM generate",    "generate"),
            ("Save/upload",     "save"),
            ("Memorize",        "memorize"),
        ]
        for label, key in stages:
            val = timings.get(key, 0)
            bar = "█" * max(1, int(val * 4))
            print(f"  {label:<16} {_fmt(val):>6}  {bar}")
        print("─"*52)
        print(f"  {'TOTAL':<16} {_fmt(total):>6}")
        print("─"*52 + "\n")

        yield json.dumps({
            "status": f"✅ Complete in {_fmt(total)}",
            "stage": "done",
            "result": final_report,
            "download_url": file_url,
            "quality_score": quality,
            "memories_used": len(memories),
            "timings": {k: round(v, 2) for k, v in timings.items()},
            "total_time": round(total, 2),
        })

    except Exception as e:
        total = time.perf_counter() - pipeline_start
        yield json.dumps({
            "status": f"Pipeline failed after {_fmt(total)}",
            "stage": "error",
            "result": str(e)
        })
