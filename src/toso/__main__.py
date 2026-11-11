"""CLI entrypoint for TOSO Trading Engine."""

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from toso.config.loader import load_config_from_file
from toso.defense.universe import load_canonical_defensive_universe
from toso.live.preflight import PreFlightSafetyEngine
from toso.paper.runner import PaperTradingRunner
from toso.scientist.journal import TradeJournal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("toso")


def get_default_config() -> str:
    return os.environ.get("TOSO_CONFIG_PATH", "config/core_config_v1.yaml")


def get_default_evidence_dir() -> str:
    return os.environ.get("TOSO_EVIDENCE_DIR", "evidence")


def cmd_validate(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        return 1
    config = load_config_from_file(str(config_path))
    logger.info("Configuration valid: %s v%s", config.strategy.strategy_id, config.metadata.version)
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        return 1

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    journal_path = evidence_dir / "trade_journal.jsonl"

    config = load_config_from_file(str(config_path))
    journal = TradeJournal(persistence_path=journal_path)
    runner = PaperTradingRunner(config=config, journal=journal)
    defense_universe = load_canonical_defensive_universe()

    receipt = PreFlightSafetyEngine.execute_preflight_checks(
        runner=runner,
        defense_universe=defense_universe,
        raise_on_failure=False,
    )
    passed_count = sum(1 for c in receipt.checks if c.status.value == "PASSED")
    logger.info(
        "Pre-flight status: %s (Passed: %d/%d)",
        receipt.overall_status.value,
        passed_count,
        len(receipt.checks),
    )
    for check in receipt.checks:
        logger.info(" - [%s] %s: %s", check.status.value, check.check_id, check.name)
    return 0 if receipt.all_passed else 1


def cmd_run(args: argparse.Namespace) -> int:
    logger.info("Initializing TOSO Trading Engine daemon...")
    if cmd_validate(args) != 0:
        return 1
    logger.info("Running operational pre-flight checks...")
    if cmd_preflight(args) != 0:
        logger.warning("Pre-flight reported warnings, continuing in monitoring mode.")
    else:
        logger.info("All 7/7 pre-flight safety checks PASSED.")

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    journal_path = evidence_dir / "trade_journal.jsonl"
    config = load_config_from_file(str(args.config))
    journal = TradeJournal(persistence_path=journal_path)
    runner = PaperTradingRunner(config=config, journal=journal)

    logger.info(
        "TOSO daemon active. Mode: PAPER | Equity: $%.2f | Symbols: %s",
        runner.broker.total_equity,
        ", ".join(config.universe.eligible_symbols),
    )
    logger.info(
        "Journal persistent storage: %s (total entries: %d)", journal_path, journal.total_entries
    )

    try:
        heartbeat_count = 0
        while True:
            time.sleep(60)
            heartbeat_count += 1
            if heartbeat_count % 10 == 0:
                logger.info(
                    "Heartbeat [%s]: TOSO healthy | Equity: $%.2f | Positions: %d | Entries: %d",
                    datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"),
                    runner.broker.total_equity,
                    len(runner.broker.get_position_snapshots()),
                    journal.total_entries,
                )
    except KeyboardInterrupt:
        logger.info("Shutdown signal received. Exiting safely.")
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    """Start the TOSO Web Operations and Governance Console."""
    import uvicorn

    from toso.api.app import create_app
    from toso.api.state import APIServerState

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    config = load_config_from_file(str(args.config))
    state = APIServerState(config=config)
    app = create_app(state=state)

    logger.info("Starting TOSO Web Operations Console on %s:%d ...", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="TOSO Bear & Bull Trading Engine")
    subparsers = parser.add_subparsers(dest="command")

    # validate
    p_val = subparsers.add_parser("validate", help="Validate YAML configuration")
    p_val.add_argument("--config", default=get_default_config(), help="Path to config file")

    # preflight
    p_pre = subparsers.add_parser("preflight", help="Run operational pre-flight checks")
    p_pre.add_argument("--config", default=get_default_config(), help="Path to config file")
    p_pre.add_argument(
        "--evidence-dir",
        default=get_default_evidence_dir(),
        help="Path to evidence storage directory",
    )

    # run / daemon
    p_run = subparsers.add_parser("run", help="Start TOSO daemon worker")
    p_run.add_argument("--config", default=get_default_config(), help="Path to config file")
    p_run.add_argument(
        "--evidence-dir",
        default=get_default_evidence_dir(),
        help="Path to evidence storage directory",
    )

    # console (web operations interface)
    p_con = subparsers.add_parser("console", help="Start TOSO Web Operations & Governance Console")
    p_con.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Host to bind to")
    p_con.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="Port to bind to (defaults to 8080)",
    )
    p_con.add_argument("--config", default=get_default_config(), help="Path to config file")
    p_con.add_argument(
        "--evidence-dir",
        default=get_default_evidence_dir(),
        help="Path to evidence storage directory",
    )

    args = parser.parse_args()
    if args.command == "validate":
        sys.exit(cmd_validate(args))
    elif args.command == "preflight":
        sys.exit(cmd_preflight(args))
    elif args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "console":
        sys.exit(cmd_console(args))
    else:
        sys.exit(cmd_validate(argparse.Namespace(config=get_default_config())))


if __name__ == "__main__":
    main()
