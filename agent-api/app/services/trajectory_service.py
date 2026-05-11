"""Trajectory service for storing and replaying conversation trajectories.

This service implements trajectory storage for debugging and analysis of agent executions.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.db_models import TrajectoryModel

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryEntry:
    """Single trajectory entry."""
    trajectory_id: str
    execution_id: str
    timestamp: datetime
    model: str
    messages: List[Dict[str, Any]]
    tool_calls: List[Dict[str, Any]]
    completed: bool
    metadata: Dict[str, Any]


class TrajectoryService:
    """Store and retrieve conversation trajectories."""
    
    def __init__(self, session: Optional[AsyncSession] = None):
        """Initialize trajectory service.
        
        Args:
            session: Optional database session for testing
        """
        self._session = session
    
    async def save_trajectory(
        self,
        execution_id: str,
        messages: List[Dict[str, Any]],
        model: str,
        completed: bool,
        metadata: Optional[Dict[str, Any]] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        session: Optional[AsyncSession] = None,
    ) -> str:
        """Save trajectory to storage.
        
        Converts reasoning scratchpad tags (thinking → think) and stores
        the trajectory in JSONB format with metadata.
        
        Args:
            execution_id: Execution identifier
            messages: List of conversation messages
            model: Model name used
            completed: Whether execution completed successfully
            metadata: Optional metadata dictionary
            tool_calls: Optional list of tool calls made
            
        Returns:
            trajectory_id: Unique identifier for the saved trajectory
        """
        # Generate unique trajectory ID
        trajectory_id = f"traj_{uuid.uuid4().hex[:16]}"
        
        # Convert reasoning scratchpad tags
        converted_messages = self._convert_scratchpad_tags(messages)
        
        # Prepare metadata
        trajectory_metadata = metadata or {}
        trajectory_metadata["saved_at"] = datetime.now(timezone.utc).isoformat()
        
        # Use provided session or create new one
        use_session = session or self._session
        
        if use_session:
            # Use provided session (for testing) - don't commit, let caller handle it
            trajectory_model = TrajectoryModel(
                trajectory_id=trajectory_id,
                execution_id=execution_id,
                model=model,
                messages=converted_messages,
                tool_calls=tool_calls or [],
                completed=completed,
                trajectory_metadata=trajectory_metadata,
                created_at=datetime.now(timezone.utc)
            )
            use_session.add(trajectory_model)
            await use_session.flush()  # Flush to get ID without committing
        else:
            # Create new session (for production)
            async with AsyncSessionLocal() as new_session:
                trajectory_model = TrajectoryModel(
                    trajectory_id=trajectory_id,
                    execution_id=execution_id,
                    model=model,
                    messages=converted_messages,
                    tool_calls=tool_calls or [],
                    completed=completed,
                    trajectory_metadata=trajectory_metadata,
                    created_at=datetime.now(timezone.utc)
                )
                new_session.add(trajectory_model)
                await new_session.commit()
                await new_session.refresh(trajectory_model)
        
        logger.info(
            f"Saved trajectory {trajectory_id} for execution {execution_id} "
            f"with {len(converted_messages)} messages"
        )
        
        return trajectory_id
    
    async def load_trajectory(
        self,
        trajectory_id: str,
        session: Optional[AsyncSession] = None,
    ) -> Optional[TrajectoryEntry]:
        """Load a stored trajectory.
        
        Args:
            trajectory_id: Trajectory identifier
            session: Optional database session for testing
            
        Returns:
            TrajectoryEntry if found, None otherwise
        """
        use_session = session or self._session
        
        if use_session:
            # Use provided session (for testing)
            result = await use_session.execute(
                select(TrajectoryModel).where(
                    TrajectoryModel.trajectory_id == trajectory_id
                )
            )
            trajectory = result.scalar_one_or_none()
        else:
            # Create new session (for production)
            async with AsyncSessionLocal() as new_session:
                result = await new_session.execute(
                    select(TrajectoryModel).where(
                        TrajectoryModel.trajectory_id == trajectory_id
                    )
                )
                trajectory = result.scalar_one_or_none()
        
        if not trajectory:
            logger.warning(f"Trajectory {trajectory_id} not found")
            return None
        
        return TrajectoryEntry(
            trajectory_id=trajectory.trajectory_id,
            execution_id=trajectory.execution_id or "",
            timestamp=trajectory.created_at or datetime.now(timezone.utc),
            model=trajectory.model or "",
            messages=trajectory.messages or [],
            tool_calls=trajectory.tool_calls or [],
            completed=trajectory.completed or False,
            metadata=trajectory.trajectory_metadata or {}
        )
    
    async def list_trajectories(
        self,
        execution_id: Optional[str] = None,
        limit: int = 100,
        session: Optional[AsyncSession] = None,
    ) -> List[TrajectoryEntry]:
        """List stored trajectories.
        
        Args:
            execution_id: Optional filter by execution ID
            limit: Maximum number of results (default 100)
            session: Optional database session for testing
            
        Returns:
            List of trajectory entries
        """
        use_session = session or self._session
        
        query = select(TrajectoryModel).order_by(
            TrajectoryModel.created_at.desc()
        ).limit(limit)
        
        if execution_id:
            query = query.where(TrajectoryModel.execution_id == execution_id)
        
        if use_session:
            # Use provided session (for testing)
            result = await use_session.execute(query)
            trajectories = result.scalars().all()
        else:
            # Create new session (for production)
            async with AsyncSessionLocal() as new_session:
                result = await new_session.execute(query)
                trajectories = result.scalars().all()
        
        return [
            TrajectoryEntry(
                trajectory_id=t.trajectory_id,
                execution_id=t.execution_id or "",
                timestamp=t.created_at or datetime.now(timezone.utc),
                model=t.model or "",
                messages=t.messages or [],
                tool_calls=t.tool_calls or [],
                completed=t.completed or False,
                metadata=t.trajectory_metadata or {}
            )
            for t in trajectories
        ]
    
    async def replay_trajectory(
        self,
        trajectory_id: str
    ) -> List[Dict[str, Any]]:
        """Replay a trajectory and return messages.
        
        This method loads a trajectory and returns its messages for replay.
        The messages can be used to reconstruct the conversation state.
        
        Args:
            trajectory_id: Trajectory identifier
            
        Returns:
            List of messages from the trajectory
        """
        trajectory = await self.load_trajectory(trajectory_id)
        
        if not trajectory:
            logger.error(f"Cannot replay trajectory {trajectory_id}: not found")
            return []
        
        logger.info(
            f"Replaying trajectory {trajectory_id} with {len(trajectory.messages)} messages"
        )
        
        return trajectory.messages
    
    def _convert_scratchpad_tags(
        self,
        messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert reasoning scratchpad tags from 'thinking' to 'think'.
        
        This normalizes reasoning tags for consistency across different model providers.
        
        Args:
            messages: List of message dictionaries
            
        Returns:
            List of messages with converted tags
        """
        converted = []
        
        for message in messages:
            # Deep copy the message to avoid mutating the original
            msg_copy = message.copy()
            
            # Convert content if it's a string
            if isinstance(msg_copy.get("content"), str):
                msg_copy["content"] = msg_copy["content"].replace(
                    "<thinking>", "<think>"
                ).replace(
                    "</thinking>", "</think>"
                )
            
            # Convert content if it's a list (multi-part content)
            elif isinstance(msg_copy.get("content"), list):
                converted_content = []
                for part in msg_copy["content"]:
                    if isinstance(part, dict) and "text" in part:
                        part_copy = part.copy()
                        part_copy["text"] = part_copy["text"].replace(
                            "<thinking>", "<think>"
                        ).replace(
                            "</thinking>", "</think>"
                        )
                        converted_content.append(part_copy)
                    else:
                        converted_content.append(part)
                msg_copy["content"] = converted_content
            
            converted.append(msg_copy)
        
        return converted


# Singleton instance
trajectory_service = TrajectoryService()
