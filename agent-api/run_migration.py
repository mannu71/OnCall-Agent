#!/usr/bin/env python3
"""Run database migration to add use_for_embeddings column."""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.database import AsyncSessionLocal
from sqlalchemy import text


async def run_migration():
    """Run the migration to add use_for_embeddings column."""
    print("Running migration: add use_for_embeddings to llm_configs")
    print("=" * 60)
    
    try:
        async with AsyncSessionLocal() as session:
            # Execute migrations one at a time
            print("Adding column...")
            await session.execute(text(
                "ALTER TABLE llm_configs "
                "ADD COLUMN IF NOT EXISTS use_for_embeddings BOOLEAN DEFAULT FALSE"
            ))
            
            print("Adding comment...")
            await session.execute(text(
                "COMMENT ON COLUMN llm_configs.use_for_embeddings IS "
                "'Whether this LLM configuration should be used for generating embeddings'"
            ))
            
            await session.commit()
            
            # Verify the column was added
            print("Verifying...")
            result = await session.execute(text("""
                SELECT column_name, data_type, column_default
                FROM information_schema.columns
                WHERE table_name = 'llm_configs' 
                AND column_name = 'use_for_embeddings'
            """))
            
            row = result.fetchone()
            if row:
                print(f"✅ Column added successfully!")
                print(f"   Column: {row[0]}")
                print(f"   Type: {row[1]}")
                print(f"   Default: {row[2]}")
            else:
                print("⚠️  Column may already exist or migration failed")
                
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("=" * 60)
    print("Migration completed!")
    return True


if __name__ == "__main__":
    success = asyncio.run(run_migration())
    sys.exit(0 if success else 1)
