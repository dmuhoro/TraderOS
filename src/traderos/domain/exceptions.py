class TraderOSError(Exception):
    """Base exception for all TraderOS errors."""


class DomainError(TraderOSError):
    """Base for domain-layer errors."""


class EntityValidationError(DomainError):
    """Entity invariant violation."""


class InfrastructureError(TraderOSError):
    """Base for infrastructure-layer errors."""


class RepositoryError(InfrastructureError):
    """Base for repository-layer errors."""


class EntityNotFoundError(RepositoryError):
    """Entity not found in repository."""


class DuplicateEntityError(RepositoryError):
    """Entity already exists."""


class ConfigError(InfrastructureError):
    """Configuration error."""


class DatabaseError(InfrastructureError):
    """Database operation error."""


class ApplicationError(TraderOSError):
    """Base for application-layer errors."""


class ServiceError(ApplicationError):
    """Service operation error."""


class InterfaceError(TraderOSError):
    """Base for interface-layer errors."""


class CLIError(InterfaceError):
    """CLI operation error."""


class ValidationError(InterfaceError):
    """Input validation error."""
