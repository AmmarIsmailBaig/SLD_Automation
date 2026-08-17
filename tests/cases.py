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
# The assignment lineup is a different standard (13.8kV, single CT set) forced
# onto no reference drawing of its own -- it gets its own config file, the same
# way 8372/8513/8478 each have theirs, rather than sharing sld_config.json's
# 8508 sheet geometry (bus_y sitting 2.019 below cubicle_top, which has no room
# for the bus PT branch this lineup's spec calls for).
CONFIG_ASSIGNMENT = ROOT / "python_scripts" / "sld_config_assignment.json"
LIBRARY = ROOT / "cad_files" / "symbol_library.dxf"


class Case:
    def __init__(self, name, units_csv, bus=None, config=CONFIG):
        self.name = name
        self.units_csv = ROOT / units_csv
        self.bus = bus
        self.config = config

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
               "--config", str(self.config), "--library", str(LIBRARY)]
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
    # The 8508 lineups above all carry two CT sets per feeder. This one is the
    # single-CT case -- it is the only fixture covering ct_compartment_protection,
    # ct_ground, and the archetypes authored from the assignment spec rather than
    # measured off 8508, so without it that whole path is untested.
    Case("assignment", "assignment_units.csv", config=CONFIG_ASSIGNMENT),
]

BY_NAME = {c.name: c for c in CASES}
