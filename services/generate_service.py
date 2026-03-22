import os
import httpx
import json
from dotenv import load_dotenv

load_dotenv()

# Groq fallback model chain: tries fast models first, degrades gracefully
GROQ_FALLBACK_MODELS = [
    "llama-3.1-8b-instant",   # Primary: fastest
    "gemma2-9b-it",           # Fallback 1: different architecture
    "mixtral-8x7b-32768",     
]

async def _call_llm(client: httpx.AsyncClient, url: str, key: str, model: str, messages: list, max_tokens: int) -> str:
    """Makes a single LLM API call and returns the text content."""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens
    }
    response = await client.post(url, headers=headers, json=payload, timeout=60.0)
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")
    return response.json()["choices"][0]["message"]["content"]

async def generate_report(query: str, format_type: str, ranked_chunks: list[dict]) -> str:
    """
    Generates the final output using an external LLM.
    This is the ONLY time the LLM is called in the entire pipeline.

    Fallback Chain:
    1. Primary model (LLM_MODEL from .env, default: llama-3.1-8b-instant)
    2. gemma2-9b-it  (Groq)
    3. mixtral-8x7b  (Groq)
    4. Graceful degraded summary built directly from ranked chunk text
    """
    # Read at call-time so --reload / new .env values are always picked up
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_API_URL = os.getenv("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")
    LLM_MODEL   = os.getenv("LLM_MODEL", "llama-3.1-8b-instant")

    if not ranked_chunks:
        return "No verifiable sources found. Cannot generate a confident report."

    # Build compressed context from top-ranked chunks
    context_texts = []
    for i, chunk in enumerate(ranked_chunks):
        url  = chunk.get("url", "Unknown source")
        text = chunk.get("text", "")
        context_texts.append(f"Source [{i+1}] ({url}): {text}")
    context_block = "\n\n".join(context_texts)

    system_prompt = (
        "You are an expert Automated Research Agent. Your task is to generate a well-structured "
        f"response in the exact format requested: '{format_type}'.\n"
        "Rules:\n"
        "1. Strictly base your answer on the provided Source Materials.\n"
        "2. Do not hallucinate or use external unverified knowledge.\n"
        "3. Provide in-line citations referencing [Source X] whenever you state a fact.\n"
        "4. Output should strictly follow the formatting requirements."
    )
    user_prompt = f"Query: {query}\n\n=== SOURCE MATERIALS ===\n{context_block}\n\nGenerate the {format_type} now."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt}
    ]

    # If no real API key is present, mock the generation to prevent crashes in dev.
    if not LLM_API_KEY:
        print("⚠️ No LLM API key — returning dev mock response.")
        return (
            f"[MOCK] {format_type} for query '{query}'.\n\n"
            f"Derived from {len(ranked_chunks)} verified chunks.\n\n"
            "1. Based on the sources, ...\n2. The evidence suggests... [Source 1]"
        )

    # Build the model fallback chain: primary first, then fallbacks
    primary = LLM_MODEL
    fallback_chain = [primary] + [m for m in GROQ_FALLBACK_MODELS if m != primary]

    async with httpx.AsyncClient() as client:
        for attempt, model in enumerate(fallback_chain):
            try:
                if attempt > 0:
                    print(f"⚠️ LLM fallback attempt {attempt}: trying model '{model}'")
                result = await _call_llm(client, LLM_API_URL, LLM_API_KEY, model, messages, max_tokens=1500)
                if attempt > 0:
                    print(f"✅ Fallback model '{model}' succeeded.")
                return result
            except Exception as e:
                print(f"❌ Model '{model}' failed: {e}")

    # Final fallback: build a plain-text summary from the raw chunks (no LLM needed)
    print("⚠️ All LLM models failed — generating degraded text summary from source chunks.")
    degraded_lines = [f"[Source {i+1}] {c.get('text', '')[:300]}..." for i, c in enumerate(ranked_chunks)]
    return (
        f"⚠️ AI generation temporarily unavailable. Here is a raw excerpt summary for '{query}':\n\n"
        + "\n\n".join(degraded_lines)
    )
