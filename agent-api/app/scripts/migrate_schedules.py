"""
Migration script to move schedules data to workflows table.

This script migrates data from the legacy 'schedules' table to the 
workflows table's 'schedule' and 'enabled' columns.

The schedules table was removed from init-db.sql and the schedule 
functionality has been consolidated into the workflows table.
"""

import asyncio
import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy import text
from app.core.database import AsyncSessionLocal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def check_schedules_table_exists(session) -> bool:
    """
    Check if the schedules table exists in the database.
    
    Args:
        session: Database session
        
    Returns:
        True if the table exists, False otherwise
    """
    result = await session.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'schedules'
        );
    """))
    return result.scalar()


async def get_schedules_data(session) -> List[Dict[str, Any]]:
    """
    Get all schedules from the schedules table.
    
    Args:
        session: Database session
        
    Returns:
        List of schedule records
    """
    result = await session.execute(text("""
        SELECT 
            id, name, workflow_name, cron_expression, 
            timezone, enabled, last_run, next_run, 
            created_at, updated_at
        FROM schedules
        ORDER BY id
    """))
    
    columns = result.keys()
    schedules = []
    for row in result:
        schedule = dict(zip(columns, row))
        schedules.append(schedule)
    
    return schedules


async def get_workflow_by_name(session, workflow_name: str) -> Optional[Dict[str, Any]]:
    """
    Get a workflow by its name.
    
    Args:
        session: Database session
        workflow_name: Name of the workflow
        
    Returns:
        Workflow record or None if not found
    """
    result = await session.execute(text("""
        SELECT id, name, schedule, enabled
        FROM workflows
        WHERE name = :name
    """), {"name": workflow_name})
    
    row = result.fetchone()
    if row:
        columns = result.keys()
        return dict(zip(columns, row))
    return None


async def update_workflow_schedule(
    session, 
    workflow_id: int, 
    cron_expression: str, 
    enabled: bool
) -> bool:
    """
    Update a workflow's schedule and enabled fields.
    
    Args:
        session: Database session
        workflow_id: ID of the workflow to update
        cron_expression: Cron expression for the schedule
        enabled: Whether the schedule is enabled
        
    Returns:
        True if update was successful
    """
    await session.execute(text("""
        UPDATE workflows
        SET schedule = :schedule,
            enabled = :enabled,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
    """), {
        "id": workflow_id,
        "schedule": cron_expression,
        "enabled": enabled
    })
    return True


async def drop_schedules_table(session) -> bool:
    """
    Drop the schedules table.
    
    Args:
        session: Database session
        
    Returns:
        True if the table was dropped successfully
    """
    await session.execute(text("DROP TABLE IF EXISTS schedules"))
    return True


async def migrate_schedules(
    drop_table: bool = False,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Migrate data from schedules table to workflows.schedule column.
    
    Args:
        drop_table: If True, drop the schedules table after successful migration
        dry_run: If True, preview changes without applying them
        
    Returns:
        Migration results with stats
    """
    result = {
        "success": False,
        "dry_run": dry_run,
        "schedules_table_exists": False,
        "total_schedules": 0,
        "migrated": 0,
        "skipped": 0,
        "failed": 0,
        "not_found": 0,
        "errors": [],
        "details": []
    }
    
    async with AsyncSessionLocal() as session:
        try:
            # Check if schedules table exists
            table_exists = await check_schedules_table_exists(session)
            result["schedules_table_exists"] = table_exists
            
            if not table_exists:
                logger.info("Schedules table does not exist - nothing to migrate")
                result["success"] = True
                result["message"] = "Schedules table does not exist - migration not needed"
                return result
            
            # Get all schedules
            schedules = await get_schedules_data(session)
            result["total_schedules"] = len(schedules)
            
            if not schedules:
                logger.info("No schedules found in the schedules table")
                result["success"] = True
                result["message"] = "No schedules to migrate"
                
                # Optionally drop the empty table
                if drop_table and not dry_run:
                    await drop_schedules_table(session)
                    await session.commit()
                    result["table_dropped"] = True
                    logger.info("Dropped empty schedules table")
                
                return result
            
            logger.info(f"Found {len(schedules)} schedules to migrate")
            
            # Process each schedule
            for schedule in schedules:
                schedule_detail = {
                    "schedule_id": schedule["id"],
                    "schedule_name": schedule["name"],
                    "workflow_name": schedule["workflow_name"],
                    "cron_expression": schedule["cron_expression"],
                    "enabled": schedule["enabled"]
                }
                
                try:
                    # Find the corresponding workflow
                    workflow = await get_workflow_by_name(
                        session, 
                        schedule["workflow_name"]
                    )
                    
                    if not workflow:
                        logger.warning(
                            f"Workflow '{schedule['workflow_name']}' not found for "
                            f"schedule '{schedule['name']}'"
                        )
                        result["not_found"] += 1
                        schedule_detail["status"] = "workflow_not_found"
                        result["details"].append(schedule_detail)
                        continue
                    
                    # Check if workflow already has a schedule
                    if workflow["schedule"]:
                        logger.info(
                            f"Workflow '{workflow['name']}' already has schedule: "
                            f"{workflow['schedule']}. Overriding with '{schedule['cron_expression']}'"
                        )
                        schedule_detail["previous_schedule"] = workflow["schedule"]
                    
                    if dry_run:
                        logger.info(
                            f"[DRY RUN] Would update workflow '{workflow['name']}' "
                            f"with schedule '{schedule['cron_expression']}' "
                            f"(enabled={schedule['enabled']})"
                        )
                        result["migrated"] += 1
                        schedule_detail["status"] = "dry_run"
                    else:
                        # Update the workflow
                        await update_workflow_schedule(
                            session,
                            workflow["id"],
                            schedule["cron_expression"],
                            schedule["enabled"]
                        )
                        logger.info(
                            f"Migrated schedule '{schedule['name']}' to "
                            f"workflow '{workflow['name']}'"
                        )
                        result["migrated"] += 1
                        schedule_detail["status"] = "migrated"
                    
                    result["details"].append(schedule_detail)
                    
                except Exception as e:
                    logger.error(
                        f"Error migrating schedule '{schedule['name']}': {e}"
                    )
                    result["failed"] += 1
                    schedule_detail["status"] = "error"
                    schedule_detail["error"] = str(e)
                    result["details"].append(schedule_detail)
                    result["errors"].append({
                        "schedule_id": schedule["id"],
                        "schedule_name": schedule["name"],
                        "error": str(e)
                    })
            
            # Commit changes if not dry run
            if not dry_run:
                await session.commit()
                
                # Drop the table if requested and no failures
                if drop_table and result["failed"] == 0:
                    await drop_schedules_table(session)
                    await session.commit()
                    result["table_dropped"] = True
                    logger.info("Dropped schedules table after successful migration")
            
            result["success"] = result["failed"] == 0
            
        except Exception as e:
            logger.error(f"Migration failed with error: {e}")
            result["success"] = False
            result["error"] = str(e)
            await session.rollback()
    
    return result


def print_migration_report(result: Dict[str, Any]) -> None:
    """Print a formatted migration report."""
    print("\n" + "=" * 60)
    print("SCHEDULES MIGRATION REPORT")
    print("=" * 60)
    
    if result.get("dry_run"):
        print("\n*** DRY RUN MODE - No changes were applied ***\n")
    
    print(f"Schedules table exists: {result.get('schedules_table_exists', False)}")
    print(f"Total schedules found: {result.get('total_schedules', 0)}")
    print("-" * 40)
    print(f"Migrated: {result.get('migrated', 0)}")
    print(f"Skipped: {result.get('skipped', 0)}")
    print(f"Workflow not found: {result.get('not_found', 0)}")
    print(f"Failed: {result.get('failed', 0)}")
    
    if result.get("table_dropped"):
        print("-" * 40)
        print("Schedules table dropped: Yes")
    
    if result.get("errors"):
        print("\nERRORS:")
        for error in result["errors"]:
            print(f"  - Schedule '{error['schedule_name']}': {error['error']}")
    
    if result.get("details"):
        print("\nDETAILS:")
        for detail in result["details"]:
            status = detail.get("status", "unknown")
            workflow = detail.get("workflow_name", "N/A")
            cron = detail.get("cron_expression", "N/A")
            print(f"  - [{status}] Schedule '{detail.get('schedule_name')}' "
                  f"-> Workflow '{workflow}' (cron: {cron})")
    
    print("=" * 60)
    print(f"Result: {'SUCCESS' if result.get('success') else 'FAILED'}")
    print("=" * 60 + "\n")


async def main():
    """Main entry point for migration script."""
    parser = argparse.ArgumentParser(
        description="Migrate schedules data to workflows table"
    )
    parser.add_argument(
        "--drop-table",
        action="store_true",
        help="Drop the schedules table after successful migration"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format"
    )
    
    args = parser.parse_args()
    
    # Run migration
    result = await migrate_schedules(
        drop_table=args.drop_table,
        dry_run=args.dry_run
    )
    
    # Output results
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_migration_report(result)
    
    # Exit with appropriate code
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    asyncio.run(main())
