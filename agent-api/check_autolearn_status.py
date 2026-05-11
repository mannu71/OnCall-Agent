"""Check auto-learn status for recent workflow executions."""
import asyncio
import json
from datetime import datetime

async def check_recent_executions():
    """Check recent workflow executions and their logs."""
    from app.repositories.db_repository import db_repository
    
    print("=" * 70)
    print("CHECKING AUTO-LEARN STATUS FOR RECENT WORKFLOWS")
    print("=" * 70)
    
    # Get recent executions
    executions = await db_repository.list_executions(limit=10)
    
    if not executions:
        print("\n❌ No recent executions found")
        return
    
    print(f"\n📊 Found {len(executions)} recent executions\n")
    
    for i, execution in enumerate(executions, 1):
        print(f"\n{'='*70}")
        print(f"EXECUTION #{i}")
        print(f"{'='*70}")
        print(f"ID: {execution['id']}")
        print(f"Workflow: {execution['workflow_name']}")
        print(f"Status: {execution['status']}")
        print(f"Started: {execution['started_at']}")
        print(f"Duration: {execution.get('duration_ms', 'N/A')} ms")
        
        # Check logs for auto-learn mentions
        logs = execution.get('logs', [])
        if logs:
            print(f"\n📝 Logs ({len(logs)} entries):")
            
            auto_learn_logs = []
            embedding_logs = []
            error_logs = []
            
            for log in logs:
                log_msg = log.get('message', '') if isinstance(log, dict) else str(log)
                log_level = log.get('level', 'INFO') if isinstance(log, dict) else 'INFO'
                
                if 'auto_learn' in log_msg.lower():
                    auto_learn_logs.append((log_level, log_msg))
                if 'embedding' in log_msg.lower():
                    embedding_logs.append((log_level, log_msg))
                if log_level in ('ERROR', 'WARNING') and ('unsupported' in log_msg.lower() or 'expired' in log_msg.lower()):
                    error_logs.append((log_level, log_msg))
            
            if auto_learn_logs:
                print(f"\n  🧠 Auto-Learn Logs ({len(auto_learn_logs)}):")
                for level, msg in auto_learn_logs:
                    icon = "✅" if level == "INFO" else "⚠️" if level == "WARNING" else "❌"
                    print(f"    {icon} [{level}] {msg[:100]}...")
            
            if embedding_logs:
                print(f"\n  🔢 Embedding Logs ({len(embedding_logs)}):")
                for level, msg in embedding_logs:
                    icon = "✅" if level == "INFO" else "⚠️" if level == "WARNING" else "❌"
                    print(f"    {icon} [{level}] {msg[:100]}...")
            
            if error_logs:
                print(f"\n  ❌ Error/Warning Logs ({len(error_logs)}):")
                for level, msg in error_logs:
                    print(f"    ⚠️ [{level}] {msg[:150]}...")
            
            if not auto_learn_logs and not embedding_logs and not error_logs:
                print("    ℹ️  No auto-learn or embedding-related logs found")
        else:
            print("\n  ℹ️  No logs available")

async def check_llm_embedding_config():
    """Check which LLM is configured for embeddings."""
    from app.repositories.db_repository import db_repository
    
    print(f"\n{'='*70}")
    print("EMBEDDING CONFIGURATION")
    print(f"{'='*70}")
    
    llm_configs = await db_repository.list_llm_configs()
    
    embedding_llm = None
    for name, config in llm_configs.items():
        if config.get('use_for_embeddings'):
            embedding_llm = (name, config)
            break
    
    if embedding_llm:
        name, config = embedding_llm
        print(f"\n✅ Embedding LLM Configured:")
        print(f"   Name: {name}")
        print(f"   Provider: {config.get('provider')}")
        print(f"   Model: {config.get('model')}")
        print(f"   Region: {config.get('region', 'N/A')}")
    else:
        print("\n⚠️  No LLM configured for embeddings")
        print("   Auto-learn will use default settings from config")

async def check_knowledge_base():
    """Check if knowledge base has any entries."""
    from app.services.knowledge_base import KnowledgeBaseService
    
    print(f"\n{'='*70}")
    print("KNOWLEDGE BASE STATUS")
    print(f"{'='*70}")
    
    kb_service = KnowledgeBaseService()
    
    try:
        # Check known issues
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import select, func
        from app.models.db_models import KnownIssueModel, LogPatternModel
        
        async with AsyncSessionLocal() as session:
            # Count known issues
            result = await session.execute(select(func.count(KnownIssueModel.id)))
            known_issues_count = result.scalar()
            
            # Count log patterns
            result = await session.execute(select(func.count(LogPatternModel.id)))
            log_patterns_count = result.scalar()
            
            print(f"\n📚 Knowledge Base Entries:")
            print(f"   Known Issues: {known_issues_count}")
            print(f"   Log Patterns: {log_patterns_count}")
            
            if known_issues_count > 0:
                # Get recent known issues
                result = await session.execute(
                    select(KnownIssueModel)
                    .order_by(KnownIssueModel.created_at.desc())
                    .limit(5)
                )
                recent_issues = result.scalars().all()
                
                print(f"\n   Recent Known Issues:")
                for issue in recent_issues:
                    print(f"     • {issue.title} ({issue.category}) - Source: {issue.source}")
    
    except Exception as e:
        print(f"\n❌ Error checking knowledge base: {e}")

async def test_embedding_service():
    """Test if embedding service is working."""
    print(f"\n{'='*70}")
    print("TESTING EMBEDDING SERVICE")
    print(f"{'='*70}")
    
    try:
        from app.services.knowledge_base import KnowledgeBaseService
        
        kb_service = KnowledgeBaseService()
        embedding_service = await kb_service._get_embedding_service()
        
        print(f"\n✅ Embedding Service Initialized:")
        print(f"   Provider: {embedding_service.provider}")
        print(f"   Model: {embedding_service.model}")
        print(f"   Region: {embedding_service.region}")
        
        # Try to generate a test embedding
        print(f"\n🧪 Testing embedding generation...")
        test_text = "Test error: Connection timeout"
        
        try:
            embedding = await embedding_service.generate_embedding(test_text)
            print(f"   ✅ Successfully generated embedding (dimension: {len(embedding)})")
            return True
        except Exception as e:
            print(f"   ❌ Failed to generate embedding: {e}")
            return False
            
    except Exception as e:
        print(f"\n❌ Error initializing embedding service: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Run all checks."""
    print("\n🔍 AUTO-LEARN STATUS CHECK\n")
    
    await check_llm_embedding_config()
    await check_knowledge_base()
    embedding_works = await test_embedding_service()
    await check_recent_executions()
    
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    
    if embedding_works:
        print("\n✅ Embedding service is working")
        print("✅ Auto-learn should be functional")
        print("\nℹ️  If auto-learn is not running, check:")
        print("   1. Workflow has ReactStrategy enabled")
        print("   2. Backend has been restarted after provider fix")
        print("   3. Workflow execution completed successfully")
    else:
        print("\n⚠️  Embedding service has issues")
        print("⚠️  Auto-learn may not be working")
        print("\nℹ️  Action needed:")
        print("   1. Check LLM configuration for embeddings")
        print("   2. Verify AWS credentials (if using Bedrock)")
        print("   3. Restart backend to apply fixes")

if __name__ == "__main__":
    asyncio.run(main())
