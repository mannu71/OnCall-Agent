"""Base repository class for data access."""
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Optional, List, Dict, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """Abstract base repository for data access operations."""
    
    def __init__(self, storage_path: Path):
        """Initialize repository with storage path.
        
        Args:
            storage_path: Path to storage directory
        """
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"{self.__class__.__name__} initialized at: {self.storage_path}")
    
    @abstractmethod
    async def get_by_id(self, item_id: str) -> Optional[T]:
        """Get an item by ID.
        
        Args:
            item_id: Unique identifier
            
        Returns:
            Item if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def list_all(self) -> List[T]:
        """List all items.
        
        Returns:
            List of all items
        """
        pass
    
    @abstractmethod
    async def save(self, item: T) -> T:
        """Save or update an item.
        
        Args:
            item: Item to save
            
        Returns:
            Saved item
        """
        pass
    
    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID.
        
        Args:
            item_id: Unique identifier
            
        Returns:
            True if deleted, False if not found
        """
        pass
    
    @abstractmethod
    async def exists(self, item_id: str) -> bool:
        """Check if an item exists.
        
        Args:
            item_id: Unique identifier
            
        Returns:
            True if exists, False otherwise
        """
        pass
