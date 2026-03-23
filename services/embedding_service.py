import os
import httpx
from dotenv import load_dotenv

load_dotenv()

HF_API_KEY = os.getenv("HF_API_KEY", "")

# We switch to Inference API to save RAM on Render Free Tier (512MB)
# Endpoint for the same model: all-MiniLM-L6-v2
API_URL = "https://api-inference.huggingface.co/models/sentence-transformers/all-MiniLM-L6-v2"

async def generate_embedding(text: str) -> list[float]:
    """ Generate embedding representation via HF Inference API """
    result = await generate_embeddings_batch([text])
    return result[0] if result else []

async def generate_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """ Generate batch embeddings via HF Inference API """
    if not texts or not HF_API_KEY:
        return []
        
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {HF_API_KEY}"}
            data = {"inputs": texts, "options": {"wait_for_model": True}}
            
            response = await client.post(API_URL, headers=headers, json=data, timeout=15.0)
            
            if response.status_code != 200:
                print(f"HF Embedding API failed ({response.status_code}): {response.text}")
                return []
                
            return response.json()
    except Exception as e:
        print(f"Embedding API exception: {e}")
        return []
