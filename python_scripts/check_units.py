"""
Check a units.csv against the config that will consume it, before building.

units.csv is the input a human writes by hand, and the engine reads it
forgivingly: an unknown column is ignored, a blank cell draws nothing, and a
misspelt archetype only surfaces as a warning after the sheet is already
written. Those are reasonable behaviours at draw time and terrible ones at
authoring time, because every mistake looks like a drawing that built fine.

So this reports the mismatch in both directions, which is the part neither the
CSV nor the config can state alone:

  columns the config asks for and the CSV does not supply  -- text that will
  silently come out empty;

  columns the CSV supplies and no config role reads       -- edits that will
  silently do nothing. The `arrester` column on the 8508 lineup is the live
  example: it is filled in on fourteen units, and arrester placement is decided
  entirely by the archetype's role list, so clearing a cell removes nothing.

Blank cells are listed rather than failed. A blank is usually deliberate -- the
riser units carry no tag and no relay -- so the only useful thing to do is show
which ones are blank and let a person confirm that is what they meant.

Usage:
    python check_units.py units.csv --config sld_config.json
    python check_units.py units.csv --config sld_config.json --bom "BOM.xlsx"
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict

# Columns annotate_unit() reads directly for every unit, regardless of config.
# They are not discoverable from the JSON because they are named in the drawing
# code, so they are restated here; if that function grows a field, add it.
ALWAYS_READ = ["unit", "tag", "description", "voltage",
               "amp_rating", "ka_rating", "relay", "destination"]

# Columns the loader uses to arrange the lineup rather than to letter it.
STRUCTURAL = ["bus", "cubicle", "archetype", "extras", "deck"]


def config_columns(cfg):
    """Columns named by role text and block attributes, i.e. what the config asks for."""
    cols = set()
    for spec in cfg.get("roles", {}).values():
        if not isinstance(spec, dict):
            continue
        source = spec.get("text", "")
        if isinstance(source, str) and source and not source.startswith("@"):
            cols.add(source)
        for value in (spec.get("attribs") or {}).values():
            if isinstance(value, str) and not value.startswith("@"):
                cols.add(value)
    return cols


def role_columns_by_archetype(cfg):
    """Per archetype, the columns its own roles read -- a blank only matters for
    units that actually carry the role."""
    out = {}
    for name, arch in cfg.get("archetypes", {}).items():
        cols = set()
        for role in arch.get("roles", []):
            spec = cfg.get("roles", {}).get(role)
            if not isinstance(spec, dict):
                continue
            source = spec.get("text", "")
            if isinstance(source, str) and source and not source.startswith("@"):
                cols.add(source)
            for value in (spec.get("attribs") or {}).values():
                if isinstance(value, str) and not value.startswith("@"):
                    cols.add(value)
        out[name] = cols
    return out


def check(units_path, config_path, bom_path=None):
    cfg = json.load(open(config_path, encoding="utf-8"))
    with open(units_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = [r for r in reader if (r.get("unit") or "").strip()]

    # Resolve the deck column into archetype names exactly as the builder will,
    # so what is checked is what will be drawn rather than what was typed.
    import build_sld
    build_sld.apply_decks(rows, cfg)

    errors, notes = [], []
    print(f"=== {units_path} against {config_path} ===")
    print(f"{len(rows)} units, {len(header)} columns\n")

    # --- archetypes and extras resolve ----------------------------------
    known_arch = set(cfg.get("archetypes", {}))
    known_roles = set(cfg.get("roles", {}))
    used = Counter()
    for r in rows:
        arch = (r.get("archetype") or "").strip()
        used[arch] += 1
        if arch not in known_arch:
            errors.append(f"unit {r['unit']}: unknown archetype {arch!r}")
        for extra in (r.get("extras") or "").replace(",", " ").split():
            if extra not in known_roles:
                errors.append(f"unit {r['unit']}: unknown extra role {extra!r}")

    print("ARCHETYPES IN USE")
    for name, n in used.most_common():
        mark = "ok" if name in known_arch else "!!"
        print(f"  {mark}  {n:>3}  {name}")
    unused = sorted(known_arch - set(used))
    if unused:
        print(f"  -- defined but unused: {', '.join(unused)}")
    print()

    # --- column contract, both directions -------------------------------
    wanted = config_columns(cfg) | set(ALWAYS_READ)
    missing = sorted(wanted - set(header))
    supplied = set(header) - set(ALWAYS_READ) - set(STRUCTURAL)
    dead = sorted(supplied - config_columns(cfg))

    print("COLUMNS THE CONFIG READS BUT THE CSV DOES NOT HAVE")
    for c in missing:
        errors.append(f"missing column {c!r}")
        print(f"  !!  {c}")
    print("  (none)\n" if not missing else "")

    print("COLUMNS THE CSV HAS THAT NOTHING READS")
    for c in dead:
        filled = sum(1 for r in rows if (r.get(c) or "").strip())
        # Empty throughout is just a spare heading; populated means somebody
        # believed it did something, which is the case worth surfacing.
        flag = "!!" if filled else "--"
        print(f"  {flag}  {c:<18} {filled}/{len(rows)} rows filled")
        if filled:
            notes.append(f"{c!r} is filled on {filled} rows and never read")
    print("  (none)\n" if not dead else "")

    # --- blanks, only where the archetype actually uses the column ------
    by_arch = role_columns_by_archetype(cfg)
    blanks = defaultdict(list)
    for r in rows:
        relevant = set(ALWAYS_READ) | by_arch.get((r.get("archetype") or "").strip(), set())
        for c in relevant & set(header):
            if not (r.get(c) or "").strip():
                blanks[c].append(r["unit"])
    print("BLANK CELLS IN COLUMNS THAT UNIT'S ARCHETYPE READS")
    for c, units in sorted(blanks.items()):
        shown = ",".join(units[:12]) + ("..." if len(units) > 12 else "")
        print(f"      {c:<18} units {shown}")
    print("  (none)" if not blanks else "")
    print()

    # --- lineup structure ------------------------------------------------
    print("LINEUP STRUCTURE")
    # A repeated unit number is how a two-high cubicle names its two breakers,
    # so what has to be unique is the unit together with its deck.
    seats = [(r["unit"].strip(), (r.get("deck") or "").strip()) for r in rows]
    dupes = [s for s, n in Counter(seats).items() if n > 1]
    if dupes:
        errors.append(f"two rows share a unit and deck: {dupes}")

    declared = [(r.get("cubicle") or "").strip() for r in rows]
    if not any(declared):
        # No cubicle column: the builder groups by unit number instead.
        declared = [r["unit"].strip() for r in rows]
    if any(declared) and not all(declared):
        # assign_cubicles falls back to one cubicle per row the moment a single
        # cell is blank, so a part-filled column silently unstacks the lineup.
        blank_at = [r["unit"] for r, d in zip(rows, declared) if not d]
        errors.append(f"cubicle column part-filled (blank on units {','.join(blank_at)}) "
                      f"-- the whole lineup will fall back to one cubicle per row")
        print("  !!  cubicle column is part-filled")
    elif all(declared) and declared:
        per = Counter(declared)
        deep = [c for c, n in per.items() if n > 2]
        print(f"      {len(per)} cubicles for {len(rows)} breakers"
              f" ({sum(1 for n in per.values() if n == 2)} two-high)")
        if deep:
            errors.append(f"cubicles holding more than two breakers: {deep}")
    else:
        print(f"      {len(rows)} cubicles, single-high throughout")

    buses = Counter((r.get("bus") or "").strip() for r in rows)
    summary = ", ".join(f"{b or 'unset'}={n}" for b, n in sorted(buses.items()))
    print(f"      buses: {summary}")
    tags = [(r.get("tag") or "").strip() for r in rows]
    tag_dupes = [t for t, n in Counter(t for t in tags if t).items() if n > 1]
    if tag_dupes:
        errors.append(f"duplicate tags: {tag_dupes}")
    print()

    # --- optional cross-check against the purchase record ----------------
    if bom_path:
        cross_check_bom(bom_path, rows, notes)

    print("NOTES" if notes else "", *[f"  -  {n}" for n in notes], sep="\n")
    if errors:
        print("\nFAIL")
        for e in errors:
            print(f"  !!  {e}")
        return 1
    print("\nPASS")
    return 0


def cross_check_bom(bom_path, rows, notes):
    """Compare the lineup against what was actually bought.

    The two documents are written by different people for different reasons, so
    a disagreement is informative in both directions: the CSV may have a unit
    nobody ordered gear for, or the BOM may be incomplete. On 8508 the BOM lists
    no surge arresters at all while the drawing carries fourteen, so a mismatch
    here is a question, never a verdict.
    """
    import read_bom

    items = read_bom.load(bom_path)
    bom_units, _ = read_bom.unit_count(items)
    bom_breakers = sum(it["qty"] or 0 for it in read_bom.find(items, r"\d+kV,\s*\d+A,\s*\d+kA"))
    csv_breakers = sum(1 for r in rows if (r.get("tag") or "").strip().startswith("52"))

    print("AGAINST THE BOM")
    for label, a, b in [("cubicles", len(rows), bom_units),
                        ("breakers", csv_breakers, bom_breakers)]:
        mark = "ok" if a == b else "??"
        print(f"  {mark}  {label:<10} csv {a:>3}   bom {b if b is not None else '?':>3}")
        if a != b:
            notes.append(f"{label}: units.csv says {a}, BOM says {b}")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("units")
    ap.add_argument("--config", required=True)
    ap.add_argument("--bom", help="cross-check counts against a purchase BOM")
    a = ap.parse_args()
    sys.exit(check(a.units, a.config, a.bom))
