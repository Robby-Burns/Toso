"""TOSO Configuration Package.

Public interface for configuration schemas, loaders, validation, and active registry.
"""

from toso.config.errors import (
    ConfigurationError,
    ConfigurationMissingError,
    ConfigurationNotFoundError,
    ConfigurationParseError,
    ConfigurationValidationError,
    UnapprovedConfigurationError,
    UnversionedConfigurationError,
)
from toso.config.loader import (
    ConfigManager,
    compute_config_checksum,
    load_config_from_dict,
    load_config_from_file,
)
from toso.config.models import (
    ApprovalStatus,
    CapitalAllocationConfig,
    CoreConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
    VersionMetadata,
)

__all__ = [
    "ApprovalStatus",
    "CapitalAllocationConfig",
    "ConfigManager",
    "ConfigurationError",
    "ConfigurationMissingError",
    "ConfigurationNotFoundError",
    "ConfigurationParseError",
    "ConfigurationValidationError",
    "CoreConfig",
    "RiskConfig",
    "StrategyConfig",
    "UnapprovedConfigurationError",
    "UniverseConfig",
    "UnversionedConfigurationError",
    "VersionMetadata",
    "compute_config_checksum",
    "load_config_from_dict",
    "load_config_from_file",
]
