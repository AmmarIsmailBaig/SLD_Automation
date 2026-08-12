"""
Compute role elevations from a device stack instead of measuring them.

Every archetype written so far carries an absolute `y` on each role, lifted off
a reference drawing with a ruler. That works only for standards we already own
a drawing of. This module lets an archetype instead declare the *order* devices
sit in along the conductor and how much room to leave between them, and derives
the elevations from the symbol library's own geometry.

The transform runs at config-load time and emits exactly the shape the engine
already consumes -- every role ends up with a concrete `y`, `y_top`/`y_bottom`
or `points`. Nothing downstream knows a stack was involved, and a config
without a `stack` key is passed through untouched, so the measured 8508 and
8513 standards are unaffected.

Two mechanisms, and between them no coordinate needs to be typed:

  stack   an ordered run of devices down (or up) the conductor. Each entry
          either names a role, which consumes that block's real height from
          the library, or declares a gap, which is free space. The cursor
          starts at `start_y` and walks.

  anchor  for everything that is not on the conductor -- phase marks, relay
          boxes, labels, leads. The role names the stacked role it hangs off
          and its offset from it, so moving a device drags its satellites
          along instead of stranding them.

Gaps between devices are a drafting choice, not an engineering fact: measuring
8513 showed the same two CTs spaced 0.944 apart on one deck and 0.772 on the
other. The breaker span, by contrast, matched to 0.001 -- that is a real
dimension and it comes from the library, never from a config.
"""
import ezdxf
from ezdxf import bbox

# Roles that occupy room on the conductor but are not library blocks have to
# say how tall they are; a block is measured instead.
SIZED_KINDS = {"breaker", "box"}


class StackError(Exception):
    pass


def block_extents(library_path):
    """
    Height and origin offset of every block in the symbol library.

    Returns {name: (bottom, top)} relative to the block origin, so a role
    placed at y has its geometry spanning y+bottom .. y+top. ATTDEFs are
    excluded: an attribute's default position is a label convention and
    stretches the box well past the device it names.
    """
    doc = ezdxf.readfile(library_path)
    out = {}
    for block in doc.blocks:
        if block.name.startswith(("*", "_")):
            continue
        ents = [e for e in block if e.dxftype() != "ATTDEF"]
        if not ents:
            continue
        try:
            ext = bbox.extents(ents, fast=True)
        except Exception:
            continue
        out[block.name] = (ext.extmin.y, ext.extmax.y)
    return out


def _height(name, spec, extents):
    """How much conductor a role consumes."""
    if "height" in spec:
        return spec["height"]
    if spec.get("kind") == "block":
        block = spec.get("block")
        if block not in extents:
            raise StackError(f"role {name!r} uses block {block!r}, "
                             f"which is not in the symbol library")
        lo, hi = extents[block]
        return hi - lo
    if spec.get("kind") in SIZED_KINDS:
        raise StackError(f"role {name!r} is a {spec['kind']} on the stack and "
                         f"must declare a 'height'")
    return 0.0


def _place(name, spec, top, extents):
    """
    Put a role's geometry so its top edge lands on `top`, and return the
    elevation the cursor should continue from.

    A block's insertion point is its origin, which is rarely its top edge --
    VCN1PJ's origin sits 0.062 below its own top -- so the offset has to come
    out of the library rather than being assumed to be zero.
    """
    kind = spec.get("kind", "block")
    height = _height(name, spec, extents)
    bottom = top - height

    if kind == "block":
        lo, hi = extents[spec["block"]]
        spec["y"] = top - hi
        # A series device interrupts the conductor across its own body unless
        # the archetype says otherwise.
        if spec.get("series") and "gap" not in spec:
            spec["gap"] = [top, bottom]
    elif kind in SIZED_KINDS:
        spec["y_top"] = top
        spec["y_bottom"] = bottom
        if spec.get("series") and "gap" not in spec:
            spec["gap"] = [top, bottom]
    else:
        spec["y"] = top

    return bottom


def apply_stack(archetype_name, arch, roles, extents):
    """Walk one archetype's stack, writing elevations into its roles."""
    stack = arch["stack"]
    cursor = stack["start_y"]
    default_gap = stack.get("default_gap", 0.0)
    placed = {}
    first = True

    for entry in stack["items"]:
        if "gap" in entry and "role" not in entry:
            cursor -= entry["gap"]
            continue
        name = entry.get("role")
        if name is None:
            raise StackError(f"{archetype_name}: stack entry {entry!r} names "
                             f"neither a role nor a gap")
        if name not in roles:
            raise StackError(f"{archetype_name}: stack references unknown "
                             f"role {name!r}")
        # `gap_before` overrides the default; the first item butts against
        # start_y so the archetype's declared top means what it says.
        if not first:
            cursor -= entry.get("gap_before", default_gap)
        first = False
        spec = roles[name]
        cursor = _place(name, spec, cursor, extents)
        placed[name] = spec

    arch["_stack_bottom"] = cursor
    return placed


def apply_anchors(archetype_name, roles, names):
    """
    Resolve roles positioned relative to a stacked role.

    Runs after the stack so anchors can point at computed elevations. A role
    may anchor to another anchored role, so this iterates until nothing more
    resolves and reports whatever is left rather than looping forever.
    """
    pending = [n for n in names
               if isinstance(roles.get(n), dict) and "anchor" in roles[n]]
    while pending:
        progressed = []
        for name in pending:
            spec = roles[name]
            target = roles.get(spec["anchor"])
            if target is None:
                raise StackError(f"{archetype_name}: role {name!r} anchors to "
                                 f"unknown role {spec['anchor']!r}")
            base = target.get("y")
            if base is None:
                base = target.get("y_top")
            if base is None:
                continue  # anchor not resolved yet
            dy = spec.pop("dy", 0.0)
            if spec.get("kind") == "lead":
                spec["points"] = [[px, base + dy + py]
                                  for px, py in spec.pop("points_dy")]
            elif spec.get("kind") in SIZED_KINDS:
                spec["y_top"] = base + dy
                spec["y_bottom"] = base + dy - spec["height"]
            else:
                spec["y"] = base + dy
            spec.pop("anchor")
            progressed.append(name)
        if not progressed:
            raise StackError(f"{archetype_name}: anchors form a cycle or point "
                             f"at unplaced roles: {sorted(pending)}")
        pending = [n for n in pending if n not in progressed]


def expand(cfg, library_path):
    """
    Turn every stack-defined archetype in a config into plain measured roles.

    Mutates and returns cfg. Configs with no stacks come back untouched, which
    is what keeps the two hand-measured standards byte-identical.
    """
    if not any("stack" in a for a in cfg.get("archetypes", {}).values()):
        return cfg

    extents = block_extents(library_path)
    for name, arch in cfg["archetypes"].items():
        if "stack" not in arch:
            continue
        apply_stack(name, arch, cfg["roles"], extents)
        apply_anchors(name, cfg["roles"], arch.get("roles", []))
    return cfg
