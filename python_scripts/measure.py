"""
measure.py

Dumps every entity of a drawing that falls inside an x/y window, sorted top
down. This is how a new archetype gets built: frame one cubicle of a reference
sheet, read the real coordinates off it, and copy them into `sld_config.json`.

Offsets in the config are relative to the cubicle centreline, so subtract the
centreline x from whatever this prints. Elevations (y) are absolute and go in
as-is.

Usage:
    python measure.py 8508+A01-000-053.dxf 24.0 27.5           # one cubicle
    python measure.py 8508+A01-000-053.dxf 24.0 27.5 9.0 15.0  # and a y band
"""

import argparse

import ezdxf


def dump(path, x0, x1, y0, y1):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    def inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    rows = []
    for e in msp:
        t = e.dxftype()
        if t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            if inside(a) or inside(b):
                rows.append((min(a[1], b[1]),
                             f"LINE   ({a[0]:8.3f},{a[1]:8.3f}) -> ({b[0]:8.3f},{b[1]:8.3f})"
                             f"  layer={e.dxf.layer}"))
        elif t == "INSERT":
            p = e.dxf.insert
            if inside(p):
                # Blank and invisible attributes are noise -- ACADE symbols
                # carry ~17 ATTDEFs each and only a few ever hold text.
                atts = " ".join(f"{a.dxf.tag}={a.dxf.text!r}" for a in e.attribs
                                if a.dxf.text.strip() and not (a.dxf.flags & 1))
                rows.append((p[1],
                             f"INSERT {e.dxf.name:14} ({p[0]:8.3f},{p[1]:8.3f})"
                             f" rot={e.dxf.rotation:6.1f} layer={e.dxf.layer}  {atts}"))
        elif t in ("MTEXT", "TEXT"):
            p = e.dxf.insert
            if inside(p):
                s = e.text if t == "MTEXT" else e.dxf.text
                h = e.dxf.char_height if t == "MTEXT" else e.dxf.height
                rows.append((p[1],
                             f"{t:6} ({p[0]:8.3f},{p[1]:8.3f}) h={h:.3f}"
                             f" layer={e.dxf.layer}  {s!r}"))
        elif t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if any(inside(p) for p in pts):
                s = " ".join(f"({p[0]:.3f},{p[1]:.3f})" for p in pts)
                rows.append((min(p[1] for p in pts),
                             f"LWPOLY closed={e.closed} layer={e.dxf.layer}  {s}"))
        elif t == "CIRCLE":
            p = e.dxf.center
            if inside(p):
                rows.append((p[1], f"CIRCLE ({p[0]:8.3f},{p[1]:8.3f})"
                                   f" r={e.dxf.radius:.3f} layer={e.dxf.layer}"))
        elif t == "ARC":
            p = e.dxf.center
            if inside(p):
                # The start/end angles identify which hop family an arc belongs
                # to: 270->90 is a riser hopping, 0->180 is a horizontal hopping.
                rows.append((p[1], f"ARC    ({p[0]:8.3f},{p[1]:8.3f}) r={e.dxf.radius:.3f}"
                                   f" {e.dxf.start_angle:.0f}->{e.dxf.end_angle:.0f}"
                                   f" layer={e.dxf.layer}"))

    rows.sort(key=lambda r: -r[0])
    for _, s in rows:
        print(s)
    print(f"\n{len(rows)} entities in x[{x0},{x1}] y[{y0},{y1}]")


def main():
    ap = argparse.ArgumentParser(description="Dump drawing entities inside an x/y window.")
    ap.add_argument("dxf")
    ap.add_argument("x0", type=float)
    ap.add_argument("x1", type=float)
    ap.add_argument("y0", type=float, nargs="?", default=-1e9)
    ap.add_argument("y1", type=float, nargs="?", default=1e9)
    args = ap.parse_args()
    dump(args.dxf, args.x0, args.x1, args.y0, args.y1)


if __name__ == "__main__":
    main()
