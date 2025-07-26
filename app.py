from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, Response, abort, redirect
import numpy as np
from pathlib import Path
import uuid
import dxf2txt
from dxf2txt import list_shapes, polygon_to_svg, convert_one
import logging
from xml.etree.ElementTree import Element, SubElement, tostring
from shapely.ops import unary_union
from shapely.affinity import rotate as _rotate_geom, scale as _scale_geom
import config
import requests
import re
from svgpathtools import svg2paths2, svg2paths
from shapely.geometry import Polygon

app = Flask(__name__)
UPLOAD_FOLDER = Path(app.root_path) / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)
# Helper to upload files to Cloudflare R2 via API
def r2_upload(key, fileobj):
    url = f"{config.CLOUDFLARE_R2_API_BASE}/{key}"
    headers = {"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"}
    # Set content-type for SVG uploads
    if key.lower().endswith('.svg'):
        headers['Content-Type'] = 'image/svg+xml'
    # Debug: log outgoing R2 upload details
    app.logger.debug("R2 upload URL: %s", url)
    app.logger.debug("R2 upload headers: %r", headers)
    # Log file object name or representation
    try:
        fname = getattr(fileobj, 'name', None)
    except Exception:
        fname = None
    app.logger.debug("R2 upload fileobj: %s", fname or repr(fileobj))
    resp = requests.put(url, data=fileobj, headers=headers)
    # Log response headers from R2
    app.logger.debug("R2 upload response headers: %r", resp.headers)
    if not resp.ok:
        app.logger.error("R2 upload failed for %s: %s %s", key, resp.status_code, resp.text)
        resp.raise_for_status()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.json
    initial_roller_value = float(data['initial_roller_value'])
    end_roller_value = float(data['end_roller_value'])
    number_of_stitches = int(data['number_of_stitches'])
    decay_rate = float(data['decay_rate'])

    roller_values = [max(initial_roller_value * np.exp(-decay_rate * i), end_roller_value) for i in range(number_of_stitches + 1)]
    return jsonify(roller_values)

@app.route('/sizing', methods=['GET', 'POST'])
def sizing():
    if request.method == 'POST':
        try:
            data = request.get_json()
            original_width_px = float(data['original_width_px'])
            original_height_px = float(data['original_height_px'])
            final_width_in = float(data['final_width_in'])
            final_height_in = float(data['final_height_in'])

            # Calculate the machine's scale factors
            scale_horizontal = final_width_in / original_width_px
            scale_vertical = final_height_in / original_height_px

            # Compute the correction factor to get square stitches
            correction_factor = scale_horizontal / scale_vertical

            # Calculate new vertical resolution
            new_height_px = original_height_px * correction_factor

            # Prepare the results
            results = {
                "original_dimensions": f"{original_width_px} x {original_height_px} pixels",
                "scale_factors": f"horizontal = {scale_horizontal:.5f} in/px, vertical = {scale_vertical:.5f} in/px",
                "correction_factor": f"{correction_factor:.3f}",
                "new_dimensions": f"{original_width_px} x {round(new_height_px)} pixels"
            }

            return jsonify(results)
        except Exception as e:
            print(f"Error processing request: {e}")
            return jsonify({"error": "Invalid input"}), 400

    return render_template('sizing_form.html')

@app.route('/convert', methods=['GET', 'POST'])
def convert_route():
    if request.method == 'POST':
        file = request.files.get('dxf_file')
        if not file:
            return "No file uploaded", 400
        try:
            sts10 = float(request.form['sts10'])
            rows10 = float(request.form['rows10'])
        except (KeyError, ValueError):
            return "Invalid gauge values", 400
        app.logger.debug("Starting conversion: file=%s, sts10=%s, rows10=%s", file.filename, sts10, rows10)
        session_id = uuid.uuid4().hex
        session_folder = UPLOAD_FOLDER / session_id
        session_folder.mkdir()
        dxf_path = session_folder / file.filename
        file.save(str(dxf_path))
        app.logger.debug("Saved DXF to %s", dxf_path)
        # Upload original DXF to R2
        with open(dxf_path, "rb") as f_obj:
            r2_upload(f"{session_id}/{file.filename}", f_obj)
        # Determine unit scale (inches→mm or mm→mm)
        units = request.form.get('units', 'mm')
        unit_scale = 25.4 if units in ('inch', 'inches') else 1.0
        app.logger.debug("Using unit scale: %s", unit_scale)
        # Generate SVG previews for each detected shape
        shapes = list_shapes(str(dxf_path), wanted_layers=None, unit_scale=unit_scale)
        if not shapes:
            app.logger.warning("No shapes found for file %s", dxf_path)
            return render_template('convert_result.html', links=[])
        # Parse SVG artboard dimensions if this is an SVG upload
        art_w_mm = art_h_mm = None
        if dxf_path.suffix.lower() == '.svg':
            import xml.etree.ElementTree as ET
            import re
            tree = ET.parse(str(dxf_path))
            root = tree.getroot()
            # parse viewBox for fallback dimensions
            vb = root.get('viewBox')
            view_w = view_h = None
            if vb:
                parts = vb.strip().split()
                if len(parts) == 4:
                    view_w = float(parts[2]); view_h = float(parts[3])
            width_attr = root.get('width'); height_attr = root.get('height')
            def parse_length(s):
                m = re.match(r'([0-9]*\.?[0-9]+)([a-zA-Z%]*)', s)
                if not m: return None, None
                return float(m.group(1)), m.group(2)
            def to_mm(val, unit):
                u = unit.lower()
                # blank unit implies points (Illustrator uses points)
                if u == '':
                    return val * 25.4 / 72
                # explicit point unit
                if u == 'pt':
                    return val * 25.4 / 72
                # inches
                if u in ('in', 'inch', 'inches'):
                    return val * 25.4
                # centimeters
                if u == 'cm':
                    return val * 10
                # millimeters
                if u == 'mm':
                    return val
                # pixels (CSS px at 96 ppi)
                if u in ('px',):
                    return val / 96 * 25.4
                # default fallback treat as pixels
                return val / 96 * 25.4
            # try explicit width/height attrs
            if width_attr:
                w_val, w_unit = parse_length(width_attr)
                if w_val is not None: art_w_mm = to_mm(w_val, w_unit)
            if height_attr:
                h_val, h_unit = parse_length(height_attr)
                if h_val is not None: art_h_mm = to_mm(h_val, h_unit)
            # fallback to viewBox dims interpreted as points
            if (art_w_mm is None or art_h_mm is None) and view_w is not None and view_h is not None:
                art_w_mm = view_w * 25.4 / 72; art_h_mm = view_h * 25.4 / 72
        preview_info = []
        for idx, (name, poly) in enumerate(shapes, start=1):
            # compute bounding box in mm
            if art_w_mm is not None and art_h_mm is not None:
                width_mm = art_w_mm
                height_mm = art_h_mm
            else:
                minx, miny, maxx, maxy = poly.bounds
                width_mm = maxx - minx
                height_mm = maxy - miny
            # convert to display units
            if units in ('inch', 'inches'):
                width = width_mm / 25.4
                height = height_mm / 25.4
            else:
                width = width_mm
                height = height_mm
            fname = name.replace(" ", "_")
            svg_name = f"{idx}_{fname}.svg"
            svg_path = session_folder / svg_name
            polygon_to_svg(poly, str(svg_path))
            # Upload SVG to R2 and set public URL
            with open(svg_path, "rb") as svg_file:
                r2_upload(f"{session_id}/{svg_name}", svg_file)
            link = f"{config.CLOUDFLARE_R2_PUBLIC_BASE}/{session_id}/{svg_name}"
            preview_info.append({'idx': idx, 'name': name, 'svg_link': link,
                                 'width': width, 'height': height})
        return render_template('preview.html', shapes=preview_info,
                               session_id=session_id, sts10=sts10,
                               rows10=rows10, unit_scale=unit_scale,
                               units=units)
    return render_template('convert.html')

@app.route('/uploads/<session_id>/<filename>')
def uploaded_file(session_id, filename):
    directory = UPLOAD_FOLDER / session_id
    return send_from_directory(str(directory), filename)

@app.route('/convert_shape', methods=['POST'])
def convert_shape():
    session_id = request.form.get('session_id')
    try:
        sts10 = float(request.form['sts10'])
        rows10 = float(request.form['rows10'])
        unit_scale = float(request.form['unit_scale'])
        piece_index = int(request.form['piece_index'])
        rotation = float(request.form.get('rotation', 0))
        mirror = request.form.get('mirror', 'none')
        garter_mode = bool(request.form.get('garter'))
        add_transfers = bool(request.form.get('transfers'))
    except (KeyError, ValueError):
        return "Invalid parameters", 400
    session_folder = UPLOAD_FOLDER / session_id
    # Determine input file and geometry scale (DXF: display unit_scale, SVG: mm)
    dxf_files = list(session_folder.glob('*.dxf'))
    if dxf_files:
        input_path = str(dxf_files[0])
        geom_scale = unit_scale
    else:
        svg_files = list(session_folder.glob('*.svg'))
        orig_svgs = [p for p in svg_files if not re.match(r'^\d+_', p.name)]
        if orig_svgs:
            input_path = str(orig_svgs[0])
            geom_scale = 1.0
        else:
            return "Input file not found", 404
    # Convert shape using the common converter
    created = convert_one(input_path, str(session_folder), sts10, rows10,
                          wanted_layers=None, unit_scale=geom_scale,
                          piece_index=piece_index,
                          rotation=rotation, mirror=mirror,
                          garter_mode=garter_mode, add_transfers=add_transfers)
    if not created:
        return "Shape not found", 404
    # Upload converted files to R2 and build public URLs
    links = []
    for p in created:
        filename = Path(p).name
        with open(p, "rb") as f_obj:
            r2_upload(f"{session_id}/{filename}", f_obj)
        link = f"{config.CLOUDFLARE_R2_PUBLIC_BASE}/{session_id}/{filename}"
        links.append(link)
    return render_template('convert_result.html', links=links)

@app.route('/preview_shape')
def preview_shape():
    import re
    # Dynamic SVG preview for a single shape with rotation and mirror union
    session_id = request.args.get('session_id')
    try:
        piece_index = int(request.args.get('piece_index', 0))
        rotation = float(request.args.get('rotation', 0))
        mirror = request.args.get('mirror', 'none')
        unit_scale = float(request.args.get('unit_scale', 1.0))
    except (TypeError, ValueError):
        abort(400, 'Invalid parameters')
    session_folder = UPLOAD_FOLDER / session_id
    # If no DXF but an original SVG was uploaded, serve it directly
    svg_files = list(session_folder.glob('*.svg'))
    # Filter out generated polygon previews (which start with digits+_)
    orig_svgs = [p for p in svg_files if not re.match(r'^\d+_', p.name)]
    if orig_svgs and not list(session_folder.glob('*.dxf')):
        # Redirect to the SVG hosted on R2 via custom domain
        orig_url = f"{config.CLOUDFLARE_R2_PUBLIC_BASE}/{session_id}/{orig_svgs[0].name}"
        new_url = orig_url.replace(config.CLOUDFLARE_R2_PUBLIC_BASE, 'https://kniterate.lunote.co')
        return redirect(new_url)
    dxf_files = list(session_folder.glob('*.dxf'))
    if not dxf_files:
        abort(404, 'DXF file not found')
    dxf_path = str(dxf_files[0])
    shapes = list_shapes(dxf_path, wanted_layers=None, unit_scale=unit_scale)
    if piece_index < 1 or piece_index > len(shapes):
        abort(404, 'Shape not found')
    name, base_poly = shapes[piece_index - 1]
    # Apply rotation
    minx, miny, maxx, maxy = base_poly.bounds
    center = ((minx + maxx) / 2, (miny + maxy) / 2)
    if rotation:
        rotated = _rotate_geom(base_poly, rotation, origin=center)
    else:
        rotated = base_poly
    # Prepare polygons list
    polys = [rotated]
    # If mirroring, generate mirrored copy and include
    if mirror in ('left', 'right'):
        minx2, miny2, maxx2, maxy2 = rotated.bounds
        cy = (miny2 + maxy2) / 2
        origin = (minx2, cy) if mirror == 'left' else (maxx2, cy)
        mirrored = _scale_geom(rotated, xfact=-1, yfact=1, origin=origin)
        # Union rotated and mirrored shapes and close small gaps
        poly_raw = unary_union([rotated, mirrored])
        poly_final = poly_raw.buffer(dxf2txt.TOL).buffer(-dxf2txt.TOL)
    else:
        poly_final = rotated
    # Build SVG document and compute dimensions
    view_minx, view_miny, view_maxx, view_maxy = poly_final.bounds
    width_mm = view_maxx - view_minx
    height_mm = view_maxy - view_miny
    # Determine display units based on unit_scale
    if unit_scale != 1.0:
        disp_units = 'inch'
        disp_width = width_mm / unit_scale
        disp_height = height_mm / unit_scale
    else:
        disp_units = 'mm'
        disp_width = width_mm
        disp_height = height_mm
    svg = Element('svg', xmlns='http://www.w3.org/2000/svg',
                  viewBox=f"{view_minx} {view_miny} {width_mm} {height_mm}",
                  width=f"{width_mm}mm", height=f"{height_mm}mm")
    # Create path(s) for polygon or multi-polygon boundary
    if hasattr(poly_final, 'geoms'):
        polys_out = poly_final.geoms
    else:
        polys_out = [poly_final]
    for p in polys_out:
        # Render exterior boundary as filled shape
        coords = ' '.join(f"{x:.3f},{y:.3f}" for x, y in p.exterior.coords)
        SubElement(svg, 'path', d=f"M {coords} Z", stroke='none', fill='black')
        # Render interior holes to remove fill where appropriate
        for interior in p.interiors:
            hole_coords = ' '.join(f"{x:.3f},{y:.3f}" for x, y in interior.coords)
            SubElement(svg, 'path', d=f"M {hole_coords} Z", stroke='none', fill='white')
    # Serialize SVG
    svg_bytes = tostring(svg)
    # Return JSON with svg and dims if requested, else raw SVG
    if request.args.get('json'):
        svg_text = svg_bytes.decode('utf-8')
        return jsonify({
            'svg': svg_text,
            'width': round(disp_width, 4),
            'height': round(disp_height, 4),
            'units': disp_units
        })
    return Response(svg_bytes, mimetype='image/svg+xml')

if __name__ == '__main__':
    app.run(debug=True)
