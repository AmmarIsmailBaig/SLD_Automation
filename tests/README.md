# Regression tests

Golden-file tests over `build_sld.py`. Each case builds a lineup and compares a
fingerprint of the resulting modelspace against a recorded one, so a refactor
that is meant to preserve geometry can be checked instead of eyeballed.

```bash
pip install pytest          # ezdxf is already needed to build
python -m pytest tests/ -q
```

## Cases

| Case | Input | Bus | Covers |
|---|---|---|---|
| `units_bus_a` | `excel/units.csv` | A | 8508 types, dual CT set |
| `units_bus_b` | `excel/units.csv` | B | 8508 types, dual CT set |
| `sample_3unit` | `excel/sample_3unit_units.csv` | all | 8508 types, minimal lineup |
| `assignment` | `assignment_units.csv` | all | spec-authored types, **single CT set** |

The `assignment` case is the one that pins the single-CT path:
`ct_compartment_protection`, `ct_ground`, and the four archetypes authored from
the assignment specification. The 8508 fixtures all carry two CT sets per
feeder, so nothing else in the suite exercises those roles.

Add one by appending a `Case(...)` to `CASES` in `cases.py` and running
`python tests/regen_golden.py <name>`.

## What the fingerprint covers

Every modelspace entity, sorted, as: type, layer, block name, coordinates
rounded to 3 decimals, plus the fields that make an entity what it is —
radius and angles for arcs, rotation, scale and visible attributes for block
references, and the string for MTEXT.

Text is included on purpose. MTEXT is about 15% of the entities and without its
content one label looks like another, so a fingerprint without text would pass
a build that put every relay label on the wrong cubicle.

Not covered, deliberately: entity handles (fresh every run), header variables,
and the imported block *definitions*. If a symbol's internals change, the
archetypes still place it at the same point — that is a library change, not a
generator regression, and wants its own test.

## When a test fails

The failure names what moved rather than just reporting that files differ.
Records are paired by identity — everything except position — and identical
shifts collapse into one line:

```
units_bus_a: built geometry differs from tests/golden/units_bus_a.json
  entities: 439 golden -> 439 current

  MOVED 7 by (+0.050, +0.000):
    INSERT VFU1_1- SLD_SYMS  (10.081,16.204)
    ...
```

Read the shape of it before doing anything:

- **One uniform delta across many entities** — a sheet datum moved
  (`sheet.pitch`, `bus_y`, `first_cubicle_x`).
- **Several deltas that scale with cubicle position** — `sheet.pitch`.
- **A handful of entities sharing a block name** — one `roles` entry.
- **ADDED / REMOVED** — an `archetypes` role list changed, or a symbol went
  missing from the library and got skipped.

If the change is intended:

```bash
python tests/regen_golden.py units_bus_a   # or no argument for all cases
```

Regenerating without reading the diff records the regression instead of
catching it, which is the one way to make these tests worthless.

## Layout

| File | Role |
|---|---|
| `fingerprint.py` | Builds the fingerprint from a DXF. `NDIGITS` sets rounding. |
| `diff.py` | Pairs records and renders the failure report. |
| `cases.py` | The cases, and the subprocess call that builds one. |
| `test_golden.py` | The tests. |
| `regen_golden.py` | Re-records goldens. |
| `golden/*.json` | The recorded fingerprints. |

Beyond the golden comparison there are two guards: `test_build_is_deterministic`
builds each case twice and requires identical fingerprints, because a golden
recorded from a nondeterministic build would fail at random and train people to
regenerate reflexively; and `test_output_is_not_empty` fails on a drawing with
no entities, or one containing an entity type `fingerprint.py` does not model —
without it, a new type would pass unchecked forever.

`build_sld.py`'s `--config` and `--library` defaults are bare relative names, so
`cases.py` passes both explicitly and the suite runs from any directory.
