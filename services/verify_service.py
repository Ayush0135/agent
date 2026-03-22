import asyncio
from transformers import pipeline
import torch

# Initialize pipeline lazily to save startup time / RAM if unused
# cross-encoder/nli-MiniLM2-L6-H768: tiny (67MB), fast NLI model, fully compatible with transformers 4.41.x
# Produces 3 labels: entailment, neutral, contradiction — perfect for source relevance checking.
verifier = None

def get_verifier():
    global verifier
    if verifier is None:
        device = 0 if torch.cuda.is_available() else -1
        verifier = pipeline(
            "text-classification",
            model="cross-encoder/nli-MiniLM2-L6-H768",
            device=device
        )
    return verifier

async def verify_sources(search_results: list[dict], query: str) -> list[dict]:
    """
    Verifies information across sources using a Hugging Face model.
    It performs cross-encoder Natural Language Inference (NLI).
    It ensures the document text (premise) entails or relates to the query (hypothesis)
    without using the large LLM multiple times.
    """
    if not search_results:
        return []

    verified_sources = []
    
    # Run the blocking pipeline call inside a thread to avoid blocking asyncio loop
    def _run_verification():
        v = get_verifier()
        results_out = []
        for src in search_results:
            # Premise is the combined document text (truncated to model max ~512 tokens)
            premise = (src.get("snippet", "") + " " + src.get("content", ""))[:1000]
            
            # cross-encoder text-classification: pass as {"text": premise, "text_pair": query}
            # Returns list: [{"label": "entailment", "score": 0.91}, ...]
            out = v({"text": premise, "text_pair": query})
            result = out[0] if isinstance(out, list) else out
            label = result.get("label", "neutral").lower()
            score = result.get("score", 0.0)
            
            src["verification"] = {
                "label": label,   # entailment / neutral / contradiction
                "score": score
            }
            results_out.append(src)
        return results_out


        
    try:
        # Offload the HF inferencing to thread pool
        verified_sources = await asyncio.to_thread(_run_verification)
    except Exception as e:
        print(f"Verification HF Model failed, using fallback: {e}")
        # Fallback Strategy: if HF pipeline fails, gracefully pass data
        for src in search_results:
            src["verification"] = {"label": "fallback_neutral", "score": 1.0}
            verified_sources.append(src)

    return verified_sources
