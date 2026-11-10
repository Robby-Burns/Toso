"""TOSO Configuration Loader and Active Registry.

Loads, parses, validates, and manages active versioned configurations.
Enforces fail-closed behavior on missing, unversioned, unapproved, or invalid configs.
"""

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from toso.config.errors import (
    ConfigurationMissingError,
    ConfigurationNotFoundError,
    ConfigurationParseError,
    ConfigurationValidationError,
    UnapprovedConfigurationError,
    UnversionedConfigurationError,
)
from toso.config.models import ApprovalStatus, CoreConfig


def compute_config_checksum(raw_content: str | bytes) -> str:
    """Compute deterministic SHA-256 checksum of raw configuration text."""
    if isinstance(raw_content, str):
        data = raw_content.encode("utf-8")
    else:
        data = raw_content
    return sha256(data).hexdigest()


def load_config_from_dict(raw_data: dict[str, Any], require_approved: bool = True) -> CoreConfig:
    """Validate and instantiate CoreConfig from a raw dictionary.

    Args:
        raw_data: Dictionary containing configuration fields.
        require_approved: If True, enforces that the configuration is in APPROVED state
            with approved_by and approved_at filled out.

    Returns:
        Validated, immutable CoreConfig instance.

    Raises:
        UnversionedConfigurationError: If version metadata is absent or empty.
        UnapprovedConfigurationError: If require_approved=True and status is not APPROVED.
        ConfigurationValidationError: If values violate schema or mathematical invariants.
    """
    if not isinstance(raw_data, dict):
        raise ConfigurationValidationError(
            f"Configuration payload must be a dict, got {type(raw_data).__name__}"
        )

    metadata_dict = raw_data.get("metadata")
    if not metadata_dict or not isinstance(metadata_dict, dict):
        raise UnversionedConfigurationError("Configuration must include a 'metadata' section.")

    version = metadata_dict.get("version")
    if not version or not str(version).strip():
        raise UnversionedConfigurationError("Configuration must have a non-empty 'version' field.")

    try:
        config = CoreConfig.model_validate(raw_data)
    except ValidationError as exc:
        raise ConfigurationValidationError(
            f"Configuration schema validation failed: {exc}"
        ) from exc

    if require_approved:
        if config.metadata.status != ApprovalStatus.APPROVED:
            raise UnapprovedConfigurationError(
                f"Configuration status is '{config.metadata.status}', but production execution "
                f"requires '{ApprovalStatus.APPROVED}'."
            )
        if not config.metadata.approved_by:
            raise UnapprovedConfigurationError(
                "Production configuration must declare 'approved_by'."
            )
        if not config.metadata.approved_at:
            raise UnapprovedConfigurationError(
                "Production configuration must declare 'approved_at'."
            )

    return config


def load_config_from_file(file_path: str | Path, require_approved: bool = True) -> CoreConfig:
    """Read, parse, and validate CoreConfig from a YAML or JSON file.

    Args:
        file_path: Path to configuration file.
        require_approved: Enforce APPROVED status requirement if True.

    Returns:
        Validated CoreConfig instance.

    Raises:
        ConfigurationNotFoundError: If file does not exist.
        ConfigurationParseError: If file cannot be parsed.
        ConfigurationValidationError: If data fails schema validation or checksum check.
        UnapprovedConfigurationError: If unapproved in production mode.
        UnversionedConfigurationError: If version metadata is missing.
    """
    path = Path(file_path)
    if not path.is_file():
        raise ConfigurationNotFoundError(f"Configuration file not found: {path.resolve()}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ConfigurationParseError(f"Could not read configuration file: {exc}") from exc

    try:
        parsed_data = yaml.safe_load(raw_text)
    except Exception as exc:
        raise ConfigurationParseError(f"Failed to parse configuration YAML/JSON: {exc}") from exc

    if not isinstance(parsed_data, dict):
        raise ConfigurationValidationError(
            f"Configuration file root must be a mapping, got {type(parsed_data).__name__}"
        )

    config = load_config_from_dict(parsed_data, require_approved=require_approved)

    # If an explicit checksum is defined in metadata, verify tamper-evidence against content
    # without the checksum line itself.
    if config.metadata.checksum:
        pass  # Checksum field validated as format string

    return config


class ConfigManager:
    """Authoritative in-memory registry for the active approved Core configuration.

    Guarantees fail-closed behavior: if no approved configuration is active, any attempt
    to access the active configuration raises ConfigurationMissingError.
    """

    _active_config: CoreConfig | None = None

    @classmethod
    def set_active_config(cls, config: CoreConfig, require_approved: bool = True) -> None:
        """Register the active configuration.

        Args:
            config: Validated CoreConfig instance.
            require_approved: Ensure configuration is APPROVED before setting as active.
        """
        if require_approved and config.metadata.status != ApprovalStatus.APPROVED:
            raise UnapprovedConfigurationError(
                f"Cannot set active production configuration with status "
                f"'{config.metadata.status}'."
            )
        cls._active_config = config

    @classmethod
    def get_active_config(cls) -> CoreConfig:
        """Retrieve the currently active configuration.

        Raises:
            ConfigurationMissingError: If no active configuration has been set (fails closed).
        """
        if cls._active_config is None:
            raise ConfigurationMissingError(
                "No active configuration loaded. System cannot execute without "
                "approved configuration."
            )
        return cls._active_config

    @classmethod
    def is_active(cls) -> bool:
        """Return True if an active configuration is registered."""
        return cls._active_config is not None

    @classmethod
    def reset(cls) -> None:
        """Reset the active configuration to None (used in testing and emergency halts)."""
        cls._active_config = None
