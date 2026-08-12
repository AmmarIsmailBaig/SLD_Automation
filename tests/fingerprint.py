"""A stable, comparable summary of a built DXF.

The point is regression detection during refactoring: two runs of build_sld.py
that are supposed to draw the same thing must produce byte-identical
fingerprints, and when they don't, the diff has to say *which* entities moved
rather than just "files differ".

What is deliberately NOT in the fingerprint: handles (fresh every run), owner
pointers, header variables, and the imported block *definitions*. Only what
lands in modelspace is compared, because that is the drawing. Block geometry is
covered indirectly -- if a symbol's internals change, the archetypes that place
it still place it at the same point, so a block edit is a library change rather
than a generator regression and belongs in its own test.

Text content IS included. MTEXT is 15% of the entities and without its string
one label is indistinguishable from another, so a fingerprint without text
would silently pass a build that put every relay label on the wrong cubicle.
"""

import json

import ezdxf

# Coordinates round here. Three decimals is finer than any dimension the
# generator places (the tightest is a 0.075 CT offset) and coarser than the
# float noise from ezdxf's DXF round-trip, so it is tight enough to catch a
# real move and loose enough not to flap.
NDIGITS = 3

FINGERPRINT_VERSION = 1

# ATTRIB flag bit 1 = invisible. build_sld.py blanks unused attributes and
# hides them rather than deleting them, so an invisible attrib carries no
# meaning and would only add noise.
ATTRIB_INVISIBLE = 1


def _n(value):
    """Round to NDIGITS and normalise -0.0, which JSON preserves."""
    r = round(float(value), NDIGITS)
    return 0.0 if r == 0.0 else r


def _pt(p, dims=2):
    return [_n(c) for c in tuple(p)[:dims]]


def _attribs(e):
    """Visible, non-empty block attributes as sorted [tag, text] pairs."""
    out = [[a.dxf.tag, a.dxf.text]
           for a in e.attribs
           if a.dxf.text.strip() and not (a.dxf.flags & ATTRIB_INVISIBLE)]
    return sorted(out)


def entity_record(e):
    """One modelspace entity as a plain, JSON-safe dict.

    Returns None for a type the generator never emits, so an unexpected type
    surfaces as a diff rather than being silently dropped.
    """
    t = e.dxftype()
    rec = {"type": t, "layer": e.dxf.layer}

    if t == "LINE":
        rec["geom"] = _pt(e.dxf.start) + _pt(e.dxf.end)

    elif t == "LWPOLYLINE":
        # 'xyb' keeps the bulge, so a rectangle that turns into a rounded
        # rectangle is a diff even though its corners did not move.
        rec["geom"] = [_n(v) for pt in e.get_points("xyb") for v in pt]
        rec["closed"] = bool(e.closed)

    elif t == "CIRCLE":
        rec["geom"] = _pt(e.dxf.center)
        rec["radius"] = _n(e.dxf.radius)

    elif t == "ARC":
        rec["geom"] = _pt(e.dxf.center)
        rec["radius"] = _n(e.dxf.radius)
        rec["angles"] = [_n(e.dxf.start_angle), _n(e.dxf.end_angle)]

    elif t == "MTEXT":
        rec["geom"] = _pt(e.dxf.insert)
        rec["text"] = e.text
        rec["height"] = _n(e.dxf.char_height)
        rec["attach"] = int(e.dxf.attachment_point)

    elif t == "INSERT":
        rec["geom"] = _pt(e.dxf.insert)
        rec["block"] = e.dxf.name
        rec["rotation"] = _n(e.dxf.get("rotation", 0.0))
        rec["scale"] = [_n(e.dxf.get("xscale", 1.0)), _n(e.dxf.get("yscale", 1.0))]
        rec["attribs"] = _attribs(e)

    else:
        return None

    return rec


def sort_key(rec):
    """Total order over records, independent of the order build_sld emitted."""
    return json.dumps(rec, sort_keys=True)


def fingerprint_dxf(path):
    """Read a built DXF and return its fingerprint payload."""
    msp = ezdxf.readfile(str(path)).modelspace()

    entities, unknown = [], {}
    for e in msp:
        rec = entity_record(e)
        if rec is None:
            unknown[e.dxftype()] = unknown.get(e.dxftype(), 0) + 1
        else:
            entities.append(rec)

    entities.sort(key=sort_key)

    counts = {}
    for rec in entities:
        counts[rec["type"]] = counts.get(rec["type"], 0) + 1

    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "ndigits": NDIGITS,
        "entity_count": len(entities),
        "entity_counts": dict(sorted(counts.items())),
        "unrecognised_types": dict(sorted(unknown.items())),
        "entities": entities,
    }


def dumps(fp):
    return json.dumps(fp, indent=2, sort_keys=True) + "\n"
