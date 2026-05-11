"""Verify consistency across all schema definitions."""
import re
from pathlib import Path

def check_file_dimensions(filepath, pattern):
    """Check vector dimensions in a file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            matches = re.findall(pattern, content)
            return matches
    except Exception as e:
        return [f"Error: {e}"]

def main():
    print("=" * 70)
    print("SCHEMA CONSISTENCY VERIFICATION")
    print("=" * 70)
    
    # Check init-db.sql
    print("\n1. Checking init-db.sql:")
    init_db_path = Path("init-db.sql")
    if init_db_path.exists():
        matches = check_file_dimensions(init_db_path, r'vector\((\d+)\)')
        if matches:
            print(f"   Found vector dimensions: {matches}")
            if all(dim == '1024' for dim in matches):
                print("   ✅ All vectors use 1024 dimensions")
            else:
                print("   ❌ Inconsistent dimensions found!")
        else:
            print("   ⚠️  No vector dimensions found")
    else:
        print("   ❌ File not found")
    
    # Check db_models.py
    print("\n2. Checking app/models/db_models.py:")
    models_path = Path("app/models/db_models.py")
    if models_path.exists():
        matches = check_file_dimensions(models_path, r'Vector\((\d+)\)')
        if matches:
            print(f"   Found vector dimensions: {matches}")
            if all(dim == '1024' for dim in matches):
                print("   ✅ All vectors use 1024 dimensions")
            else:
                print("   ❌ Inconsistent dimensions found!")
        else:
            print("   ⚠️  No vector dimensions found")
    else:
        print("   ❌ File not found")
    
    # Check migration file
    print("\n3. Checking migrations/update_embedding_dimensions_to_1024.sql:")
    migration_path = Path("migrations/update_embedding_dimensions_to_1024.sql")
    if migration_path.exists():
        matches = check_file_dimensions(migration_path, r'vector\((\d+)\)')
        if matches:
            print(f"   Found vector dimensions: {matches}")
            if all(dim == '1024' for dim in matches):
                print("   ✅ All vectors use 1024 dimensions")
            else:
                print("   ❌ Inconsistent dimensions found!")
        else:
            print("   ⚠️  No vector dimensions found")
    else:
        print("   ❌ File not found")
    
    # Check symptoms field type
    print("\n4. Checking symptoms field consistency:")
    
    # Check init-db.sql
    if init_db_path.exists():
        with open(init_db_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'symptoms JSON' in content:
                print("   ✅ init-db.sql uses JSON for symptoms")
            elif 'symptoms TEXT[]' in content:
                print("   ❌ init-db.sql uses TEXT[] for symptoms (should be JSON)")
            else:
                print("   ⚠️  Could not determine symptoms type in init-db.sql")
    
    # Check db_models.py
    if models_path.exists():
        with open(models_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'symptoms = Column(JSON)' in content:
                print("   ✅ db_models.py uses JSON for symptoms")
            else:
                print("   ⚠️  Could not determine symptoms type in db_models.py")
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    print("\nAll schema files should use:")
    print("  • Vector dimensions: 1024")
    print("  • Symptoms field: JSON")
    print("\nThis ensures compatibility with Amazon Titan Embed Text v2")

if __name__ == "__main__":
    main()
