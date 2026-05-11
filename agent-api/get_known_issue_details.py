"""Get full details of the Known Issue."""
import asyncio
import json
from app.core.database import AsyncSessionLocal
from sqlalchemy import text

async def get_details():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text('''
            SELECT id, title, description, symptoms, solution, category, source, created_at 
            FROM known_issues 
            ORDER BY created_at DESC 
            LIMIT 1
        '''))
        row = result.fetchone()
        
        if row:
            print("=" * 80)
            print("KNOWN ISSUE DETAILS")
            print("=" * 80)
            print(f"\nID: {row[0]}")
            print(f"Title: {row[1]}")
            print(f"Category: {row[5]}")
            print(f"Source: {row[6]}")
            print(f"Created: {row[7]}")
            
            print("\n" + "=" * 80)
            print("DESCRIPTION")
            print("=" * 80)
            print(row[2])
            
            print("\n" + "=" * 80)
            print("SYMPTOMS")
            print("=" * 80)
            if row[3]:
                if isinstance(row[3], str):
                    symptoms = json.loads(row[3])
                else:
                    symptoms = row[3]
                for i, symptom in enumerate(symptoms, 1):
                    print(f"{i}. {symptom}")
            else:
                print("None")
            
            print("\n" + "=" * 80)
            print("SOLUTION")
            print("=" * 80)
            if row[4]:
                print(row[4])
            else:
                print("None")
            
            print("\n" + "=" * 80)
        else:
            print("No Known Issues found")

asyncio.run(get_details())
