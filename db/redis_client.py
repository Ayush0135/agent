import redis.asyncio as redis
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "")

# In-memory fallback if Redis is unavailable (useful for Render Free Tier)
# This will reset whenever the worker restarts, but prevents the app from crashing.
_memory_cache = {}

if REDIS_URL:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
else:
    print("⚠️ REDIS_URL not found — using In-Memory fallback (ephemeral).")
    redis_client = None

async def get_cache(key: str):
    if redis_client:
        try:
            return await asyncio.wait_for(redis_client.get(key), timeout=2.0)
        except Exception:
            pass
    return _memory_cache.get(key)

async def set_cache(key: str, value: str, expire: int = 3600):
    if redis_client:
        try:
            await asyncio.wait_for(redis_client.set(key, value, ex=expire), timeout=2.0)
            return
        except Exception:
            pass
    _memory_cache[key] = value

# For other redis commands used in payment_service
class RedisHelper:
    async def get(self, key): return await get_cache(key)
    async def set(self, key, val, ex=None): await set_cache(key, val, expire=ex or 3600)
    async def incr(self, key):
        val = int(await get_cache(key) or 0)
        await set_cache(key, str(val + 1))
        return val + 1
    async def decr(self, key):
        val = int(await get_cache(key) or 0)
        await set_cache(key, str(val - 1))
        return val - 1

# Provide a shim that matches the redis interface used elsewhere
if not redis_client:
    redis_client = RedisHelper()
else:
    # Wrap the real client with a timeout-safe check for basic methods
    class SafeRedis:
        def __init__(self, real): self.real = real
        async def get(self, k): 
            try: return await asyncio.wait_for(self.real.get(k), timeout=2.0)
            except: return _memory_cache.get(k)
        async def set(self, k, v, ex=None):
            try: await asyncio.wait_for(self.real.set(k, v, ex=ex), timeout=2.0)
            except: _memory_cache[k] = v
        async def incr(self, k):
            try: return await asyncio.wait_for(self.real.incr(k), timeout=2.0)
            except:
                v = int(_memory_cache.get(k) or 0) + 1
                _memory_cache[k] = str(v); return v
        async def decr(self, k):
            try: return await asyncio.wait_for(self.real.decr(k), timeout=2.0)
            except:
                v = int(_memory_cache.get(k) or 0) - 1
                _memory_cache[k] = str(v); return v
    
    redis_client = SafeRedis(redis_client)
