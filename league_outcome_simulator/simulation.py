"""
Football season simulation module - delegates all simulation to Rust implementation.
"""

import glob
import os
import subprocess
import sys
from pathlib import Path


def _ensure_rust_extension():
    """
    Compile and install the Rust extension if it's missing or if source files
    are newer than the installed artifact.  Uses ``maturin develop --release``
    so the built module is placed directly into the active virtualenv.
    """
    project_root = Path(__file__).resolve().parent.parent

    # Look for the installed module in site-packages (where Python actually loads it)
    import sysconfig
    site_dir = Path(sysconfig.get_path("purelib"))
    pkg_dir = site_dir / "league_outcome_simulator_rust"
    installed_files = list(pkg_dir.glob("*.pyd")) + list(pkg_dir.glob("*.so"))

    # Also check target/release for cargo-only builds (legacy fallback)
    ext_dir = project_root / "target" / "release"
    target_patterns = [
        str(ext_dir / "league_outcome_simulator_rust*.pyd"),
        str(ext_dir / "league_outcome_simulator_rust*.dll"),
        str(ext_dir / "libleague_outcome_simulator_rust*.so"),
    ]
    target_files = []
    for pattern in target_patterns:
        target_files.extend(glob.glob(pattern))

    all_ext_files = installed_files + [Path(f) for f in target_files]

    # Rust source files
    rs_files = list((project_root / "src").glob("*.rs"))
    needs_build = False
    if not all_ext_files:
        needs_build = True
    else:
        ext_mtime = max(os.path.getmtime(str(f)) for f in all_ext_files)
        src_mtime = max(f.stat().st_mtime for f in rs_files) if rs_files else 0
        if src_mtime > ext_mtime:
            needs_build = True
    if needs_build:
        print("🔨 Compiling Rust extension...")
        env = os.environ.copy()
        # Ensure VIRTUAL_ENV is set so maturin can find the venv
        if "VIRTUAL_ENV" not in env:
            venv_dir = Path(sys.prefix)
            if venv_dir != Path(sys.base_prefix):
                env["VIRTUAL_ENV"] = str(venv_dir)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "maturin", "develop", "--release"],
                cwd=str(project_root),
                env=env,
            )
        except subprocess.CalledProcessError as e:
            print("❌ Failed to compile Rust extension:", e)
            sys.exit(1)


# Restore module-level build check and import
_ensure_rust_extension()

try:
    from league_outcome_simulator_rust import (
        simulate_bulk as rust_simulate_bulk,  # type: ignore
    )
    from league_outcome_simulator_rust import (
        simulate_season as rust_simulate_season,  # type: ignore
    )
except ImportError:
    print(
        "❌ Rust simulation library could not be loaded! Please compile the Rust extension module."
    )
    raise ImportError("Rust simulation module is required but could not be imported")


def simulate_season(base_table, fixtures, home_table=None, away_table=None):
    """
    Simulate the remainder of the season based on current standings and fixtures.
    Delegates to the Rust implementation (precompiled at import time).
    """
    return rust_simulate_season(base_table, fixtures, home_table, away_table)


def simulate_bulk(base_table, fixtures, home_table, away_table, n_sims):
    """
    Simulate many seasons in parallel using Rust's Rayon for maximum performance.
    Returns a dict: {team_name: {position: count, ...}, ...}

    This is much faster than calling simulate_season repeatedly from Python
    because it avoids Python GIL overhead and serialization costs.
    """
    return rust_simulate_bulk(base_table, fixtures, home_table, away_table, n_sims)
