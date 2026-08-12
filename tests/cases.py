"""The lineups under regression test, and how to build one.

build_sld.py is invoked as a subprocess rather than imported, so the test
covers the CLI the README documents -- argument parsing, config loading and
saveas included -- instead of a private entry point.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

BUILD = ROOT / "python_scripts" / "build_sld.py"
CONFIG = ROOT / "python_scripts" / "sld_config.json"
LIBRARY = ROOT / "cad_files" / "symbol_library.dxf"


class Case:
    def __init__(self, name, units_csv, bus=None):
        self.name = name
        self.units_csv = ROOT / units_csv
        self.bus = bus

    @property
    def golden_path(self):
        return GOLDEN_DIR / f"{self.name}.json"

    def build(self, out_dxf):
        """Run the generator; return its stdout. Raises if it fails."""
        # --config and --library are passed explicitly because build_sld.py's
        # defaults are bare relative names ("sld_config.json"), which only
        # resolve if the process happens to be run from the directory holding
        # them. Passing them makes the test independent of the caller's cwd.
        cmd = [sys.executable, str(BUILD), str(self.units_csv), str(out_dxf),
               "--config", str(CONFIG), "--library", str(LIBRARY)]
        if self.bus:
            cmd += ["--bus", self.bus]

        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"build_sld.py failed for {self.name} (exit {p.returncode})\n"
                f"  cmd: {' '.join(cmd)}\n"
                f"  stdout: {p.stdout}\n"
                f"  stderr: {p.stderr}")
        return p.stdout


CASES = [
    Case("units_bus_a", "excel/units.csv", bus="A"),
    Case("units_bus_b", "excel/units.csv", bus="B"),
    Case("sample_3unit", "excel/sample_3unit_units.csv"),
]

BY_NAME = {c.name: c for c in CASES}
