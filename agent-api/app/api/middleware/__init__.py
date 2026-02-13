"""Middleware package."""
from app.api.middleware.error_handler import register_exception_handlers

__all__ = ['register_exception_handlers']
