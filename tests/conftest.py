import sys
from pathlib import Path

# The test modules import fingerprint/diff/cases as top-level names. pytest
# only guarantees that for the rootdir, so add this directory explicitly --
# it also lets regen_golden.py be run directly from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))
