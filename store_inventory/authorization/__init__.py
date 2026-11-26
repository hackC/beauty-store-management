"""Permission-group administration module."""
from .composition import create_permission_group_service
from .domain import AuditActor, AuthorizationError

__all__ = ["create_permission_group_service", "AuditActor", "AuthorizationError"]
