"""
compare_source.py

Diffs a generated sheet against the reference drawing it is meant to reproduce,
cubicle by cubicle, and reports what is missing, extra or displaced.

The comparison is deliberately *not* sheet-absolute. Cubicle widths on the
reference vary (8513 runs 3.067 to 3.610) while generated lineups use one pitch
for all of them, so absolute x diverges further with every cubicle and would
report the whole sheet as displaced. Instead each entity is re-expressed as an
offset from its own cubicle's centreline, which is exactly the frame the config
stores geometry in -- so a delta here is a delta in the config.

Centrelines are detected rather than configured: the main power conductor is
the longest vertical run inside a cubicle, on both drawings. That way the tool
needs no config and works against any reference sheet.

Usage:
    python compare_source.py 8513+A01-000-052.dxf 8513_full.dxf
    python compare_source.py ref.dxf built.dxf --tol 0.01 --ignore-text
"""

import argparse
import collections
import re

import ezdxf

# Render junk and sheet furniture: present on the reference, never generated,
# and not part of the electrical drawing.
IGNORE_LAYERS = {"INFORBORDER", "ASHADE", "DEFPOINTS", "VIEWPORTS"}

CUBICLE_MIN_H = 10.0


def cubicles(msp, overrides=None):
    """
    The tall boxes, left to right, as (x0, x1, centreline).

    `overrides` maps a 1-based cubicle number to an explicit centreline, for
    the cubicles where detection cannot work: the tie breaker is horizontal and
    so its cubicle holds no vertical power conductor at all, leaving the
    longest vertical to be some incidental control lead.

    Any consistent choice of centreline is workable as long as this function is
    the only one making it -- config offsets are stored relative to whatever
    origin it returns, so `derive_archetype.py` imports it rather than
    reimplementing the rule.
    """
    overrides = overrides or {}
    boxes = []
    for e in msp.query("LWPOLYLINE"):
        pts = [(p[0], p[1]) for p in e.get_points("xy")]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if max(ys) - min(ys) > CUBICLE_MIN_H:
            boxes.append((min(xs), max(xs)))
    boxes = sorted(set(boxes))

    # Longest vertical line inside the box is the main conductor. Sheet
    # furniture has to be excluded here as well as from the comparison itself:
    # the title-block rules are long, vertical and sit inside a cubicle's x
    # span, so without the filter the tie cubicle centres on a border line.
    verticals = []
    for e in msp.query("LINE"):
        if e.dxf.layer in IGNORE_LAYERS:
            continue
        a, b = e.dxf.start, e.dxf.end
        if abs(a[0] - b[0]) < 1e-6:
            verticals.append((a[0], abs(a[1] - b[1])))

    out = []
    for n, (x0, x1) in enumerate(boxes, start=1):
        if n in overrides:
            centre = overrides[n]
        else:
            inside = [(length, x) for x, length in verticals if x0 < x < x1]
            centre = max(inside)[1] if inside else (x0 + x1) / 2
        out.append((x0, x1, centre))
    return out


def parse_centres(values):
    """`--centre 6=17.559` -> {6: 17.559}."""
    out = {}
    for v in values or []:
        n, _, x = v.partition("=")
        out[int(n)] = float(x)
    return out


def ident(e, ignore_text):
    """
    What makes two entities the same kind of thing.

    MTEXT formatting codes are stripped before comparing. The reference centres
    a paragraph with the inline code `\\pxqc;` while the generator centres by
    attachment point; both plot identically, so leaving the code in reports
    every centred label as a difference in content it does not have.
    """
    t = e.dxftype()
    if t == "INSERT":
        return e.dxf.name
    if t in ("MTEXT", "TEXT"):
        if ignore_text:
            return ""
        raw = e.text if t == "MTEXT" else e.dxf.text
        raw = raw.replace("\\P", " ")
        raw = re.sub(r"\\p[^;]*;", "", raw)           # paragraph properties
        raw = re.sub(r"\\[A-Za-z][^\\;]*;?", "", raw)  # font, height, colour...
        raw = raw.replace("{", "").replace("}", "")
        return " ".join(raw.split())[:40]
    return ""


def anchor(e):
    """The point an entity is pinned by, or None if it has no single anchor."""
    t = e.dxftype()
    d = e.dxf
    if t in ("INSERT", "MTEXT", "TEXT"):
        return d.insert[0], d.insert[1]
    if t in ("CIRCLE", "ARC"):
        return d.center[0], d.center[1]
    if t == "LINE":
        return min(d.start[0], d.end[0]), min(d.start[1], d.end[1])
    if t == "LWPOLYLINE":
        pts = list(e.get_points("xy"))
        return min(p[0] for p in pts), min(p[1] for p in pts)
    return None


def extent(e, q=1000.0):
    """
    A size signature, so a short line does not match a long one.

    Quantised by the same tolerance as position, not by a fixed number of
    decimals. The reference is hand-drafted and its coordinates carry full
    float noise -- a conductor running 15.59775..18.57236 is 2.97461 long,
    which rounds to 2.975, while the same run rebuilt from a config storing
    three decimals is 2.974. Rounding sizes independently of `--tol` turns that
    into a spurious missing-and-extra pair for every entity that lands near a
    boundary.
    """
    t = e.dxftype()
    d = e.dxf
    if t == "LINE":
        return round(abs(d.end[0] - d.start[0]) * q), round(abs(d.end[1] - d.start[1]) * q)
    if t in ("CIRCLE", "ARC"):
        return (round(d.radius * q),)
    if t == "LWPOLYLINE":
        pts = list(e.get_points("xy"))
        return (round((max(p[0] for p in pts) - min(p[0] for p in pts)) * q),
                round((max(p[1] for p in pts) - min(p[1] for p in pts)) * q))
    return ()


def collect(msp, boxes, ignore_text, tol, band=None):
    """
    Bucket entities by cubicle, keyed relative to that cubicle's centreline.

    `band` restricts the comparison to an elevation range, which is how one
    deck of a two-high cubicle is checked while the other is still unbuilt --
    without it every entity of the missing deck reports as a difference and
    buries the ones that matter.
    """
    q = 1.0 / tol
    buckets = collections.defaultdict(collections.Counter)
    for e in msp:
        if e.dxf.layer in IGNORE_LAYERS:
            continue
        p = anchor(e)
        if p is None:
            continue
        if band and not (band[0] <= p[1] <= band[1]):
            continue
        # The cubicle outline itself is excluded: its width is the one thing
        # the two drawings are known to disagree about, so comparing it would
        # report a difference already accounted for.
        if e.dxftype() == "LWPOLYLINE" and extent(e, q)[1] > CUBICLE_MIN_H * q:
            continue
        for i, (x0, x1, centre) in enumerate(boxes):
            if x0 <= p[0] < x1:
                key = (e.dxftype(), ident(e, ignore_text),
                       round((p[0] - centre) * q), round(p[1] * q), extent(e, q))
                buckets[i][key] += 1
                break
    return buckets


def describe(key, tol):
    t, name, dx, y, size = key
    sz = " ".join(f"{v * tol:g}" for v in size)
    return (f"{t:11s} {name[:28]:28s} dx={dx * tol:+7.3f} y={y * tol:8.3f}"
            + (f"  [{sz}]" if sz else ""))


def main():
    ap = argparse.ArgumentParser(description="Diff a generated sheet against its reference.")
    ap.add_argument("reference")
    ap.add_argument("generated")
    ap.add_argument("--tol", type=float, default=0.005,
                    help="coordinate tolerance in drawing units (default 0.005)")
    ap.add_argument("--ignore-text", action="store_true",
                    help="match text by position only, not content")
    ap.add_argument("--cubicle", type=int, help="report only this cubicle (1-based)")
    ap.add_argument("--limit", type=int, default=12,
                    help="max lines shown per bucket per cubicle")
    ap.add_argument("--centre", action="append", metavar="N=X",
                    help="force cubicle N's centreline (repeatable)")
    ap.add_argument("--gen-centre", action="append", metavar="N=X",
                    help="same, for the generated drawing")
    ap.add_argument("--y", nargs=2, type=float, metavar=("Y0", "Y1"),
                    help="restrict to an elevation band (one deck of a two-high cubicle)")
    args = ap.parse_args()

    ref_msp = ezdxf.readfile(args.reference).modelspace()
    gen_msp = ezdxf.readfile(args.generated).modelspace()

    ref_boxes = cubicles(ref_msp, parse_centres(args.centre))
    gen_boxes = cubicles(gen_msp, parse_centres(args.gen_centre))
    print(f"reference {args.reference}: {len(ref_boxes)} cubicles")
    print(f"generated {args.generated}: {len(gen_boxes)} cubicles")
    if len(ref_boxes) != len(gen_boxes):
        print("  ! cubicle counts differ -- comparing the overlap, left to right")
    print()

    ref = collect(ref_msp, ref_boxes, args.ignore_text, args.tol, args.y)
    gen = collect(gen_msp, gen_boxes, args.ignore_text, args.tol, args.y)

    total_missing = total_extra = total_match = 0
    for i in range(min(len(ref_boxes), len(gen_boxes))):
        if args.cubicle and i + 1 != args.cubicle:
            continue
        r, g = ref[i], gen[i]
        missing, extra = r - g, g - r
        matched = sum((r & g).values())
        total_match += matched
        total_missing += sum(missing.values())
        total_extra += sum(extra.values())

        x0, x1, centre = ref_boxes[i]
        status = "OK" if not missing and not extra else ""
        print(f"--- cubicle {i + 1}  ref x {x0:.3f}..{x1:.3f} centre {centre:.3f}  "
              f"matched {matched}, missing {sum(missing.values())}, "
              f"extra {sum(extra.values())} {status}")
        for label, bag in (("missing", missing), ("extra", extra)):
            for key, n in list(bag.most_common(args.limit)):
                print(f"    {label:7s} x{n} {describe(key, args.tol)}")
            if len(bag) > args.limit:
                print(f"    {label:7s} ... {len(bag) - args.limit} more distinct")

    print(f"\nTOTAL matched {total_match}, missing {total_missing}, extra {total_extra}")
    return 0 if not (total_missing or total_extra) else 1


if __name__ == "__main__":
    raise SystemExit(main())
