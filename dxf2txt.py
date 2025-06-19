"""dxf2txt.py – convert DXF garment outlines ➜ DesignaKnit .txt shape files.
Call `convert(dxf_path, out_dir, sts10, rows10, wanted_layers=None)` from your
Flask app.  Returns list of generated .txt paths.

This version heals tiny gaps, samples SPLINEs, expands INSERT blocks, merges
LINE/ARC chains (Gerber/Lectra exports), and lets you filter by layer names.
"""
from pathlib import Path
import math, logging
from typing import List, Iterable
import ezdxf                     # pip install ezdxf
from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import linemerge

logger = logging.getLogger(__name__)
TOL = 0.05  # mm tolerance when welding small gaps

# ---------------------------------------------------------------------------
# entity → polygons helper ---------------------------------------------------

def _entity_to_polys(ent, layer: str, polys: List, wanted_layers: Iterable[str]):
    if wanted_layers and layer.upper().strip() not in wanted_layers:
        return
    # HATCH boundary paths --------------------------------------------------
    if ent.dxftype() == "HATCH":
        for path in ent.paths:
            coords = []
            for edge in path.edges:
                # try polyline edge vertices first
                pts = []
                try:
                    pts = [(pt.x, pt.y) for pt in edge.vertices()]
                except Exception:
                    try:
                        pts = [(pt.x, pt.y) for pt in edge.flattening(TOL)]
                    except Exception:
                        continue
                coords.extend(pts)
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])
            poly = Polygon(coords).buffer(TOL)
            if poly.is_valid:
                polys.append((layer, poly))
        return
    # Polyline (lightweight) --------------------------------------------------
    if ent.dxftype() == "LWPOLYLINE":
        pts = [tuple(pt[:2]) for pt in ent.get_points()]
        # Require at least 3 points to form a polygon
        if len(pts) < 3:
            return
        # Force closure
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        poly = Polygon(pts).buffer(TOL)
        if poly.is_valid:
            polys.append((layer, poly))
        return
    # Classic POLYLINE -------------------------------------------------------
    if ent.dxftype() == "POLYLINE":
        # Flatten polyline segments (handles bulges), fallback to get_points or vertices
        coords = []
        try:
            coords = [(pt[0], pt[1]) for pt in ent.flattening(TOL)]
        except Exception:
            try:
                coords = [tuple(pt[:2]) for pt in ent.get_points()]
            except Exception:
                try:
                    coords = [(v.dxf.location.x, v.dxf.location.y) for v in ent.vertices]
                except Exception:
                    logger.error("Failed to get coordinates for POLYLINE entity on layer %s", layer)
                    return
        # Require at least 3 points to form a polygon
        if len(coords) < 3:
            return
        # Force closure
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        poly = Polygon(coords).buffer(TOL)
        if poly.is_valid:
            polys.append((layer, poly))
        return
    # Spline -----------------------------------------------------------------
    if ent.dxftype() == "SPLINE":
        pts = [ent.point(t)[:2] for t in (i/100 for i in range(101))]
        poly = Polygon(pts).buffer(TOL)
        if poly.is_valid:
            polys.append((layer, poly))
        return
    # Line/Arc handled in separate pass

# ---------------------------------------------------------------------------
# main collector -------------------------------------------------------------

def collect_polygons(doc, wanted_layers=None):
    """Return list[(layer, Polygon)] of closed outlines in *doc* modelspace.
    Handles POLYLINE/LWPOLYLINE, SPLINE, INSERT-contained entities, and
    LINE/ARC chains (merged).
    """
    msp = doc.modelspace()
    polys: List = []
    # segments for line/arc merging
    segs_by_layer = {}

    # Pass 1 – explicit entities + expanded INSERTs
    for e in msp:
        if e.dxftype() == "INSERT":
            # Determine piece name from TEXT entities in this block reference
            ves = list(e.virtual_entities())
            piece_name = e.dxf.name
            for ent2 in ves:
                if ent2.dxftype() == "TEXT":
                    txt = getattr(ent2.dxf, 'text', None)
                    if txt and 'Piece Name:' in txt:
                        piece_name = txt.split(':', 1)[1].strip()
                        break
            # flatten block reference into primitives under piece_name
            for ent in ves:
                _entity_to_polys(ent, piece_name, polys, wanted_layers)
                # record line/arc segments for merging by piece_name
                if ent.dxftype() == 'LINE':
                    start, end = ent.dxf.start, ent.dxf.end
                    segs_by_layer.setdefault(piece_name, []).append(
                        LineString([(start.x, start.y), (end.x, end.y)])
                    )
                elif ent.dxftype() == 'ARC':
                    pts = [(pt.x, pt.y) for pt in ent.flattening(0.5)]
                    segs_by_layer.setdefault(piece_name, []).append(
                        LineString(pts)
                    )
            continue
        _entity_to_polys(e, e.dxf.layer, polys, wanted_layers)

    # Pass 2 – merge LINE/ARC chains by layer
    for layer in {e.dxf.layer for e in msp if e.dxftype() in ("LINE", "ARC")}:
        if wanted_layers and layer.upper().strip() not in wanted_layers:
            continue
        segs = []
        for e in msp.query(f'LINE ARC[layer=="{layer}"]'):
            if e.dxftype() == 'LINE':
                start = e.dxf.start
                end = e.dxf.end
                segs.append(LineString([(start.x, start.y), (end.x, end.y)]))
            else:  # ARC
                # Flatten arc into line segments
                pts = [(pt.x, pt.y) for pt in e.flattening(0.5)]
                segs.append(LineString(pts))
        if not segs:
            continue
        merged = linemerge(MultiLineString(segs))
        ls_geoms = merged.geoms if hasattr(merged, 'geoms') else [merged]
        for ls in ls_geoms:
            if ls.is_ring:
                poly = Polygon(ls.coords).buffer(TOL)
                if poly.is_valid:
                    polys.append((layer, poly))

    if not polys:
        # Fallback: scan block definitions for closed polylines, hatches, splines
        for block_layout in doc.blocks:
            for ent in block_layout:
                _entity_to_polys(ent, ent.dxf.layer, polys, wanted_layers)
        # Fallback: merge line/arc loops found in block definitions
        for block_layout in doc.blocks:
            for e in block_layout:
                if e.dxftype() == "LINE":
                    start, end = e.dxf.start, e.dxf.end
                    segs_by_layer.setdefault(e.dxf.layer, []).append(
                        LineString([(start.x, start.y), (end.x, end.y)])
                    )
                elif e.dxftype() == "ARC":
                    pts = [(pt.x, pt.y) for pt in e.flattening(0.5)]
                    segs_by_layer.setdefault(e.dxf.layer, []).append(
                        LineString(pts)
                    )
        for layer, segs in segs_by_layer.items():
            merged = linemerge(MultiLineString(segs))
            geoms = merged.geoms if hasattr(merged, 'geoms') else [merged]
            for ls in geoms:
                if ls.is_ring:
                    poly = Polygon(ls.coords).buffer(TOL)
                    if poly.is_valid:
                        polys.append((layer, poly))
        logger.info("Fallback detected %d shape(s) in block definitions", len(polys))
    return polys

# ---------------------------------------------------------------------------
# raster + writer ------------------------------------------------------------

def row_stitch_counts(poly: Polygon, sts10: float, rows10: float):
    """Yield, for each row, a list of (indent_stitches, stitch_count) tuples."""
    mm_row = 100 / rows10
    mm_st = 100 / sts10
    minx, miny, maxx, maxy = poly.bounds
    rows = int(math.ceil((maxy - miny) / mm_row))
    for r in range(rows):
        y = miny + r * mm_row + 1e-6
        scan = LineString([(minx - 10, y), (maxx + 10, y)])
        xs = poly.intersection(scan)
        runs = []
        if not xs.is_empty:
            # Handle single or multi line segments
            segs = xs.geoms if hasattr(xs, 'geoms') else [xs]
            for seg in segs:
                # segment start and length in mm
                coords = list(seg.coords)
                xs_vals = [pt[0] for pt in coords]
                seg_min = min(xs_vals)
                width_mm = seg.length
                # convert to stitch units
                indent = int(round((seg_min - minx) / mm_st))
                count = int(round(width_mm / mm_st))
                if count > 0:
                    runs.append((indent, count))
        yield runs

def _write_shape(path: Path, piece_name: str, filename_root: str,
                 sts_row: List[int], sts10: float, rows10: float, yarn=4):
    rows = len(sts_row)
    # sts_row now contains per-row runs of (indent,count)
    # Determine max stitches width across all runs
    max_sts = 0
    for runs in sts_row:
        for indent, count in runs:
            max_sts = max(max_sts, indent + count)
    # Helper to render a row of yarn runs
    def render_row(runs):
        line = [' '] * max_sts
        for indent, count in runs:
            for i in range(indent, indent + count):
                if 0 <= i < max_sts:
                    line[i] = str(yarn)
        return ''.join(line)
    # Helper to render a row of stitch symbols
    def render_symbols(runs):
        line = [' '] * max_sts
        for indent, count in runs:
            for i in range(indent, indent + count):
                if 0 <= i < max_sts:
                    line[i] = '-'
        return ''.join(line)

    with open(path, 'w', encoding='utf-8') as f:
        w = f.write
        w("FILE FORMAT : DAK\nFILE FORMAT VERSION : 0.43\nGARMENT PIECE\n")
        w(f"Shape filename : {filename_root}\nPiece : {piece_name}\n")
        w(f"Stitches : {max_sts}\nRows : {rows}\n")
        w("RIB DIMENSIONS\nStitches : 0\nRows : 0\n")
        w("RIB TENSIONS\nStitches per 10 cm =  0\nRows per 10 cm =  0\n")
        w("MAIN TENSIONS\n")
        w(f"Stitches per 10 cm =  {int(sts10)}\nRows per 10 cm =  {int(rows10)}\n")
        w("YARNS\n")
        for runs in sts_row:
            w(render_row(runs) + "\n")
        w("\nYARN PALETTE\nYarn L : 112,180,249 light blue\n")
        w("\nSTITCH SYMBOLS\n")
        for runs in sts_row:
            w(render_symbols(runs) + "\n")
        w("\nSTITCH PATTERN NOTES\nSHAPE FILE NOTES\nEND\n")

# ---------------------------------------------------------------------------
# SVG preview and single-shape conversion helpers

def list_shapes(dxf_path: str, wanted_layers=None, unit_scale: float = 1.0):
    """List named shape polygons from a DXF or SVG file."""
    ext = Path(dxf_path).suffix.lower()
    if ext == '.svg':
        import xml.etree.ElementTree as ET
        from shapely.geometry import Polygon, MultiPolygon
        from svgpathtools import parse_path, Path as SVGPath
        tree = ET.parse(dxf_path)
        root = tree.getroot()
        shapes = []
        # --- Parse artboard units and compute scale to mm ---
        width_attr = root.get('width')
        height_attr = root.get('height')
        viewBox = root.get('viewBox')
        import re
        def parse_length(s):
            m = re.match(r'([0-9.]+)([a-zA-Z%]*)', s)
            if not m: return None, None
            return float(m.group(1)), m.group(2)
        def to_mm(val, unit):
            u = unit.lower()
            if u == '': return val * 25.4 / 72  # Illustrator default: points
            if u == 'pt': return val * 25.4 / 72
            if u in ('in', 'inch', 'inches'): return val * 25.4
            if u == 'cm': return val * 10
            if u == 'mm': return val
            if u == 'px': return val / 96 * 25.4
            return val / 96 * 25.4
        scale_x = scale_y = 1.0
        # Default: use width/height attributes for real-world size
        w_val = h_val = view_w = view_h = None
        w_unit = h_unit = None
        if width_attr and height_attr:
            w_val, w_unit = parse_length(width_attr)
            h_val, h_unit = parse_length(height_attr)
        if viewBox:
            parts = viewBox.strip().split()
            if len(parts) == 4:
                view_w = float(parts[2])
                view_h = float(parts[3])
        # If both width/height and viewBox are present, use them for scaling
        if w_val and h_val and view_w and view_h:
            width_mm = to_mm(w_val, w_unit)
            height_mm = to_mm(h_val, h_unit)
            scale_x = width_mm / view_w
            scale_y = height_mm / view_h
        # If only width/height, fallback to old logic
        elif w_val and h_val:
            width_mm = to_mm(w_val, w_unit)
            height_mm = to_mm(h_val, h_unit)
            scale_x = width_mm / w_val
            scale_y = height_mm / h_val
        # If only viewBox, treat as points (Illustrator default)
        elif view_w and view_h:
            scale_x = 25.4 / 72
            scale_y = 25.4 / 72
        # --- END scale calculation ---
        for idx, elem in enumerate(root.findall('.//{http://www.w3.org/2000/svg}path'), start=1):
            d = elem.get('d')
            if not d:
                continue
            svg_path = parse_path(d)
            polygons = []
            for subpath in svg_path.continuous_subpaths():
                coords = [seg.start for seg in subpath]
                if coords and coords[0] != coords[-1]:
                    coords.append(subpath[-1].end)
                # --- scale to mm ---
                coords = [(pt.real * scale_x, pt.imag * scale_y) for pt in coords]
                if len(coords) >= 3:
                    polygons.append(Polygon(coords))
            if not polygons:
                continue
            from shapely.ops import unary_union
            poly = unary_union(polygons)
            name = Path(dxf_path).stem
            shapes.append((name, poly))
        return shapes
    doc = ezdxf.readfile(dxf_path)
    pieces = collect_polygons(doc, wanted_layers)
    from shapely.ops import unary_union
    grouped = {}
    for name, poly in pieces:
        grouped.setdefault(name, []).append(poly)
    result = []
    for name, polys in grouped.items():
        merged = unary_union(polys) if len(polys) > 1 else polys[0]
        result.append((name, merged))
    # Scale DXF shapes to millimeters based on unit_scale
    if unit_scale != 1.0:
        from shapely.affinity import scale as _scale_geom
        result = [(name, _scale_geom(poly, xfact=unit_scale, yfact=unit_scale, origin=(0, 0))) for name, poly in result]
    return result

def polygon_to_svg(poly, path: str, stroke='none', fill='black', stroke_width=0):
    """Write a simple SVG file rendering the filled Polygon."""
    from xml.etree.ElementTree import Element, SubElement, tostring
    minx, miny, maxx, maxy = poly.bounds
    width = maxx - minx
    height = maxy - miny
    root = Element('svg', xmlns='http://www.w3.org/2000/svg',
                   viewBox=f"{minx} {miny} {width} {height}",
                   width=f"{width}mm", height=f"{height}mm")
    coords = " ".join(f"{x:.3f},{y:.3f}" for x, y in poly.exterior.coords)
    path_d = f"M {coords} Z"
    # Draw filled shape
    el = SubElement(root, 'path', d=path_d, stroke=stroke, fill=fill)
    if stroke_width:
        el.set('stroke-width', str(stroke_width))
    with open(path, 'wb') as f:
        f.write(tostring(root))

def convert_one(dxf_path: str, out_dir: str, sts10: float, rows10: float,
                wanted_layers=None, unit_scale: float=1.0,
                piece_index: int=1, rotation: float=0.0, mirror: str="none"):
    """Convert a single named shape (by index) from DXF to DAK txt."""
    shapes = list_shapes(dxf_path, wanted_layers, unit_scale)
    if not shapes or piece_index < 1 or piece_index > len(shapes):
        return []
    name, base_poly = shapes[piece_index - 1]
    # Apply rotation and mirror, then union shapes for final outline
    from shapely.affinity import rotate as _rotate_geom, scale as _scale_geom
    # Rotation around center of base shape
    minx, miny, maxx, maxy = base_poly.bounds
    center = ((minx + maxx) / 2, (miny + maxy) / 2)
    if rotation:
        rotated = _rotate_geom(base_poly, rotation, origin=center)
    else:
        rotated = base_poly
    # Build list of polygons to merge: rotated shape always present
    polys_to_merge = [rotated]
    # If mirroring, create mirrored copy of rotated shape
    if mirror in ('left', 'right'):
        # Mirror about left or right edge of rotated shape's bounding box
        minx2, miny2, maxx2, maxy2 = rotated.bounds
        cy = (miny2 + maxy2) / 2
        origin = (minx2, cy) if mirror == 'left' else (maxx2, cy)
        mirrored = _scale_geom(rotated, xfact=-1, yfact=1, origin=origin)
        polys_to_merge.append(mirrored)
    # Union all pieces into final polygon or multipolygon
    from shapely.ops import unary_union
    poly = unary_union(polys_to_merge)
    # close small gaps between mirrored/rotated parts
    poly = poly.buffer(TOL).buffer(-TOL)
    from pathlib import Path
    fname = name.replace(" ", "_")
    txt_path = Path(out_dir) / f"{piece_index}_{fname}.txt"
    counts = list(row_stitch_counts(poly, sts10, rows10))
    _write_shape(txt_path, name, fname, counts, sts10, rows10)
    return [str(txt_path)]

# ---------------------------------------------------------------------------
# public API -----------------------------------------------------------------

def convert(dxf_path: str, out_dir: str, sts10: float, rows10: float, wanted_layers: Iterable[str]=None, unit_scale: float=1.0):
    """Convert *dxf_path* to one .txt per closed outline in *out_dir*.
    Returns list[str] of generated files.
    """
    logger.info("Converting DXF %s", dxf_path)
    doc = ezdxf.readfile(dxf_path)
    pieces = collect_polygons(doc, wanted_layers)
    # Scale polygon geometries from drawing units to mm
    if unit_scale != 1.0 and pieces:
        from shapely.affinity import scale as _scale_geom
        pieces = [
            (layer, _scale_geom(poly, xfact=unit_scale, yfact=unit_scale, origin=(0, 0)))
            for layer, poly in pieces
        ]
    logger.info("Detected %d raw shapes", len(pieces))
    # Group multiple polygons by piece name into single shapes
    from shapely.ops import unary_union
    grouped = {}
    for name, poly in pieces:
        grouped.setdefault(name, []).append(poly)
    pieces = []
    for name, polys_list in grouped.items():
        merged_poly = unary_union(polys_list) if len(polys_list) > 1 else polys_list[0]
        pieces.append((name, merged_poly))
    logger.info("Grouped into %d shapes", len(pieces))
    if not pieces:
        logger.warning("No shapes found in %s", dxf_path)
        return []

    out_p = Path(out_dir); out_p.mkdir(parents=True, exist_ok=True)
    created = []
    for idx, (name, poly) in enumerate(pieces, 1):
        fname = name.replace(" ", "_")
        # Name each shape file uniquely by index and layer name
        txt_path = out_p / f"{idx}_{fname}.txt"
        counts = list(row_stitch_counts(poly, sts10, rows10))
        _write_shape(txt_path, name, fname, counts, sts10, rows10)
        created.append(str(txt_path))
        logger.debug("Wrote %s", txt_path)
    return created