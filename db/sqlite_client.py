import asyncio
from datetime import datetime
from db.supabase_client import get_supabase_client

# This module now uses Supabase instead of a local SQLite file 
# to ensure data persistence on platforms like Render/Vercel.

async def save_research(user_id: str, query: str, format_type: str, result: str, download_url: str = None):
    """Saves a research result to the Supabase 'research_history' table."""
    try:
        def _insert():
            sb = get_supabase_client()
            sb.table("research_history").insert({
                "user_id": user_id,
                "query": query,
                "format": format_type,
                "result": result,
                "download_url": download_url,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
        
        await asyncio.to_thread(_insert)
    except Exception as e:
        print(f"⚠️ Failed to save history to Supabase: {e}")

async def get_history(user_id: str, limit: int = 20) -> list[dict]:
    """Fetch recent research history for a user from Supabase."""
    try:
        def _fetch():
            sb = get_supabase_client()
            response = sb.table("research_history") \
                .select("*") \
                .eq("user_id", user_id) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
            return response.data or []
        
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        print(f"⚠️ Failed to fetch history from Supabase: {e}")
        return []

async def delete_history_item(item_id: int, user_id: str):
    """Delete a specific history item from Supabase."""
    try:
        def _delete():
            sb = get_supabase_client()
            sb.table("research_history") \
                .delete() \
                .eq("id", item_id) \
                .eq("user_id", user_id) \
                .execute()
        
        await asyncio.to_thread(_delete)
    except Exception as e:
        print(f"⚠️ Failed to delete history from Supabase: {e}")
