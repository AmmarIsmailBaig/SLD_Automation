"""
generate_sld.py

Generates a Single Line Diagram (SLD) DXF with repeated feeder-cell blocks,
tapped off a main horizontal bus, driven entirely by a CSV of feeder data.

This mirrors the FEEDER_CELL block you build by hand in AutoCAD (tap line ->
3-pole breaker -> CT -> feeder line/load arrow, with TAG / DESCRIPTION /
RATING / VOLTAGE attributes) but defines it in code so it can be placed
programmatically, once per row of feeder data.

Usage:
    python generate_sld.py feeders.csv output.dxf

CSV columns expected: tag, description, voltage, amp_rating, ka_rating
"""

import sys
import csv
import ezdxf
from ezdxf.addons import Importer

CELL_SPACING = 3.0    # drawing units between feeder cell centerlines
BUS_Y = 20.0           # y-coordinate of the main horizontal bus
BREAKER_SIZE = 0.6     # circuit breaker box half-size


def import_feeder_cell_block(doc, library_dxf_path, block_name="FEEDER_CELL"):
    """
    Pull the FEEDER_CELL block definition (geometry + attribute defs) straight
    out of a DXF you exported from AutoCAD, instead of drawing it in code.
    Use this whenever you have a real block to work from.
    """
    if block_name in doc.blocks:
        return doc.blocks.get(block_name)

    source_doc = ezdxf.readfile(library_dxf_path)
    importer = Importer(source_doc, doc)
    importer.import_block(block_name)
    importer.finalize()
    return doc.blocks.get(block_name)


def build_feeder_cell_block(doc):
    """
    Fallback: define a placeholder FEEDER_CELL block in code when no AutoCAD
    export is available yet. Once you have a real block, use
    import_feeder_cell_block() instead -- see main().
    """
    if "FEEDER_CELL" in doc.blocks:
        return doc.blocks.get("FEEDER_CELL")

    blk = doc.blocks.new(name="FEEDER_CELL", base_point=(0, 0))

    # Tap line down from the bus
    blk.add_line((0, 0), (0, -2.0))

    # 3-pole breaker symbol: box + diagonal contact line
    b = BREAKER_SIZE
    blk.add_lwpolyline(
        [(-b, -2.0), (b, -2.0), (b, -2.0 - 2 * b), (-b, -2.0 - 2 * b)],
        close=True,
    )
    blk.add_line((-b, -2.0), (b, -2.0 - 2 * b))

    # Down to the CT
    blk.add_line((0, -2.0 - 2 * b), (0, -4.0))
    blk.add_circle((0, -4.3), radius=0.3)

    # Feeder line to a load arrow
    blk.add_line((0, -4.6), (0, -5.5))
    blk.add_line((0, -5.5), (-0.25, -5.0))
    blk.add_line((0, -5.5), (0.25, -5.0))

    # Attribute definitions -- order here sets the insert prompt order
    attrib_specs = [
        ("TAG", (0.4, -1.5), 0.25),
        ("DESCRIPTION", (0.4, -3.0), 0.2),
        ("VOLTAGE", (0.4, -3.3), 0.2),
        ("AMP_RATING", (0.4, -3.6), 0.2),
        ("KA_RATING", (0.4, -3.9), 0.2),
    ]
    for tag, pos, height in attrib_specs:
        blk.add_attdef(
            tag=tag,
            text=tag,
            dxfattribs={"height": height, "insert": pos, "style": "STANDARD"},
        )

    return blk


def draw_bus(msp, num_cells, start_x=0.0):
    """Draw the main horizontal bus, sized to span every feeder cell."""
    end_x = start_x + CELL_SPACING * max(num_cells - 1, 0)
    msp.add_line(
        (start_x - 1.5, BUS_Y),
        (end_x + 1.5, BUS_Y),
        dxfattribs={"lineweight": 50},
    )


# Maps CSV column -> ATTDEF tag as it actually exists inside the AutoCAD block.
# The block was authored with sample values typed into the Tag field, so the tags
# read like data ("3150A") rather than names ("RATING"). add_auto_attribs() keys
# must match these tags exactly or the attributes silently come out blank.
# If the block is ever re-authored with proper tag names, update the right-hand
# side here (or drop the mapping entirely).
ATTRIB_MAP = {
    "tag": "52-GEN",
    "voltage": "17.5KV",
    "amp_rating": "3150A",
    "ka_rating": "40KA",
}


def place_feeder_cells(msp, feeders, start_x=0.0):
    """Insert one FEEDER_CELL instance per feeder row, with its attributes filled in."""
    for i, feeder in enumerate(feeders):
        x = start_x + i * CELL_SPACING
        blk_ref = msp.add_blockref("FEEDER_CELL", (x, BUS_Y), dxfattribs={"layer": "SLD"})
        blk_ref.add_auto_attribs({
            attdef_tag: feeder.get(column, "")
            for column, attdef_tag in ATTRIB_MAP.items()
        })


def read_feeders(csv_path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    if len(sys.argv) not in (3, 4):
        print("Usage: python generate_sld.py feeders.csv output.dxf [feeder_cell_library.dxf]")
        sys.exit(1)

    csv_path, out_path = sys.argv[1], sys.argv[2]
    library_path = sys.argv[3] if len(sys.argv) == 4 else None
    feeders = read_feeders(csv_path)

    doc = ezdxf.new("R2010")
    doc.layers.add("SLD", color=7)
    msp = doc.modelspace()

    if library_path:
        import_feeder_cell_block(doc, library_path)
    else:
        build_feeder_cell_block(doc)

    draw_bus(msp, len(feeders))
    place_feeder_cells(msp, feeders)

    doc.saveas(out_path)
    print(f"Wrote {out_path} with {len(feeders)} feeder cells.")


if __name__ == "__main__":
    main()