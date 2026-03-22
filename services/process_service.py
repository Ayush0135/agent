import asyncio
from typing import List, Dict

async def chunk_documents(verified_data: List[Dict], max_tokens: int = 500) -> List[Dict]:
    """
    Split document content into chunks of roughly `max_tokens`.
    For simplicity and performance without tokenizer overhead, we approximate 
    1 token ~ 0.75 words. Max 500 tokens = ~375 words.
    """
    chunks = []
    if not verified_data:
        return chunks
        
    def _chunk_sync():
        words_per_chunk = int(max_tokens * 0.75)
        local_chunks = []
        
        for doc in verified_data:
            content = doc.get("content", "")
            if not content:
                continue
            words = content.split()
            
            for i in range(0, len(words), words_per_chunk):
                chunk_text = " ".join(words[i:i + words_per_chunk])
                local_chunks.append({
                    "url": doc.get("url"),
                    "title": doc.get("title"),
                    "text": chunk_text,
                    "verification_score": doc.get("verification", {}).get("score", 0.0)
                })
        return local_chunks
        
    return await asyncio.to_thread(_chunk_sync)
