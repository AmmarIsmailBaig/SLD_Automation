"""
derive_archetype.py

Turns one cubicle of a reference drawing into a draft archetype for
`sld_config.json`: roles with `dx` already measured from that cubicle's
centreline, and `y` copied through absolute.

This is the same measurement `measure.py` prints, but expressed in the frame
the config actually stores. Retyping sixty coordinates out of a dump is slow
and the errors it produces are silent -- 16.128 read as 16.218 looks perfectly
plausible until someone builds the panel -- so the arithmetic is done here and
the remaining work is curation.

It is deliberately a *draft*. Blocks, notes, boxes and bubbles come out
mechanically because each is one entity pinned at one point. Lines do not: a
LINE in the source may be main conductor, control wiring, or a lead on some
device, and telling those apart is a reading of the drawing rather than a
measurement. Those are listed unclassified at the end for hand assignment.

The centreline rule is imported from `compare_source.py` rather than
reimplemented, so offsets derived here and deltas reported there share an
origin.

Usage:
    python derive_archetype.py 8513+A01-000-052.dxf --cubicle 2 \
        --name feeder_upper --y 12.609 19.400
    python derive_archetype.py ref.dxf --cubicle 6 --centre 17.559 --name tie
"""

import argparse
import json

import ezdxf

from compare_source import IGNORE_LAYERS, cubicles

ATTRIB_INVISIBLE = 1

# A closed box this size holding a 52- label is a drawout breaker, not a
# relay outline; it is the one box that interrupts the main conductor.
BREAKER_W = (0.70, 0.85)
BREAKER_H = (0.85, 1.00)
BUBBLE_R = (0.15, 0.25)


def slug(text, dx, y):
    """
    A unique, traceable draft name: what it is plus where it sits.

    Both coordinates are needed, not just the elevation. The arrester tap puts
    two identical circles on one line at y=17.879, so keying on elevation alone
    silently drops one of them -- roles live in a dict, and the second write
    wins.
    """
    base = "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")
    pos = f"{dx:+.3f}_{y:.3f}".replace(".", "p").replace("-", "n").replace("+", "")
    return f"{base or 'item'}_{pos}"


def visible_attribs(e):
    return {a.dxf.tag: a.dxf.text for a in e.attribs
            if a.dxf.text.strip() and not (a.dxf.flags & ATTRIB_INVISIBLE)}


def derive(path, cubicle_no, name, y0, y1, centre_override):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    overrides = {cubicle_no: centre_override} if centre_override is not None else {}
    boxes = cubicles(msp, overrides)
    if not 1 <= cubicle_no <= len(boxes):
        raise SystemExit(f"cubicle {cubicle_no} out of range (1..{len(boxes)})")
    x0, x1, centre = boxes[cubicle_no - 1]

    def keep(p):
        return x0 <= p[0] < x1 and y0 <= p[1] <= y1

    roles, order, unclassified = {}, [], []

    for e in msp:
        if e.dxf.layer in IGNORE_LAYERS:
            continue
        t = e.dxftype()

        if t == "INSERT":
            p = e.dxf.insert
            if not keep(p):
                continue
            spec = {"kind": "block", "block": e.dxf.name,
                    "dx": round(p[0] - centre, 3), "y": round(p[1], 3),
                    "rotation": round(e.dxf.rotation, 1), "series": False}
            atts = visible_attribs(e)
            if atts:
                # Literals for now. Anything that varies per unit -- CT ratios,
                # ratings, tags -- has to be repointed at a units.csv column by
                # dropping the '@'.
                spec["attribs"] = {k: "@" + v for k, v in atts.items()}
                spec["_todo"] = "check which attribs should be CSV columns, not literals"
            key = slug(e.dxf.name, p[0] - centre, p[1])
            roles[key] = spec
            order.append(key)

        elif t in ("MTEXT", "TEXT"):
            p = e.dxf.insert
            if not keep(p):
                continue
            raw = e.text if t == "MTEXT" else e.dxf.text
            clean = raw.replace("\\pxqc;", "")
            height = e.dxf.char_height if t == "MTEXT" else e.dxf.height
            key = slug(clean.replace("\\P", " ")[:18], p[0] - centre, p[1])
            roles[key] = {"kind": "note",
                          "dx": round(p[0] - centre, 3), "y": round(p[1], 3),
                          "height": round(height, 3),
                          "align": "centre" if "\\pxqc;" in raw else "left",
                          "text": "@" + clean,
                          "_todo": "literal text -- repoint at a CSV column if it varies"}
            order.append(key)

        elif t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if not any(keep(p) for p in pts):
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if h > 10.0:
                continue                      # the cubicle outline itself
            is_breaker = (BREAKER_W[0] < w < BREAKER_W[1]
                          and BREAKER_H[0] < h < BREAKER_H[1])
            spec = {"kind": "breaker" if is_breaker else "box",
                    "dx": round(min(xs) - centre, 3), "width": round(w, 3),
                    "y_top": round(max(ys), 3), "y_bottom": round(min(ys), 3),
                    "series": bool(is_breaker)}
            if is_breaker:
                spec["gap"] = [round(max(ys), 3), round(min(ys), 3)]
            else:
                spec["layer"] = e.dxf.layer
            key = slug("breaker" if is_breaker else "box", min(xs) - centre, min(ys))
            roles[key] = spec
            order.append(key)

        elif t == "CIRCLE":
            p = e.dxf.center
            if not keep(p):
                continue
            r = e.dxf.radius
            key = slug("bubble" if BUBBLE_R[0] < r < BUBBLE_R[1] else "circle", p[0] - centre, p[1])
            roles[key] = {"kind": "circle",
                          "dx": round(p[0] - centre, 3), "y": round(p[1], 3),
                          "radius": round(r, 3), "layer": e.dxf.layer,
                          "_todo": ("looks like a device bubble -- consider kind "
                                    "'bubble' with a label and leads"
                                    if BUBBLE_R[0] < r < BUBBLE_R[1] else "")}
            order.append(key)

        elif t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            if keep(a) or keep(b):
                unclassified.append(
                    f"LINE ({a[0] - centre:+.3f},{a[1]:.3f}) -> "
                    f"({b[0] - centre:+.3f},{b[1]:.3f})  layer={e.dxf.layer}")
        elif t == "ARC":
            p = e.dxf.center
            if keep(p):
                unclassified.append(
                    f"ARC  ({p[0] - centre:+.3f},{p[1]:.3f}) r={e.dxf.radius:.3f} "
                    f"{e.dxf.start_angle:.0f}->{e.dxf.end_angle:.0f}  "
                    "(probably a generated hop -- usually no role needed)")

    order.sort(key=lambda k: -roles[k].get("y", roles[k].get("y_top", 0)))
    draft = {
        "_derived_from": f"{path} cubicle {cubicle_no} "
                         f"(x {x0:.3f}..{x1:.3f}, centreline {centre:.3f}, "
                         f"y {y0:.3f}..{y1:.3f})",
        "roles": {k: roles[k] for k in order},
        "archetype": {name: {"description": f"DRAFT -- {name}", "roles": order}},
        "_unclassified": unclassified,
    }
    return draft


def main():
    ap = argparse.ArgumentParser(description="Draft an archetype from a reference cubicle.")
    ap.add_argument("dxf")
    ap.add_argument("--cubicle", type=int, required=True, help="1-based, left to right")
    ap.add_argument("--name", default="draft_archetype")
    ap.add_argument("--y", nargs=2, type=float, default=[-1e9, 1e9],
                    metavar=("Y0", "Y1"),
                    help="elevation band, to split one cubicle into two decks")
    ap.add_argument("--centre", type=float,
                    help="force the centreline (the tie cubicle needs this)")
    ap.add_argument("-o", "--out", help="write JSON here instead of stdout")
    args = ap.parse_args()

    draft = derive(args.dxf, args.cubicle, args.name, args.y[0], args.y[1], args.centre)
    text = json.dumps(draft, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {args.out}: {len(draft['roles'])} roles, "
              f"{len(draft['_unclassified'])} unclassified lines")
    else:
        print(text)


if __name__ == "__main__":
    main()
