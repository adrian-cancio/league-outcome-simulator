"""Rust simulation bridge with optional build assistance."""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _import_rust_module() -> ModuleType:
    return importlib.import_module("league_outcome_simulator_rust")


def build_rust_extension() -> None:
    """Build the Rust extension into the active environment."""
    env = dict(**__import__("os").environ)
    subprocess.check_call(
        [sys.executable, "-m", "maturin", "develop", "--release"],
        cwd=str(PROJECT_ROOT),
        env=env,
    )


def get_rust_module(*, auto_build: bool = False) -> ModuleType:
    """Load the Rust module, optionally building it on demand."""
    try:
        return _import_rust_module()
    except ImportError as exc:
        if not auto_build:
            raise RuntimeError(
                "Rust simulation library is not available. Install the package with its Rust extension, "
                "or run the CLI which can attempt a local build automatically."
            ) from exc

        LOGGER.info("Rust extension not found, attempting local build via maturin")
        try:
            build_rust_extension()
        except Exception as build_exc:  # pragma: no cover - exercised from CLI
            raise RuntimeError(
                "Could not build the Rust extension automatically. Ensure Rust and maturin are installed."
            ) from build_exc
        return _import_rust_module()


def simulate_season(
    base_table,
    fixtures,
    home_table=None,
    away_table=None,
    *,
    seed: int | None = None,
    auto_build: bool = False,
):
    """Simulate one season using the Rust backend."""
    rust_module = get_rust_module(auto_build=auto_build)
    return rust_module.simulate_season(
        base_table, fixtures, home_table, away_table, seed
    )


def simulate_bulk(
    base_table,
    fixtures,
    home_table,
    away_table,
    n_sims,
    *,
    seed: int | None = None,
    top_k_tables: int = 25,
    auto_build: bool = False,
):
    """Simulate many seasons in bulk using the Rust backend."""
    rust_module = get_rust_module(auto_build=auto_build)
    return rust_module.simulate_bulk(
        base_table,
        fixtures,
        home_table,
        away_table,
        n_sims,
        seed,
        top_k_tables,
    )
