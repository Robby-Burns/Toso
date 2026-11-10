"""TOSO Configuration Errors.

Explicit error taxonomy for configuration validation and fail-closed controls.
"""


class ConfigurationError(Exception):
    """Base error for all configuration-related failures."""


class ConfigurationNotFoundError(ConfigurationError):
    """Raised when a requested configuration file cannot be found."""


class ConfigurationParseError(ConfigurationError):
    """Raised when a configuration file cannot be parsed (e.g. malformed YAML/JSON)."""


class ConfigurationValidationError(ConfigurationError):
    """Raised when configuration values violate schema, invariants, or mathematical constraints."""


class UnapprovedConfigurationError(ConfigurationError):
    """Raised when an unapproved configuration is attempted to be used in production."""


class UnversionedConfigurationError(ConfigurationError):
    """Raised when configuration lacks explicit version metadata."""


class ConfigurationMissingError(ConfigurationError):
    """Raised when the system attempts to execute without an active approved configuration."""
