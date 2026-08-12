"""
Check a generated SLD without comparing it to anything.

compare_source.py grades a drawing by how closely it matches a reference sheet.
That is the right test when we own the reference and the wrong one otherwise:
for a standard we are drawing for the first time there is nothing to diff
against, and "looks like the last job" is not a definition of correct anyway.

So this asks the questions that have answers regardless of what was drawn
before. Does any label land on top of another? Does any symbol float
unconnected? Does the conductor actually run from the top of the unit to the
bottom, breaking only where a device interrupts it? Does anything spill outside
the cubicle that should not?

What it deliberately does not check is engineering intent. It cannot tell you
that a feeder is missing its arrester, only that everything drawn is drawn
cleanly. Catching the missing arrester needs the BOM, and catching a BOM that
omits arresters needs a person.

Usage:
    python validate_sld.py 8478_sld.dxf --config sld_config_8478.json
"""
import argparse
import re
import sys

import ezdxf
from ezdxf import bbox

# Rough advance width and line pitch of the SHX fonts these sheets use, as a
# multiple of the character height. Measured off the reference drawings rather
# than taken from a font metric, because the plotted text is what collides.
CHAR_W = 0.62
LINE_H = 1.42

# Entities the checks skip: the cubicle outline legitimately encloses
# everything, and the conductor legitimately passes through devices.
STRUCTURAL_LAYERS = {"SLD_CUBICLE"}
WIRE_LAYERS = {"SLD_WIRE", "SLD_BUS", "SLD_CONTROL"}

FORMAT_CODES = re.compile(r"\\p[^;]*;|\\[A-Za-z][^;\\]*;?|[{}]")


def mtext_box(entity):
    """
    Bounding box of an MTEXT, estimated from its own character height.

    ezdxf can report a box for MTEXT but needs font metrics to do it well, and
    the result drifts for the SHX fonts here. Estimating from character height
    and the longest line is cruder but errs consistently, which is what a
    collision check wants.
    """
    raw = entity.text
    body = FORMAT_CODES.sub("", raw)
    lines = [ln for ln in body.split("\\P")] or [""]
    h = entity.dxf.char_height
    width = max(len(ln) for ln in lines) * h * CHAR_W
    height = len(lines) * h * LINE_H
    x, y = entity.dxf.insert.x, entity.dxf.insert.y

    # attachment_point 1/2/3 = top left/centre/right; the sheets only use 1 and 2.
    ap = entity.dxf.attachment_point
    if ap in (2, 5, 8):
        x -= width / 2
    elif ap in (3, 6, 9):
        x -= width
    return (x, y - height, x + width, y)


def entity_box(entity):
    """(x0, y0, x1, y1) for anything, or None if it has no extent."""
    if entity.dxftype() == "MTEXT":
        return mtext_box(entity)
    try:
        ext = bbox.extents([entity], fast=False)
    except Exception:
        return None
    if not ext.has_data:
        return None
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def overlap(a, b, pad=0.0):
    """Overlapping area of two boxes, shrunk by pad so a touch is not a clash."""
    x0 = max(a[0], b[0]) + pad
    y0 = max(a[1], b[1]) + pad
    x1 = min(a[2], b[2]) - pad
    y1 = min(a[3], b[3]) - pad
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def contained(inner, outer, slack=0.02):
    """
    True when `inner` sits wholly inside `outer`.

    A label drawn inside the symbol it names -- FT-1 in its test switch, 86 in
    its lockout circle -- overlaps that symbol completely and on purpose. Only
    a partial overlap means two things are fighting for the same space.
    """
    return (inner[0] >= outer[0] - slack and inner[1] >= outer[1] - slack
            and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack)


def describe(entity):
    t = entity.dxftype()
    if t == "MTEXT":
        body = FORMAT_CODES.sub("", entity.text).replace("\\P", " ")
        return f"MTEXT {' '.join(body.split())[:34]!r}"
    if t == "INSERT":
        return f"INSERT {entity.dxf.name}"
    return t


def check_text_collisions(msp, pad, clearance=0.0):
    """
    Any label overlapping another label, or a symbol, is a defect.

    `clearance` additionally reports labels that clear their neighbour but only
    just. Boxes that merely touch are geometrically legal and still read as a
    collision on a plot -- the 8478 relay label started exactly where the test
    switch box ended, which looked wrong on screen and measured clean.
    """
    boxed = []
    for e in msp:
        if e.dxf.layer in STRUCTURAL_LAYERS:
            continue
        b = entity_box(e)
        if b:
            boxed.append((e, b))

    texts = [(e, b) for e, b in boxed if e.dxftype() == "MTEXT"]
    others = [(e, b) for e, b in boxed
              if e.dxftype() != "MTEXT" and e.dxf.layer not in WIRE_LAYERS]

    def grow(b):
        return (b[0] - clearance, b[1] - clearance,
                b[2] + clearance, b[3] + clearance)

    findings, tight = [], []
    for i, (ea, ba) in enumerate(texts):
        for eb, bb in texts[i + 1:]:
            if overlap(ba, bb, pad):
                findings.append(("text overlaps text", ea, eb, ba))
            elif clearance and overlap(grow(ba), bb):
                tight.append(("text nearly touches text", ea, eb, ba))
        for eb, bb in others:
            if contained(ba, bb):
                continue
            # A label whose centre sits inside a symbol belongs to it -- the
            # ANSI function number written in its circle, the rating written in
            # the breaker body. Full containment is the stricter test and fails
            # these, because the text box is only estimated from font metrics
            # and a three-character label in a 0.276 circle lands within a few
            # thousandths either way. Centre-inside decides the same question
            # without depending on that estimate.
            cx, cy = (ba[0] + ba[2]) / 2.0, (ba[1] + ba[3]) / 2.0
            if bb[0] <= cx <= bb[2] and bb[1] <= cy <= bb[3]:
                continue
            if overlap(ba, bb, pad):
                findings.append(("text overlaps symbol", ea, eb, ba))
            elif clearance and overlap(grow(ba), bb):
                tight.append(("text nearly touches symbol", ea, eb, ba))
    return findings, tight


def check_floating_symbols(msp, tol):
    """
    A symbol that touches no conductor is either misplaced or orphaned.

    Ground symbols and device bubbles legitimately sit at the end of a lead, so
    the test is contact with any wire-layer geometry, not contact with the main
    conductor specifically.
    """
    wires = []
    for e in msp:
        if e.dxf.layer not in WIRE_LAYERS:
            continue
        if e.dxftype() == "LINE":
            wires.append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)))
        elif e.dxftype() == "ARC":
            b = entity_box(e)
            if b:
                wires.append(((b[0], b[1]), (b[2], b[3])))

    findings = []
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        b = entity_box(e)
        if not b:
            continue
        grown = (b[0] - tol, b[1] - tol, b[2] + tol, b[3] + tol)
        touching = False
        for (ax, ay), (bx, by) in wires:
            seg = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
            if (grown[0] <= seg[2] and seg[0] <= grown[2]
                    and grown[1] <= seg[3] and seg[1] <= grown[3]):
                touching = True
                break
        if not touching:
            findings.append(e)
    return findings


def check_containment(msp, cfg):
    """
    Geometry outside its cubicle.

    Cable entry deliberately sits above the box -- the cable comes in from
    outside -- so the top edge is not enforced, only the sides and the bottom.

    Annotation is exempt entirely. Both reference standards park the
    destination text clear of the cubicle: 8508 below it, 8513 above it. What
    this check is really looking for is *geometry* that has escaped its unit,
    which on a lineup would mean a device drawn into its neighbour's cubicle.
    """
    sheet = cfg["sheet"]
    x0 = sheet["first_cubicle_x"]
    pitch = sheet["pitch"]
    findings = []
    for e in msp:
        if e.dxf.layer in STRUCTURAL_LAYERS or e.dxftype() == "MTEXT":
            continue
        b = entity_box(e)
        if not b:
            continue
        # Which cubicle this belongs to is decided by where its centre falls,
        # then it is checked against that cubicle's own sides. Checking against
        # the whole lineup instead would pass a device drawn squarely in its
        # neighbour's box, which is the failure this exists to catch.
        # The lineup bus is one line across every cubicle by definition, so it
        # is the one thing that is supposed to span them.
        if b[2] - b[0] > pitch and abs(b[3] - sheet["bus_y"]) < 1e-6:
            continue
        centre = (b[0] + b[2]) / 2.0
        index = int((centre - x0) // pitch)
        left = x0 + pitch * index
        if b[0] < left - 1e-6 or b[2] > left + pitch + 1e-6:
            findings.append((f"crosses into a neighbouring cubicle "
                             f"(cubicle {index + 1})", e, b))
            continue
        # The bottom edge is exempt for the conductor itself, on the same
        # grounds the top edge is exempt for cable entry: on a bottom-exit
        # lineup the cable leaves through the floor of the cubicle, so the run
        # and its terminal connector are meant to cross that line. Only things
        # off the centreline are held to it.
        on_centreline = abs(centre - (left + sheet["centreline_from_left"])) < 0.2
        if b[1] < sheet["cubicle_bottom"] - 1e-6 and not on_centreline:
            findings.append(("below cubicle bottom", e, b))
    return findings


def check_conductor(msp, cfg, archetype):
    """The power conductor should span the unit, broken only at series devices."""
    arch = cfg["archetypes"][archetype]
    top = arch.get("conductor_top")
    bottom = cfg["sheet"]["bus_y"]
    # Only lines on the unit's own centreline are the power conductor. Shunt
    # branches -- the PT tap, the CPT tap -- are vertical, on the same layer,
    # and offset, so without the x filter each one reads as another break.
    centreline = (cfg["sheet"]["first_cubicle_x"]
                  + cfg["sheet"]["centreline_from_left"])
    runs = sorted(
        (min(e.dxf.start.y, e.dxf.end.y), max(e.dxf.start.y, e.dxf.end.y))
        for e in msp
        if e.dxftype() == "LINE" and e.dxf.layer == "SLD_WIRE"
        and abs(e.dxf.start.x - e.dxf.end.x) < 1e-6
        and abs(e.dxf.start.x - centreline) < 1e-6
        and abs(e.dxf.start.y - e.dxf.end.y) > 1e-6
    )
    expected = sorted(
        (min(s["gap"]), max(s["gap"]))
        for s in (cfg["roles"][n] for n in arch["roles"])
        if isinstance(s, dict) and s.get("series") and "gap" in s
    )
    problems = []
    if not runs:
        return ["no vertical conductor drawn at all"], runs, expected
    if abs(max(r[1] for r in runs) - top) > 0.01:
        problems.append(f"conductor starts at {max(r[1] for r in runs):.3f}, "
                        f"archetype declares conductor_top {top}")
    if abs(min(r[0] for r in runs) - bottom) > 0.01:
        problems.append(f"conductor ends at {min(r[0] for r in runs):.3f}, "
                        f"sheet declares bus_y {bottom}")
    # Every break in the run should correspond to a declared series device.
    breaks = [(runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1)]
    if len(breaks) != len(expected):
        problems.append(f"{len(breaks)} break(s) in the conductor but "
                        f"{len(expected)} series device(s) declared")
    return problems, runs, expected


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dxf")
    ap.add_argument("--config", required=True)
    ap.add_argument("--archetype", help="archetype to check the conductor against")
    ap.add_argument("--pad", type=float, default=0.01,
                    help="shrink each box by this before testing overlap")
    ap.add_argument("--touch", type=float, default=0.02,
                    help="how close a symbol must be to a wire to count as connected")
    ap.add_argument("--clearance", type=float, default=0.06,
                    help="report labels clearing a neighbour by less than this")
    a = ap.parse_args()

    import build_sld
    cfg = build_sld.load_config(a.config)
    msp = ezdxf.readfile(a.dxf).modelspace()
    failures = 0

    print(f"=== {a.dxf} ===\n")

    collisions, tight = check_text_collisions(msp, a.pad, a.clearance)
    print(f"TEXT COLLISIONS: {len(collisions)}")
    for what, ea, eb, box in collisions:
        print(f"  {what}")
        print(f"     {describe(ea)}  at ({box[0]:.3f}, {box[3]:.3f})")
        print(f"     {describe(eb)}")
    failures += len(collisions)

    # Tight clearance is a legibility warning, not a defect, so it is reported
    # but does not fail the run.
    print(f"\nTIGHT CLEARANCE (< {a.clearance}): {len(tight)}")
    for what, ea, eb, box in tight:
        print(f"  {what}: {describe(ea)} / {describe(eb)}")

    floating = check_floating_symbols(msp, a.touch)
    print(f"\nUNCONNECTED SYMBOLS: {len(floating)}")
    for e in floating:
        print(f"  {describe(e)} at ({e.dxf.insert.x:.3f}, {e.dxf.insert.y:.3f})")
    failures += len(floating)

    outside = check_containment(msp, cfg)
    print(f"\nOUTSIDE THE CUBICLE: {len(outside)}")
    for what, e, b in outside:
        print(f"  {what}: {describe(e)}  x {b[0]:.3f}..{b[2]:.3f}  y {b[1]:.3f}..{b[3]:.3f}")
    failures += len(outside)

    if a.archetype:
        problems, runs, expected = check_conductor(msp, cfg, a.archetype)
        print(f"\nCONDUCTOR: {len(runs)} segment(s), "
              f"{len(expected)} series device(s) declared")
        for r in runs:
            print(f"    y {r[0]:8.3f} .. {r[1]:8.3f}")
        for p in problems:
            print(f"  !! {p}")
        failures += len(problems)

    print(f"\n{'PASS' if not failures else str(failures) + ' FINDING(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
