"""
preview.py

Renders a DXF to PNG for quick visual checking without opening AutoCAD.

Note on colours: these drawings use ACI colour 7, which AutoCAD resolves
against the model-space background (black on white paper, white on a dark
screen). A naive render on a white canvas turns every colour-7 entity white
and produces a blank image -- the reference IOM drawings render blank too.
This forces a black-on-white policy so geometry is always visible.

Usage:
    python preview.py sld_full.dxf preview.png
    python preview.py sld_full.dxf unit1.png --xlim 0 4 --ylim 8 21.5
"""

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, ColorPolicy


def render(dxf_path, png_path, xlim=None, ylim=None, width_in=18, dpi=120):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    ctx = RenderContext(doc)
    cfg = Configuration(color_policy=ColorPolicy.BLACK, lineweight_scaling=0.7)

    fig = plt.figure(figsize=(width_in, width_in * 0.6))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    Frontend(ctx, MatplotlibBackend(ax), config=cfg).draw_layout(msp, finalize=False)

    # finalize=False leaves the axes at matplotlib's default 0-1 limits, so
    # anything not explicitly framed has to be framed from the drawing's own
    # extents -- otherwise the whole sheet falls outside the view and the PNG
    # comes out blank.
    if not (xlim and ylim):
        extents = bbox.extents(msp, fast=True)
        if extents.has_data:
            margin = 0.02 * max(extents.size.x, extents.size.y)
            xlim = xlim or (extents.extmin.x - margin, extents.extmax.x + margin)
            ylim = ylim or (extents.extmin.y - margin, extents.extmax.y + margin)

    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    ax.set_aspect("equal", adjustable="box")

    fig.savefig(png_path, dpi=dpi, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return png_path


def main():
    ap = argparse.ArgumentParser(description="Render a DXF to PNG.")
    ap.add_argument("dxf")
    ap.add_argument("png")
    ap.add_argument("--xlim", nargs=2, type=float)
    ap.add_argument("--ylim", nargs=2, type=float)
    ap.add_argument("--width", type=float, default=18)
    ap.add_argument("--dpi", type=int, default=120)
    args = ap.parse_args()

    out = render(args.dxf, args.png, args.xlim, args.ylim, args.width, args.dpi)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
