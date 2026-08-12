"""
extract_symbols.py

One-time tool: pulls the real AutoCAD Electrical symbol blocks out of an IOM
project drawing into a standalone symbol_library.dxf, then adds placeholder
("stub") blocks for the symbols that don't exist in the reference drawings yet.

Re-run this whenever the reference drawing gains new symbols, or when the real
versions of the stubbed symbols arrive.

Accepts several source drawings so symbols can be harvested from wherever they
were drawn -- the original project sheets, or a working DXF the drafter added
new symbols to. Later sources win on name collisions.

Usage:
    python extract_symbols.py symbol_library.dxf 8508+A01-000-053.dxf [more.dxf ...]
"""

import sys
import ezdxf
from ezdxf.addons import Importer

# Real blocks to lift out of the source drawings.
HARVEST = [
    "VXF1CT",        # current transformer, vertical
    "HXF1CT",        # current transformer, horizontal
    "VXF1T1_1-",     # transformer / PT, vertical
    "HXF1T1_1-",     # transformer / PT, horizontal
    "VFU1_1-",       # fuse, vertical
    "VDS11F",        # disconnect switch
    "HGND2",         # ground
    "VCN1PJ",        # drawout primary disconnect (plug/jack)
    "WDDOT",         # wire junction dot
    "ARRESTER",      # surge arrester -- whole branch: tap dot, wire, ground, label
    "DEVICE_BUBBLE", # ANSI device bubble, function number in the DB attribute
    "VFU1",          # fuse, vertical (plain)
    "HFU1",          # fuse, horizontal
    # The 8372 sheets are drawn from a different ACADE symbol set than the
    # 8508/8513 ones -- same devices, different blocks -- so both live in the
    # library and a config picks the set its standard was drawn with.
    "VC01PJ_1-",     # drawout primary disconnect, vertical (8372's VCN1PJ)
    "HC01PJ_1-",     # drawout primary disconnect, horizontal
    "1LCT1A",        # current transformer (8372's VXF1CT)
    "CSHEXA",        # relay / instrument block
    "WD1005",        # wire junction dot (8372's WDDOT)
]

STUB_LAYER = "SLD_STUB"


def add_stubs(doc):
    """
    Placeholder geometry for symbols not present in the reference drawings.
    Drawn on their own layer in a contrasting colour so they are obvious on a
    plot -- these are meant to be replaced, not shipped.

    Each stub keeps its insertion point on the electrical centreline at the top
    terminal, so swapping in the real block is a library change only.
    """
    if STUB_LAYER not in doc.layers:
        doc.layers.add(STUB_LAYER, color=1)  # red

    def new(name):
        if name in doc.blocks:
            doc.blocks.delete_block(name, safe=False)
        return doc.blocks.new(name=name, base_point=(0, 0))

    # --- Ground switch: blade to earth, hinged off the main line ---------
    b = new("SLD_STUB_GND_SWITCH")
    b.add_line((0, 0), (0, -0.20), dxfattribs={"layer": STUB_LAYER})
    b.add_line((0, -0.20), (0.38, -0.52), dxfattribs={"layer": STUB_LAYER})
    b.add_line((0, -0.60), (0, -0.75), dxfattribs={"layer": STUB_LAYER})
    for i, half in enumerate((0.22, 0.14, 0.07)):
        y = -0.75 - i * 0.07
        b.add_line((-half, y), (half, y), dxfattribs={"layer": STUB_LAYER})
    b.add_attdef(tag="TAG1", text="", dxfattribs={
        "height": 0.10, "insert": (0.28, -0.30), "layer": STUB_LAYER})

    # --- 43LR local/remote selector switch -------------------------------
    b = new("SLD_STUB_43LR")
    b.add_circle((0, -0.22), radius=0.22, dxfattribs={"layer": STUB_LAYER})
    b.add_line((0, 0), (0, -0.44), dxfattribs={"layer": STUB_LAYER})
    b.add_attdef(tag="TAG1", text="43LR", dxfattribs={
        "height": 0.11, "insert": (-0.16, -0.28), "layer": STUB_LAYER})

    # Surge arrester and the ANSI device bubble were stubbed here originally;
    # both are now real blocks harvested from the drafter's working file, so
    # only the two genuinely-missing symbols remain stubbed.
    return ["SLD_STUB_GND_SWITCH", "SLD_STUB_43LR"]


def main():
    if len(sys.argv) < 3:
        print("Usage: python extract_symbols.py <symbol_library.dxf> <source.dxf> [more.dxf ...]")
        sys.exit(1)

    out_path, sources = sys.argv[1], sys.argv[2:]
    # Build on the existing library rather than replacing it. Not every symbol
    # in it came from a source drawing -- ARRESTER and DEVICE_BUBBLE were drawn
    # by hand -- so starting from an empty document silently deleted them the
    # moment this ran against a drawing that happened not to contain them.
    # Harvesting is meant to add symbols, never to remove one.
    try:
        lib = ezdxf.readfile(out_path)
        kept = sorted(b.name for b in lib.blocks if not b.name.startswith(("*", "_")))
        print(f"  extending {out_path} ({len(kept)} existing blocks)")
    except (IOError, ezdxf.DXFError):
        lib = ezdxf.new("R2013", setup=True)

    found = {}
    for src_path in sources:
        src = ezdxf.readfile(src_path)
        importer = Importer(src, lib)
        for name in HARVEST:
            if name in src.blocks:
                # Later sources win, so a symbol redrawn in a working file
                # supersedes the version in the original project sheet.
                if name in lib.blocks:
                    lib.blocks.delete_block(name, safe=False)
                importer.import_block(name)
                found[name] = src_path
        importer.finalize()

    stubs = add_stubs(lib)
    lib.saveas(out_path)

    missing = [n for n in HARVEST if n not in found]
    print(f"Wrote {out_path}")
    print(f"  harvested {len(found)} block(s):")
    for name in HARVEST:
        if name in found:
            print(f"    {name:16} <- {found[name]}")
    if missing:
        print(f"  NOT FOUND in any source: {', '.join(missing)}")
    print(f"  stubs added: {', '.join(stubs)}")


if __name__ == "__main__":
    main()
