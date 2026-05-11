"""Check what's in the knowledge base in detail."""
import asyncio
from datetime import datetime

async def check_knowledge_base():
    """Check knowledge base contents in detail."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select, func
    from app.models.db_models import KnownIssueModel, LogPatternModel, AnalysisHistoryModel
    
    print("=" * 70)
    print("KNOWLEDGE BASE DETAILED REPORT")
    print("=" * 70)
    
    async with AsyncSessionLocal() as session:
        # Check Known Issues
        result = await session.execute(select(func.count(KnownIssueModel.id)))
        known_issues_count = result.scalar()
        
        print(f"\n📚 Known Issues: {known_issues_count}")
        
        if known_issues_count > 0:
            result = await session.execute(
                select(KnownIssueModel)
                .order_by(KnownIssueModel.created_at.desc())
                .limit(10)
            )
            issues = result.scalars().all()
            
            for i, issue in enumerate(issues, 1):
                print(f"\n  Issue #{i}:")
                print(f"    Title: {issue.title}")
                print(f"    Category: {issue.category}")
                print(f"    Source: {issue.source}")
                print(f"    Created: {issue.created_at}")
                print(f"    Description: {issue.description[:100]}...")
                if issue.symptoms:
                    print(f"    Symptoms: {len(issue.symptoms)} symptom(s)")
                print(f"    Solution: {issue.solution[:100]}...")
        
        # Check Log Patterns
        result = await session.execute(select(func.count(LogPatternModel.id)))
        log_patterns_count = result.scalar()
        
        print(f"\n📊 Log Patterns: {log_patterns_count}")
        
        if log_patterns_count > 0:
            result = await session.execute(
                select(LogPatternModel)
                .order_by(LogPatternModel.created_at.desc())
                .limit(10)
            )
            patterns = result.scalars().all()
            
            for i, pattern in enumerate(patterns, 1):
                print(f"\n  Pattern #{i}:")
                print(f"    Name: {pattern.name}")
                print(f"    Type: {pattern.pattern_type}")
                print(f"    Severity: {pattern.severity}")
                print(f"    Pattern: {pattern.pattern[:100]}...")
                print(f"    Created: {pattern.created_at}")
        
        # Check Analysis History
        result = await session.execute(select(func.count(AnalysisHistoryModel.id)))
        analysis_count = result.scalar()
        
        print(f"\n📈 Analysis History: {analysis_count}")
        
        if analysis_count > 0:
            result = await session.execute(
                select(AnalysisHistoryModel)
                .order_by(AnalysisHistoryModel.start_time.desc())
                .limit(10)
            )
            analyses = result.scalars().all()
            
            for i, analysis in enumerate(analyses, 1):
                print(f"\n  Analysis #{i}:")
                print(f"    Log Group: {analysis.log_group}")
                print(f"    Type: {analysis.analysis_type}")
                print(f"    Start: {analysis.start_time}")
                print(f"    End: {analysis.end_time}")
                print(f"    Anomalies Found: {analysis.anomalies_found}")
                print(f"    Patterns Matched: {analysis.patterns_matched}")
                print(f"    Summary: {analysis.summary[:100]}...")
    
    print("\n" + "=" * 70)
    print("WHAT AUTO-LEARN DOES")
    print("=" * 70)
    
    print("""
Auto-learn runs at the end of each workflow execution and:

1. 📝 Records Analysis History
   - Logs the execution details
   - Tracks anomalies found
   - Records patterns matched
   - Stores execution summary

2. 🔍 Detects Resolutions
   - Looks for resolution keywords in final answer
   - Keywords: "resolved", "fixed", "solution", "workaround"
   - If found, creates a Known Issue entry

3. 💾 Creates Known Issues
   - Title: User query (first 120 chars)
   - Description: Final answer (first 1000 chars)
   - Symptoms: [User query]
   - Solution: Final answer (first 2000 chars)
   - Category: "agent_discovered"
   - Source: "agent"

4. 🔢 Generates Embeddings
   - Creates vector embeddings for the issue
   - Stores in pgvector for similarity search
   - Enables future workflows to find similar issues

5. 🚨 Tracks Tool Failures
   - Logs any tool execution failures
   - Helps identify problematic tools

Benefits:
- Future workflows can search for similar issues
- Reduces redundant analysis
- Builds organizational knowledge
- Improves over time
""")
    
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    if known_issues_count > 0 or analysis_count > 0:
        print("\n✅ Auto-learn HAS been working!")
        print(f"   - {known_issues_count} known issue(s) discovered")
        print(f"   - {analysis_count} analysis record(s)")
        print(f"   - {log_patterns_count} log pattern(s)")
    else:
        print("\n⚠️  Auto-learn has NOT created any entries yet")
        print("   Possible reasons:")
        print("   1. All executions failed due to expired credentials")
        print("   2. No resolutions detected in final answers")
        print("   3. Container needs restart to use new config")

if __name__ == "__main__":
    asyncio.run(check_knowledge_base())
