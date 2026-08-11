"""
Read a switchgear BOM and report what it pins down about the lineup.

A BOM is an inventory, not an arrangement. It says how many breakers were
bought and what CT sits in unit 7; it does not say which cubicle is the tie or
what feeder 3 feeds. So this reports two lists: what the counts determine, and
what they leave open. The second list is the part a human still has to supply,
and printing it is the point -- a silent gap becomes an invented drawing.

Counting works off the per-unit items. Anything fitted once per cubicle (test
switch, local DC breaker, heater MCB, potential indicator) counts the cubicles,
and items fitted n-per-unit divide down to the same number. When those answers
disagree the BOM is inconsistent and we say so rather than picking one.
"""
import argparse
import re
from collections import Counter

import openpyxl

# Items fitted exactly once per cubicle, as (regex, divisor). The divisor is
# how many of that item each cubicle gets, so quantity/divisor is the cubicle
# count from that row alone.
PER_UNIT = [
    (r"FT-1 test switch.*C1-C1", 1, "CT test switch"),
    (r"Position switch", 1, "position switch"),
    (r"Temp& ?humidity monitor", 1, "temp/humidity monitor"),
    (r"Potential indicator", 1, "potential indicator"),
    (r"Heater MCB", 1, "heater MCB"),
    (r"Lamp ", 1, "unit lamp"),
    (r"UL 489 MCCB, 125/250 VDC", 1, "local 125VDC MCB"),
    (r"100W .*heater", 2, "unit heaters (2/unit)"),
    (r"Power Distribution Block", 2, "Mersen PDB (2/unit)"),
]

CT_RE = re.compile(r"(\d+(?:-\d+)?)/5(?:/5)?A")
BREAKER_RE = re.compile(r"(\d+(?:\.\d+)?)kV,\s*(\d+)A,\s*(\d+)kA")


def cell_int(value):
    """Quantities are sometimes prose ('30 fuses, 15 holders')."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def load(path, sheet=None):
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() if c else "" for c in rows[0]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    ci, cq, ca = col("description"), col("quantity"), col("application")
    cp, cm = col("part number"), col("manufacturer")
    out = []
    for r in rows[1:]:
        desc = str(r[ci]).replace("\n", " ").strip() if ci is not None and r[ci] else ""
        if not desc:
            continue
        out.append({
            "desc": desc,
            "qty": cell_int(r[cq]) if cq is not None else None,
            "qty_raw": r[cq] if cq is not None else None,
            "app": str(r[ca]).strip() if ca is not None and r[ca] else "",
            "part": str(r[cp]).replace("\n", " ").strip() if cp is not None and r[cp] else "",
            "mfr": str(r[cm]).strip() if cm is not None and r[cm] else "",
        })
    return out


def find(items, pattern, field="desc"):
    rx = re.compile(pattern, re.I)
    return [it for it in items if rx.search(it[field])]


def unit_count(items):
    """Every per-unit item votes; agreement is the answer, disagreement is a fault."""
    votes = {}
    for pattern, divisor, label in PER_UNIT:
        for it in find(items, pattern):
            if it["qty"]:
                q, rem = divmod(it["qty"], divisor)
                votes[label] = q if not rem else None
    tally = Counter(v for v in votes.values() if v)
    return (tally.most_common(1)[0][0] if tally else None), votes


def report(path, sheet=None):
    items = load(path, sheet)
    units, votes = unit_count(items)

    print(f"=== {path} : {len(items)} line items ===\n")

    print("CUBICLE COUNT")
    for label, n in votes.items():
        mark = "ok" if n == units else "??"
        print(f"  {mark}  {n!s:>4}  {label}")
    print(f"  -> {units} cubicles\n")

    print("BREAKERS")
    breakers = 0
    for it in find(items, r"\d+kV,\s*\d+A,\s*\d+kA"):
        m = BREAKER_RE.search(it["desc"])
        breakers += it["qty"] or 0
        print(f"  {it['qty']:>3} x {m.group(1)}kV {m.group(2)}A {m.group(3)}kA"
              f"   [{it['mfr']} {it['part']}]")
    print(f"  -> {breakers} breakers, {units - breakers} cubicles without one\n")

    print("CURRENT TRANSFORMERS  (quantities are single CTs; /3 = three-phase sets)")
    ct_sets = 0
    # A CT row can be recognised by its description or by its application, and
    # several answer to both -- dedupe on identity so they are counted once.
    seen = set()
    candidates = find(items, r"\d+/5.*A.*5P20|CT", "desc") + find(items, r"CT", "app")
    for it in candidates:
        if id(it) in seen or "/5" not in it["desc"]:
            continue
        seen.add(id(it))
        sets, rem = divmod(it["qty"] or 0, 3)
        ct_sets += sets
        ratio = CT_RE.search(it["desc"])
        note = "" if not rem else f"  (!! {it['qty']} is not a multiple of 3)"
        print(f"  {sets:>3} sets  {ratio.group(0) if ratio else '?':<16} {it['app']}{note}")
    print(f"  -> {ct_sets} CT sets for {breakers} breakers\n")

    print("POTENTIAL TRANSFORMERS")
    for it in find(items, r"[Pp]otential transformer"):
        per_phase = "per phase" in it["desc"].lower()
        sets = (it["qty"] or 0) // 3 if per_phase else it["qty"]
        print(f"  {sets} sets ({it['qty']} PTs, 1 per phase)   {it['app']}")
    print()

    print("PROTECTION AND METERING")
    for pat, what in [(r"751", "feeder protection relay"),
                      (r"735", "revenue meter"),
                      (r"850.*[Dd]ifferential|High-Impedance Differential", "bus differential relay"),
                      (r"86 lockout", "86 lockout")]:
        for it in find(items, pat):
            print(f"  {it['qty']:>3} x {what:<28} {it['app']}")
    print()

    print("AUXILIARY / NON-BREAKER EQUIPMENT")
    for pat in [r"Lighting Panel", r"Power Panel", r"XFMR", r"Fusible Switch"]:
        for it in find(items, pat):
            print(f"  {it['qty']:>3} x {it['desc'][:56]}")
    print()

    print("EXPLICIT UNIT REFERENCES IN THE BOM")
    for it in items:
        for m in re.finditer(r"units?\s*(\d+)\s*&\s*(\d+)", it["app"], re.I):
            ratio = CT_RE.search(it["desc"])
            print(f"  units {m.group(1)} and {m.group(2)}: {ratio.group(0) if ratio else it['desc'][:40]}")
    print()

    print("NOT DETERMINED BY THIS BOM  -- must come from a lineup schedule")
    for line in [
        "position of each cubicle in the lineup (left-to-right order)",
        f"which of the {units} cubicles are mains, ties, feeders, PT or auxiliary",
        "single-high or two-high construction",
        "bus arrangement (main-tie-main? where the tie sits, which units are on which bus)",
        "breaker tag for each unit (52-M1A, 52-F1A, ...)",
        "what each feeder feeds (the destination text)",
        "surge arresters -- none appear in this BOM at all",
        "cubicle width and height (physical gear dimensions)",
        "cable entry direction (top or bottom) per unit",
    ]:
        print(f"  - {line}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bom")
    ap.add_argument("--sheet")
    a = ap.parse_args()
    report(a.bom, a.sheet)
