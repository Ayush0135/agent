import os
import httpx
from dotenv import load_dotenv

load_dotenv()

HF_API_KEY = os.getenv("HF_API_KEY", "")

# Same model: cross-encoder/nli-MiniLM2-L6-H768 for NLI logic
# Using Inference API removes the local RAM usage entirely
API_URL = "https://api-inference.huggingface.co/models/cross-encoder/nli-MiniLM2-L6-H768"

async def verify_sources(search_results: list[dict], query: str) -> list[dict]:
    """
    Verifies information across sources using HF Inference API.
    Replaces local transformers call for 512MB RAM compatibility.
    """
    if not search_results or not HF_API_KEY:
        return search_results

    # Prepare inputs for Inference API (batching)
    # We send premise and hypothesis together as a list of strings if the model supports it, 
    # but Cross-Encoders usually expect inputs in separate calls or a specific list format.
    verified_sources = []
    
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {HF_API_KEY}"}
            
            for src in search_results:
                premise = (src.get("snippet", "") + " " + src.get("content", ""))[:1000]
                
                # API format for text-classification with text-pair
                payload = {
                    "inputs": {"text": premise, "text_pair": query},
                    "options": {"wait_for_model": True}
                }
                
                # We do this sequentially here for simplicity, or gather them.
                # Since we check only 10 sources, it's manageable.
                resp = await client.post(API_URL, headers=headers, json=payload, timeout=12.0)
                
                if resp.status_code == 200:
                    data = resp.json()
                    result = data[0] if isinstance(data, list) else data
                    # Inference API returns labels differently sometimes.
                    # Usually: [{"label": "LABEL_0", "score": 0.99}, ...]
                    # For NLI: Neutral/Entailment/Contradiction
                    label = result.get("label", "neutral").lower()
                    score = result.get("score", 1.0)
                else:
                    label, score = "fallback", 1.0
                
                src["verification"] = {"label": label, "score": score}
                verified_sources.append(src)
                
    except Exception as e:
        print(f"HF Verify API failed: {e}")
        # Fallback Strategy: if HF pipeline fails, gracefully pass data
        for src in search_results:
            src["verification"] = {"label": "fallback_error", "score": 1.0}
            verified_sources.append(src)

    return verified_sources
