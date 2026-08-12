"""Turn two fingerprints into a report a human can act on.

A plain list diff answers "did it change". During a refactor the useful
question is "what moved, and by how much" -- a uniform shift across 40
entities means a datum changed, while two entities moving on their own means
an archetype did. So records are paired up by identity (everything except
position) and reported as moves with their delta, with identical deltas
collapsed into one line.
"""

import json

from fingerprint import NDIGITS, sort_key

# How many example entities to print per group before truncating.
MAX_EXAMPLES = 8

# Fields that describe *where* an entity is. Everything else is its identity.
POSITIONAL = {"geom"}


def _identity(rec):
    return json.dumps({k: v for k, v in rec.items() if k not in POSITIONAL},
                      sort_keys=True)


def _stride(rec):
    """Numbers per vertex in rec['geom']: polylines carry a bulge, others don't."""
    return 3 if rec["type"] == "LWPOLYLINE" else 2


def _coord_pairs(rec):
    s = _stride(rec)
    g = rec["geom"]
    return [(g[i], g[i + 1]) for i in range(0, len(g) - 1, s)]


def _distance(a, b):
    """Squared distance between two geom vectors, or None if incomparable."""
    if len(a["geom"]) != len(b["geom"]):
        return None
    return sum((x - y) ** 2 for x, y in zip(a["geom"], b["geom"]))


def _uniform_delta(old, new):
    """(dx, dy) if every vertex shifted the same way, else None."""
    po, pn = _coord_pairs(old), _coord_pairs(new)
    if not po or len(po) != len(pn):
        return None
    d = (round(pn[0][0] - po[0][0], NDIGITS), round(pn[0][1] - po[0][1], NDIGITS))
    for (ox, oy), (nx, ny) in zip(po, pn):
        if (round(nx - ox, NDIGITS), round(ny - oy, NDIGITS)) != d:
            return None
    return d


def _label(rec):
    """Short human description: what the entity is and where it sits."""
    bits = [rec["type"]]
    if "block" in rec:
        bits.append(rec["block"])
    bits.append(rec["layer"])
    if "text" in rec:
        t = rec["text"].replace("\n", " ")
        bits.append(repr(t[:40] + ("..." if len(t) > 40 else "")))
    at = " ".join(f"({x:.3f},{y:.3f})" for x, y in _coord_pairs(rec)[:2])
    return f"{' '.join(bits)}  {at}"


def _multiset_diff(golden, current):
    """Records only-in-golden and only-in-current, keeping duplicates."""
    pool = {}
    for rec in current:
        pool.setdefault(sort_key(rec), []).append(rec)

    removed = []
    for rec in golden:
        k = sort_key(rec)
        if pool.get(k):
            pool[k].pop()
        else:
            removed.append(rec)

    added = [r for recs in pool.values() for r in recs]
    return removed, added


def _pair_moves(removed, added):
    """Greedily match removed to added within identity groups.

    Returns (moves, removed_leftover, added_leftover) where each move is an
    (old, new) pair of the same kind of entity in a different place.
    """
    by_identity = {}
    for rec in added:
        by_identity.setdefault(_identity(rec), []).append(rec)

    moves, leftover = [], []
    for old in removed:
        candidates = by_identity.get(_identity(old))
        if not candidates:
            leftover.append(old)
            continue
        best, best_d = None, None
        for cand in candidates:
            d = _distance(old, cand)
            if d is not None and (best_d is None or d < best_d):
                best, best_d = cand, d
        if best is None:
            leftover.append(old)
            continue
        candidates.remove(best)
        moves.append((old, best))

    added_left = [r for recs in by_identity.values() for r in recs]
    return moves, leftover, added_left


def _section(lines, title, items, render):
    if not items:
        return
    lines.append(f"  {title}")
    for item in items[:MAX_EXAMPLES]:
        lines.append(f"    {render(item)}")
    if len(items) > MAX_EXAMPLES:
        lines.append(f"    ... and {len(items) - MAX_EXAMPLES} more")
    lines.append("")


def describe(golden, current, name):
    """Human-readable report, or '' when the fingerprints match."""
    if golden.get("entities") == current.get("entities"):
        return ""

    g, c = golden["entities"], current["entities"]
    lines = [
        f"{name}: built geometry differs from tests/golden/{name}.json",
        f"  entities: {len(g)} golden -> {len(c)} current",
        "",
    ]

    gc, cc = golden.get("entity_counts", {}), current.get("entity_counts", {})
    changed = [f"{t}: {gc.get(t, 0)} -> {cc.get(t, 0)}"
               for t in sorted(set(gc) | set(cc)) if gc.get(t, 0) != cc.get(t, 0)]
    if changed:
        lines.append("  counts by type: " + ", ".join(changed))
        lines.append("")

    if current.get("unrecognised_types"):
        lines.append(f"  unrecognised entity types: {current['unrecognised_types']}")
        lines.append("")

    removed, added = _multiset_diff(g, c)
    moves, removed, added = _pair_moves(removed, added)

    # Collapse identical shifts: one line for "the whole lineup slid 0.5 right"
    # beats forty lines saying it entity by entity.
    uniform, varying = {}, []
    for old, new in moves:
        d = _uniform_delta(old, new)
        if d is None:
            varying.append((old, new))
        else:
            uniform.setdefault(d, []).append((old, new))

    for (dx, dy), pairs in sorted(uniform.items(), key=lambda kv: -len(kv[1])):
        _section(lines, f"MOVED {len(pairs)} by ({dx:+.3f}, {dy:+.3f}):", pairs,
                 lambda p: _label(p[0]))

    _section(lines, f"MOVED {len(varying)} (reshaped, not a plain shift):", varying,
             lambda p: f"{_label(p[0])}\n      -> {_label(p[1])}")

    _section(lines, f"ADDED {len(added)}:", added, _label)
    _section(lines, f"REMOVED {len(removed)}:", removed, _label)

    lines.append("  If this change is intended, re-record with:")
    lines.append(f"    python tests/regen_golden.py {name}")
    return "\n".join(lines)
