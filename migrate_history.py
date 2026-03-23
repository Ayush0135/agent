import sqlite3
import os
import asyncio
from datetime import datetime
from db.supabase_client import get_supabase_client
from pathlib import Path

# This script migrates your existing SQLite research history into your new Supabase table.
# It will restore all your previous queries and results.

DB_PATH = Path(__file__).parent / "local_research.db"

async def migrate():
    if not DB_PATH.exists():
        print("❌ SQLite file not found.")
        return

    print("🔄 Connecting to local SQLite...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM research_history")
    rows = cursor.fetchall()
    conn.close()

    print(f"📊 Found {len(rows)} history items to migrate.")
    sb = get_supabase_client()
    
    payloads = []
    for r in rows:
        # Map SQLite row to Supabase schema
        payloads.append({
            "user_id": r["user_id"],
            "query": r["query"],
            "format": r["format"],
            "result": r["result"],
            "download_url": r["download_url"],
            "created_at": r["created_at"]
        })

    if not payloads:
        print("✅ No data to migrate.")
        return

    print(f"📤 Uploading {len(payloads)} items to Supabase...")
    # Insert in chunks to avoid size limits
    batch_size = 10
    for i in range(0, len(payloads), batch_size):
        chunk = payloads[i:i + batch_size]
        sb.table("research_history").insert(chunk).execute()
        print(f"   - Migrated {i + len(chunk)} / {len(payloads)}")

    print("✨ Migration complete! Your history is now restored in Supabase.")

if __name__ == "__main__":
    asyncio.run(migrate())
