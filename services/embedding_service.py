import asyncio
from sentence_transformers import SentenceTransformer
import torch

# Global instantiation
embedder = None

def get_embedder():
    global embedder
    if embedder is None:
        device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        # 'all-MiniLM-L6-v2' is fast, lightweight, and suitable for vector search
        embedder = SentenceTransformer('all-MiniLM-L6-v2', device=device)
    return embedder

async def generate_embedding(text: str) -> list[float]:
    """ Generate embedding representation asynchronously """
    def _run():
        return get_embedder().encode(text).tolist()
    return await asyncio.to_thread(_run)

async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """ Generate batch embeddings simultaneously """
    if not texts:
        return []
    def _run():
        return get_embedder().encode(texts).tolist()
    return await asyncio.to_thread(_run)
