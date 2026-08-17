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
BUILD_INTAKE = ROOT / "python_scripts" / "build_intake.py"
STANDARD = ROOT / "python_scripts" / "standard.json"
CONFIG = ROOT / "python_scripts" / "sld_config.json"
# The assignment lineup is a different standard (13.8kV, single CT set) forced
# onto no reference drawing of its own -- it gets its own config file, the same
# way 8372/8513/8478 each have theirs, rather than sharing sld_config.json's
# 8508 sheet geometry (bus_y sitting 2.019 below cubicle_top, which has no room
# for the bus PT branch this lineup's spec calls for).
CONFIG_ASSIGNMENT = ROOT / "python_scripts" / "sld_config_assignment.json"
CONFIG_8478 = ROOT / "python_scripts" / "sld_config_8478.json"
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


class IntakeCase(Case):
    """A lineup built from an intake workbook rather than a hand-written config.

    This is the path with no measured config behind it: the devices are stacked
    from what the row states, so a change to standard.json or to the stacker
    moves geometry on every unit at once. That is exactly the kind of change a
    fingerprint catches and a person reading a render does not.
    """

    def __init__(self, name, workbook, standard=STANDARD):
        super().__init__(name, workbook)
        self.workbook = ROOT / workbook
        self.standard = standard

    def build(self, out_dxf):
        cmd = [sys.executable, str(BUILD_INTAKE), str(self.workbook), str(out_dxf),
               "--standard", str(self.standard), "--library", str(LIBRARY)]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(
                f"build_intake.py failed for {self.name} (exit {p.returncode})\n"
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
    # The only config whose archetype declares a device stack rather than
    # measured elevations, so it is the only case that exercises stacker.py --
    # which measures the real blocks out of the symbol library to place them.
    Case("stacked_38kv", "excel/8478_units.csv", config=CONFIG_8478),

    # --- the intake path -------------------------------------------------
    # Single-high, and deliberately the workbook written against the OLD
    # 25-column template: it pins backward compatibility, so trimming a column
    # from the template can never quietly stop an already-filled sheet building.
    IntakeCase("intake_single", "intake_assignment.xlsx"),

    # Two-high, and the one fixture holding the reflection together. Between
    # them its rows cover: a PT-only upper deck (no amp_rating, so no breaker,
    # no drawouts and no cable exit), a breaker upper deck carrying a second CT,
    # the mirrored control run, and a plain single-high unit in the same lineup.
    # Every upper-deck rule -- the unreflected PT branch, unrotated attribute
    # text, the hidden duplicate header, conductor_top -- fails visibly here.
    IntakeCase("intake_twohigh", "excel/intake_twohigh.xlsx"),
]

BY_NAME = {c.name: c for c in CASES}
