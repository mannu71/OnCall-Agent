"""MCP Tools for alert management in CloudWatch Log Analyzer.

This module provides tools for creating, managing, and tracking alerts
generated during log analysis.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from functools import wraps

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.db_models import AlertModel, LogPatternModel, KnowledgeEntryModel

logger = logging.getLogger(__name__)


def handle_exceptions(func: Callable) -> Callable:
    """Decorator to handle exceptions in MCP tools.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            return {
                "error": True,
                "message": str(e)
            }
    return wrapper


# ============================================
# MCP TOOL FUNCTIONS
# ============================================

@handle_exceptions
async def create_alert(
    log_group: str,
    alert_type: str,
    severity: str,
    message: str,
    details: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create a new alert from log analysis.
    
    This tool creates alerts when anomalies or patterns are detected
    during log analysis.
    
    Args:
        log_group: CloudWatch log group where the issue was detected
        alert_type: Type of alert (anomaly, pattern_match, error_spike, correlation)
        severity: Alert severity (critical, high, medium, low, info)
        message: Human-readable alert message
        details: Additional details about the alert
        
    Returns:
        Created alert information
        
    Example:
        ```python
        result = await create_alert(
            log_group="/aws/lambda/my-function",
            alert_type="error_spike",
            severity="high",
            message="Error rate increased by 300% in the last 5 minutes",
            details={
                "baseline_rate": 10,
                "current_rate": 40,
                "deviation_factor": 4.0
            }
        )
        ```
    """
    async with AsyncSessionLocal() as session:
        alert = AlertModel(
            log_group=log_group,
            alert_type=alert_type,
            severity=severity,
            message=message,
            details=details,
            status="new",
            created_at=datetime.now(timezone.utc)
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        
        return {
            "success": True,
            "alert": {
                "id": alert.id,
                "log_group": alert.log_group,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "message": alert.message,
                "details": alert.details,
                "status": alert.status,
                "created_at": alert.created_at.isoformat() if alert.created_at else None
            }
        }


@handle_exceptions
async def get_alerts(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    log_group: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """Get alerts with optional filtering.
    
    This tool retrieves alerts from the database with various filters.
    
    Args:
        status: Filter by status (new, acknowledged, resolved, dismissed)
        severity: Filter by severity (critical, high, medium, low, info)
        log_group: Filter by log group
        limit: Maximum number of alerts to return
        
    Returns:
        List of matching alerts
        
    Example:
        ```python
        # Get all new high-severity alerts
        result = await get_alerts(status="new", severity="high")
        ```
    """
    async with AsyncSessionLocal() as session:
        query = select(AlertModel).order_by(AlertModel.created_at.desc()).limit(limit)
        
        if status:
            query = query.where(AlertModel.status == status)
        if severity:
            query = query.where(AlertModel.severity == severity)
        if log_group:
            query = query.where(AlertModel.log_group == log_group)
        
        result = await session.execute(query)
        alerts = result.scalars().all()
        
        return {
            "success": True,
            "alerts": [
                {
                    "id": alert.id,
                    "log_group": alert.log_group,
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "details": alert.details,
                    "status": alert.status,
                    "created_at": alert.created_at.isoformat() if alert.created_at else None,
                    "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                    "resolved_by": alert.resolved_by
                }
                for alert in alerts
            ],
            "count": len(alerts)
        }


@handle_exceptions
async def acknowledge_alert(
    alert_id: int,
    acknowledged_by: Optional[str] = None,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """Acknowledge an alert.
    
    This tool marks an alert as acknowledged, indicating it has been
    seen and is being investigated.
    
    Args:
        alert_id: ID of the alert to acknowledge
        acknowledged_by: User or system acknowledging the alert
        notes: Optional notes about the acknowledgment
        
    Returns:
        Updated alert information
        
    Example:
        ```python
        result = await acknowledge_alert(
            alert_id=123,
            acknowledged_by="oncall-engineer",
            notes="Investigating the error spike"
        )
        ```
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AlertModel).where(AlertModel.id == alert_id)
        )
        alert = result.scalar_one_or_none()
        
        if not alert:
            return {
                "success": False,
                "error": f"Alert {alert_id} not found"
            }
        
        alert.status = "acknowledged"
        if notes:
            details = alert.details or {}
            details["acknowledgment_notes"] = notes
            details["acknowledged_by"] = acknowledged_by
            details["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            alert.details = details
        
        await session.commit()
        await session.refresh(alert)
        
        return {
            "success": True,
            "alert": {
                "id": alert.id,
                "status": alert.status,
                "details": alert.details
            }
        }


@handle_exceptions
async def resolve_alert(
    alert_id: int,
    resolved_by: str,
    resolution: str
) -> Dict[str, Any]:
    """Resolve an alert.
    
    This tool marks an alert as resolved with a resolution description.
    
    Args:
        alert_id: ID of the alert to resolve
        resolved_by: User or system resolving the alert
        resolution: Description of how the alert was resolved
        
    Returns:
        Updated alert information
        
    Example:
        ```python
        result = await resolve_alert(
            alert_id=123,
            resolved_by="oncall-engineer",
            resolution="Fixed the underlying database connection issue"
        )
        ```
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AlertModel).where(AlertModel.id == alert_id)
        )
        alert = result.scalar_one_or_none()
        
        if not alert:
            return {
                "success": False,
                "error": f"Alert {alert_id} not found"
            }
        
        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = resolved_by
        
        details = alert.details or {}
        details["resolution"] = resolution
        alert.details = details
        
        await session.commit()
        await session.refresh(alert)
        
        return {
            "success": True,
            "alert": {
                "id": alert.id,
                "status": alert.status,
                "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
                "resolved_by": alert.resolved_by,
                "resolution": resolution
            }
        }


@handle_exceptions
async def get_alert_summary(
    time_range_hours: int = 24
) -> Dict[str, Any]:
    """Get a summary of alerts for dashboard display.
    
    This tool provides aggregated alert statistics for monitoring dashboards.
    
    Args:
        time_range_hours: Time range in hours to include in summary
        
    Returns:
        Alert summary with counts by severity, status, and type
        
    Example:
        ```python
        result = await get_alert_summary(time_range_hours=24)
        ```
    """
    async with AsyncSessionLocal() as session:
        # Get all alerts in time range
        cutoff_time = datetime.now(timezone.utc) - __import__('datetime').timedelta(hours=time_range_hours)
        
        result = await session.execute(
            select(AlertModel).where(AlertModel.created_at >= cutoff_time)
        )
        alerts = result.scalars().all()
        
        # Count by severity
        severity_counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0
        }
        
        # Count by status
        status_counts = {
            "new": 0,
            "acknowledged": 0,
            "resolved": 0,
            "dismissed": 0
        }
        
        # Count by type
        type_counts = {}
        
        # Count by log group
        log_group_counts = {}
        
        for alert in alerts:
            # Severity
            if alert.severity in severity_counts:
                severity_counts[alert.severity] += 1
            
            # Status
            if alert.status in status_counts:
                status_counts[alert.status] += 1
            
            # Type
            alert_type = alert.alert_type or "unknown"
            type_counts[alert_type] = type_counts.get(alert_type, 0) + 1
            
            # Log group
            log_group = alert.log_group or "unknown"
            log_group_counts[log_group] = log_group_counts.get(log_group, 0) + 1
        
        # Calculate average resolution time
        resolved_alerts = [a for a in alerts if a.status == "resolved" and a.resolved_at]
        avg_resolution_time = None
        if resolved_alerts:
            total_resolution_time = sum(
                (a.resolved_at - a.created_at).total_seconds()
                for a in resolved_alerts
                if a.resolved_at and a.created_at
            )
            avg_resolution_time = total_resolution_time / len(resolved_alerts)
        
        return {
            "success": True,
            "summary": {
                "total_alerts": len(alerts),
                "time_range_hours": time_range_hours,
                "by_severity": severity_counts,
                "by_status": status_counts,
                "by_type": type_counts,
                "by_log_group": log_group_counts,
                "average_resolution_time_seconds": avg_resolution_time,
                "new_critical": severity_counts["critical"] if status_counts["new"] > 0 else 0
            }
        }


@handle_exceptions
async def dismiss_alert(
    alert_id: int,
    dismissed_by: str,
    reason: str
) -> Dict[str, Any]:
    """Dismiss an alert as false positive or not actionable.
    
    Args:
        alert_id: ID of the alert to dismiss
        dismissed_by: User or system dismissing the alert
        reason: Reason for dismissal
        
    Returns:
        Updated alert information
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AlertModel).where(AlertModel.id == alert_id)
        )
        alert = result.scalar_one_or_none()
        
        if not alert:
            return {
                "success": False,
                "error": f"Alert {alert_id} not found"
            }
        
        alert.status = "dismissed"
        details = alert.details or {}
        details["dismissed_by"] = dismissed_by
        details["dismissal_reason"] = reason
        details["dismissed_at"] = datetime.now(timezone.utc).isoformat()
        alert.details = details
        
        await session.commit()
        
        return {
            "success": True,
            "alert": {
                "id": alert.id,
                "status": alert.status
            }
        }


@handle_exceptions
async def get_knowledge_entries(
    category: Optional[str] = None,
    limit: int = 50
) -> Dict[str, Any]:
    """Get knowledge entries from the knowledge base.

    This tool retrieves knowledge entries that can be matched against
    detected patterns for automated resolution suggestions.

    Args:
        category: Filter by category (database, network, authentication, etc.)
        limit: Maximum number of entries to return

    Returns:
        List of knowledge entries
    """
    async with AsyncSessionLocal() as session:
        query = select(KnowledgeEntryModel).limit(limit)

        if category:
            query = query.where(KnowledgeEntryModel.category == category)

        result = await session.execute(query)
        entries = result.scalars().all()

        return {
            "success": True,
            "entries": [
                {
                    "id": entry.id,
                    "title": entry.title,
                    "description": entry.description,
                    "symptoms": entry.symptoms,
                    "solution": entry.solution,
                    "category": entry.category,
                    "source": entry.source,
                }
                for entry in entries
            ],
            "count": len(entries)
        }


# Backward-compat alias — remove once all callers are updated
get_known_issues = get_knowledge_entries


@handle_exceptions
async def create_knowledge_entry(
    title: str,
    description: str,
    symptoms: List[str],
    solution: str,
    category: str
) -> Dict[str, Any]:
    """Create a new knowledge entry in the knowledge base.

    This tool adds a knowledge entry that can be used for pattern matching
    and automated resolution suggestions.

    Args:
        title: Entry title
        description: Detailed description
        symptoms: List of symptoms/signatures to match
        solution: Resolution steps
        category: Entry category

    Returns:
        Created knowledge entry
    """
    async with AsyncSessionLocal() as session:
        entry = KnowledgeEntryModel(
            title=title,
            description=description,
            symptoms=symptoms,
            solution=solution,
            category=category,
            source="manual",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

        return {
            "success": True,
            "entry": {
                "id": entry.id,
                "title": entry.title,
                "description": entry.description,
                "symptoms": entry.symptoms,
                "solution": entry.solution,
                "category": entry.category,
                "source": entry.source,
            }
        }


# Backward-compat alias
create_known_issue = create_knowledge_entry