"""Verify database schema for embeddings."""
import asyncio

async def verify_schema():
    """Check the current database schema."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import text
    
    print("=" * 70)
    print("DATABASE SCHEMA VERIFICATION")
    print("=" * 70)
    
    async with AsyncSessionLocal() as session:
        # Check known_issues table
        print("\n1. Known Issues Table:")
        result = await session.execute(text("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name = 'known_issues' AND column_name = 'embedding'
        """))
        row = result.fetchone()
        
        if row:
            print(f"   Column: {row[0]}")
            print(f"   Data Type: {row[1]}")
            print(f"   UDT Name: {row[2]}")
            
            # Try to get vector dimensions
            try:
                result = await session.execute(text("""
                    SELECT atttypmod 
                    FROM pg_attribute 
                    WHERE attrelid = 'known_issues'::regclass 
                    AND attname = 'embedding'
                """))
                typmod = result.scalar()
                if typmod and typmod > 0:
                    dimensions = typmod - 4  # pgvector stores dimensions as typmod - 4
                    print(f"   Dimensions: {dimensions}")
                else:
                    print(f"   Dimensions: Could not determine (typmod={typmod})")
            except Exception as e:
                print(f"   Dimensions: Error - {e}")
        else:
            print("   ❌ Embedding column not found!")
        
        # Check log_patterns table
        print("\n2. Log Patterns Table:")
        result = await session.execute(text("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns 
            WHERE table_name = 'log_patterns' AND column_name = 'embedding'
        """))
        row = result.fetchone()
        
        if row:
            print(f"   Column: {row[0]}")
            print(f"   Data Type: {row[1]}")
            print(f"   UDT Name: {row[2]}")
            
            # Try to get vector dimensions
            try:
                result = await session.execute(text("""
                    SELECT atttypmod 
                    FROM pg_attribute 
                    WHERE attrelid = 'log_patterns'::regclass 
                    AND attname = 'embedding'
                """))
                typmod = result.scalar()
                if typmod and typmod > 0:
                    dimensions = typmod - 4
                    print(f"   Dimensions: {dimensions}")
                else:
                    print(f"   Dimensions: Could not determine (typmod={typmod})")
            except Exception as e:
                print(f"   Dimensions: Error - {e}")
        else:
            print("   ❌ Embedding column not found!")
        
        # Check data counts
        print("\n3. Data Counts:")
        result = await session.execute(text("SELECT COUNT(*) FROM known_issues"))
        known_issues_count = result.scalar()
        print(f"   Known Issues: {known_issues_count}")
        
        result = await session.execute(text("SELECT COUNT(*) FROM log_patterns"))
        log_patterns_count = result.scalar()
        print(f"   Log Patterns: {log_patterns_count}")
        
        result = await session.execute(text("SELECT COUNT(*) FROM analysis_history"))
        analysis_count = result.scalar()
        print(f"   Analysis History: {analysis_count}")
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(verify_schema())
