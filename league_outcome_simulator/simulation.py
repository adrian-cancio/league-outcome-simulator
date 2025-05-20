"""
Football season simulation module - delegates all simulation to Rust implementation.
"""

import os
import sys
import subprocess
import glob
from pathlib import Path


def _ensure_rust_extension():
    """
    Compile the Rust extension if it's missing or if source files are newer than the compiled artifact.
    """
    project_root = Path(__file__).resolve().parent.parent
    ext_dir = project_root / "target" / "release"
    # Pattern for the Rust Python extension on Windows (pyd) or other platforms (.so)
    patterns = [str(ext_dir / "league_outcome_simulator_rust*.pyd"),
                str(ext_dir / "libleague_outcome_simulator_rust*.so")]
    ext_files = []
    for pattern in patterns:
        ext_files.extend(glob.glob(pattern))
    # Rust source files
    rs_files = list((project_root / "src").glob("*.rs"))
    needs_build = False
    if not ext_files:
        needs_build = True
    else:
        ext_mtime = max(os.path.getmtime(f) for f in ext_files)
        src_mtime = max(f.stat().st_mtime for f in rs_files) if rs_files else 0
        if src_mtime > ext_mtime:
            needs_build = True
    if needs_build:
        print("🔨 Compiling Rust extension...")
        try:
            subprocess.check_call(["cargo", "build", "--release"], cwd=str(project_root))
        except subprocess.CalledProcessError as e:
            print("❌ Failed to compile Rust extension:", e)
            sys.exit(1)

# Restore module-level build check and import
_ensure_rust_extension()

try:
    from league_outcome_simulator_rust import simulate_season as rust_simulate_season  # type: ignore
except ImportError:
    print("❌ Rust simulation library could not be loaded! Please compile the Rust extension module.")
    raise ImportError("Rust simulation module is required but could not be imported")

def simulate_season(base_table, fixtures, home_table=None, away_table=None):
    """
    Simulate the remainder of the season based on current standings and fixtures.
    Delegates to the Rust implementation (precompiled at import time).
    """
    return rust_simulate_season(base_table, fixtures, home_table, away_table)