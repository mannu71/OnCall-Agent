"""Check the Known Issue that was created."""
import asyncio
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text('''
            SELECT id, title, description, category, source, created_at 
            FROM known_issues 
            ORDER BY created_at DESC 
            LIMIT 1
        '''))
        row = result.fetchone()
        
        if row:
            print("=" * 70)
            print("MOST RECENT KNOWN ISSUE")
            print("=" * 70)
            print(f"\nID: {row[0]}")
            print(f"Title: {row[1]}")
            print(f"Category: {row[3]}")
            print(f"Source: {row[4]}")
            print(f"Created: {row[5]}")
            print(f"\nDescription:")
            print(row[2][:500] if len(row[2]) > 500 else row[2])
            if len(row[2]) > 500:
                print("... (truncated)")
        else:
            print("No Known Issues found")

asyncio.run(check())
