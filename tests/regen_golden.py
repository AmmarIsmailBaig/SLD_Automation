"""Re-record golden fingerprints.

    python tests/regen_golden.py              # every case
    python tests/regen_golden.py units_bus_a  # just one

Run this only when a geometry change is intended, and read the diff the failing
test printed first -- a golden file regenerated without looking at what moved
records the regression instead of catching it.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import BY_NAME, CASES, GOLDEN_DIR  # noqa: E402
from fingerprint import dumps, fingerprint_dxf  # noqa: E402


def regen(case):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"{case.name}.dxf"
        case.build(out)
        fp = fingerprint_dxf(out)

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    case.golden_path.write_text(dumps(fp), encoding="utf-8")
    print(f"{case.name}: {fp['entity_count']} entities -> "
          f"{case.golden_path.relative_to(GOLDEN_DIR.parent.parent)}")


def main(argv):
    if argv:
        unknown = [n for n in argv if n not in BY_NAME]
        if unknown:
            sys.exit(f"unknown case(s): {', '.join(unknown)}\n"
                     f"known: {', '.join(BY_NAME)}")
        selected = [BY_NAME[n] for n in argv]
    else:
        selected = CASES

    for case in selected:
        regen(case)


if __name__ == "__main__":
    main(sys.argv[1:])
