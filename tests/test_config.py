"""Comprehensive tests for TOSO Core Configuration.

Verifies:
- AC-001: Only an approved, versioned configuration may control production Core behavior.
- AC-002: Invalid, unversioned, or unavailable configuration prevents execution (fails closed).
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from toso.config import (
    ApprovalStatus,
    ConfigManager,
    ConfigurationMissingError,
    ConfigurationNotFoundError,
    ConfigurationParseError,
    ConfigurationValidationError,
    CoreConfig,
    UnapprovedConfigurationError,
    UnversionedConfigurationError,
    load_config_from_dict,
    load_config_from_file,
)


@pytest.fixture(autouse=True)
def reset_config_manager():
    """Ensure ConfigManager is clean before and after every test."""
    ConfigManager.reset()
    yield
    ConfigManager.reset()


@pytest.fixture
def valid_config_dict() -> dict:
    """Fixture providing a complete, valid, approved configuration dictionary."""
    return {
        "metadata": {
            "version": "1.0.0",
            "status": "APPROVED",
            "approved_by": "Risk Committee",
            "approved_at": "2026-09-14T10:00:00Z",
            "description": "Base production configuration.",
        },
        "strategy": {
            "strategy_id": "trend_breakout_v1",
            "timeframe": "1d",
            "fast_ma_period": 50,
            "slow_ma_period": 200,
            "atr_period": 14,
            "breakout_lookback_bars": 20,
            "invalidation_atr_multiple": 2.0,
            "pyramid_min_profit_r": 1.0,
            "max_pyramid_adds": 3,
        },
        "risk": {
            "max_risk_per_trade_pct": 0.01,
            "max_aggregate_position_risk_pct": 0.02,
            "max_portfolio_risk_pct": 0.06,
            "max_position_concentration_pct": 0.20,
            "slippage_buffer_pct": 0.005,
            "emergency_drawdown_stop_pct": 0.10,
        },
        "capital": {
            "core_allocation_pct": 0.80,
            "reserve_allocation_pct": 0.15,
            "black_swan_allocation_pct": 0.05,
        },
        "universe": {
            "eligible_symbols": ["AAPL", "MSFT", "GOOGL"],
            "min_average_daily_volume_shares": 500_000,
            "min_share_price_dollars": 5.0,
        },
        "require_invalidation_before_entry": True,
        "short_selling_enabled": False,
    }


# ==============================================================================
# AC-001: Approved, Versioned Configuration Controls Core Behavior
# ==============================================================================


def test_ac001_load_canonical_approved_config_file():
    """Verify canonical YAML configuration file loads, validates, and reflects approved status."""
    config_path = Path("config/core_config_v1.yaml")
    assert config_path.is_file(), "Canonical configuration file must exist."

    config = load_config_from_file(config_path, require_approved=True)

    assert isinstance(config, CoreConfig)
    assert config.metadata.version == "1.0.0"
    assert config.metadata.status == ApprovalStatus.APPROVED
    assert config.metadata.approved_by == "System Owner"
    assert config.metadata.approved_at is not None
    assert config.strategy.strategy_id == "core_trend_v1"
    assert config.strategy.fast_ma_period == 50
    assert config.strategy.slow_ma_period == 200
    assert config.risk.max_risk_per_trade_pct == 0.01
    assert config.risk.max_aggregate_position_risk_pct == 0.02
    assert config.risk.max_portfolio_risk_pct == 0.06
    assert config.capital.core_allocation_pct == 0.80
    assert config.capital.reserve_allocation_pct == 0.15
    assert config.capital.black_swan_allocation_pct == 0.05
    assert "AAPL" in config.universe.eligible_symbols
    assert config.require_invalidation_before_entry is True


def test_ac001_configuration_immutability(valid_config_dict):
    """Verify configuration is completely immutable to prevent runtime tampering."""
    config = load_config_from_dict(valid_config_dict, require_approved=True)

    with pytest.raises(ValidationError):
        config.risk.max_risk_per_trade_pct = 0.05

    with pytest.raises(ValidationError):
        config.metadata.status = ApprovalStatus.DRAFT

    with pytest.raises(ValidationError):
        config.strategy.fast_ma_period = 10

    with pytest.raises(ValidationError):
        config.require_invalidation_before_entry = False


def test_ac001_active_config_manager_workflow(valid_config_dict):
    """Verify ConfigManager sets, holds, and returns approved configuration."""
    assert not ConfigManager.is_active()

    config = load_config_from_dict(valid_config_dict, require_approved=True)
    ConfigManager.set_active_config(config)

    assert ConfigManager.is_active()
    active = ConfigManager.get_active_config()
    assert active == config
    assert active.metadata.version == "1.0.0"


# ==============================================================================
# AC-002: Failure Paths & Fail-Closed Controls
# ==============================================================================


def test_ac002_missing_config_file_fails_closed():
    """Verify loading a non-existent configuration file raises ConfigurationNotFoundError."""
    with pytest.raises(ConfigurationNotFoundError) as exc_info:
        load_config_from_file("non_existent_config.yaml")
    assert "not found" in str(exc_info.value).lower()


def test_ac002_malformed_yaml_fails_closed(tmp_path):
    """Verify malformed YAML syntax raises ConfigurationParseError."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("metadata:\n  version: [unclosed list", encoding="utf-8")

    with pytest.raises(ConfigurationParseError):
        load_config_from_file(bad_yaml)


def test_ac002_non_dict_config_fails_closed(tmp_path):
    """Verify non-mapping configuration raises ConfigurationValidationError."""
    scalar_yaml = tmp_path / "scalar.yaml"
    scalar_yaml.write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(ConfigurationValidationError):
        load_config_from_file(scalar_yaml)


def test_ac002_unversioned_config_rejected(valid_config_dict):
    """Verify configuration without version metadata raises UnversionedConfigurationError."""
    del valid_config_dict["metadata"]
    with pytest.raises(UnversionedConfigurationError):
        load_config_from_dict(valid_config_dict)

    valid_config_dict["metadata"] = {"version": "  "}
    with pytest.raises(UnversionedConfigurationError):
        load_config_from_dict(valid_config_dict)


def test_ac002_unapproved_status_rejected_in_production(valid_config_dict):
    """Verify configuration with status != APPROVED raises UnapprovedConfigurationError."""
    valid_config_dict["metadata"]["status"] = "DRAFT"

    with pytest.raises(UnapprovedConfigurationError) as exc_info:
        load_config_from_dict(valid_config_dict, require_approved=True)
    assert "production execution requires 'APPROVED'" in str(exc_info.value)


def test_ac002_approved_status_without_approver_rejected(valid_config_dict):
    """Verify APPROVED status lacking approved_by or approved_at raises ValidationError."""
    valid_config_dict["metadata"]["approved_by"] = None
    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict, require_approved=True)
    assert "approved_by" in str(exc_info.value)

    valid_config_dict["metadata"]["approved_by"] = "Owner"
    valid_config_dict["metadata"]["approved_at"] = ""
    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict, require_approved=True)
    assert "approved_at" in str(exc_info.value)


def test_ac002_cannot_set_unapproved_config_as_active(valid_config_dict):
    """Verify ConfigManager rejects setting unapproved configuration as active."""
    valid_config_dict["metadata"]["status"] = "DRAFT"
    draft_config = load_config_from_dict(valid_config_dict, require_approved=False)

    with pytest.raises(UnapprovedConfigurationError):
        ConfigManager.set_active_config(draft_config, require_approved=True)

    assert not ConfigManager.is_active()


def test_ac002_missing_active_config_raises_missing_error():
    """Verify getting active config when none is set raises ConfigurationMissingError."""
    with pytest.raises(ConfigurationMissingError) as exc_info:
        ConfigManager.get_active_config()
    assert "No active configuration loaded" in str(exc_info.value)


def test_ac002_moving_average_ordering_validation(valid_config_dict):
    """Verify fast_ma_period >= slow_ma_period raises ConfigurationValidationError."""
    valid_config_dict["strategy"]["fast_ma_period"] = 200
    valid_config_dict["strategy"]["slow_ma_period"] = 50

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict)
    assert "must be strictly less than" in str(exc_info.value)


def test_ac002_risk_hierarchy_validation(valid_config_dict):
    """Verify trade risk > aggregate risk, or aggregate risk > portfolio risk raises error."""
    # aggregate risk < trade risk
    valid_config_dict["risk"]["max_risk_per_trade_pct"] = 0.03
    valid_config_dict["risk"]["max_aggregate_position_risk_pct"] = 0.02
    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict)
    assert "cannot be less than max_risk_per_trade_pct" in str(exc_info.value)

    # portfolio risk < aggregate risk
    valid_config_dict["risk"]["max_risk_per_trade_pct"] = 0.01
    valid_config_dict["risk"]["max_aggregate_position_risk_pct"] = 0.08
    valid_config_dict["risk"]["max_portfolio_risk_pct"] = 0.05
    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict)
    assert "cannot be less than max_aggregate_position_risk_pct" in str(exc_info.value)


def test_ac002_capital_allocations_sum_validation(valid_config_dict):
    """Verify capital allocations not summing to 1.0 raise ConfigurationValidationError."""
    valid_config_dict["capital"]["core_allocation_pct"] = 0.70
    valid_config_dict["capital"]["reserve_allocation_pct"] = 0.15
    valid_config_dict["capital"]["black_swan_allocation_pct"] = 0.10  # sum = 0.95

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict)
    assert "must sum to 1.0" in str(exc_info.value)


def test_ac002_invalidation_invariant_cannot_be_disabled(valid_config_dict):
    """Verify require_invalidation_before_entry cannot be set to False (INV-005)."""
    valid_config_dict["require_invalidation_before_entry"] = False

    with pytest.raises(ConfigurationValidationError) as exc_info:
        load_config_from_dict(valid_config_dict)
    assert "must remain True" in str(exc_info.value)
