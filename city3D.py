#- geo3D_qgis: 2026
#- arkriger

import os
import json
import processing
from urllib.parse import quote

from qgis.core import (
    QgsField, QgsProject, QgsDistanceArea, QgsCoordinateTransform, QgsFeatureRequest,
    QgsCoordinateReferenceSystem, QgsGeometry, QgsVariantUtils, QgsVectorLayer, QgsVectorFileWriter,
    QgsLineSymbol, QgsSingleSymbolRenderer, QgsMapLayer, QgsCoordinateTransformContext
)
from qgis.PyQt.QtCore import QEventLoop, QUrl
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest
from qgis.PyQt.QtGui import QColor

from PyQt5.QtCore import QVariant

from pyproj import CRS


def _remove_layer_by_name(name):
    """Finds and removes any existing layer with the same name to prevent duplicates."""
    existing_layers = QgsProject.instance().mapLayersByName(name)
    for layer in existing_layers:
        QgsProject.instance().removeMapLayer(layer.id())
        
def _fetch_overpass(query):
    """Synchronous network fetcher using QGIS-native QNetworkAccessManager."""
    manager = QNetworkAccessManager()
    loop = QEventLoop()
    manager.finished.connect(loop.quit)
    url = f'https://overpass-api.de/api/interpreter?data={quote(query)}'
    reply = manager.get(QNetworkRequest(QUrl(url)))
    loop.exec_()
    
    if reply.error() != 0:
        raise RuntimeError(f"Overpass request failed: {reply.errorString()}")
    return json.loads(reply.readAll().data().decode())

def _parse_to_geojson(raw_data, geom_type="Polygon"):
    """Generic Overpass JSON to GeoJSON converter."""
    geojson = {"type": "FeatureCollection", "features": []}
    
    for el in raw_data.get("elements", []):
        tags = el.get("tags", {})
        
        # --- Handle Ways (Simple Lines or Polygons) ---
        if el.get("type") == "way" and "geometry" in el:
            coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
            if len(coords) < 2: continue
            
            if geom_type == "MultiLineString":
                geometry = {"type": "MultiLineString", "coordinates": [coords]}
            elif geom_type == "Polygon":
                if len(coords) < 4: continue
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else: # Default LineString
                geometry = {"type": "LineString", "coordinates": coords}
                
            geojson["features"].append({"type": "Feature", "properties": tags, "geometry": geometry})

        # --- Handle Relations (The Bus Routes) ---
        elif el.get("type") == "relation" and "members" in el:
            if geom_type == "MultiLineString":
                # For bus routes, we want to combine all 'way' members into one MultiLineString
                line_segments = []
                for m in el["members"]:
                    if m.get("type") == "way" and "geometry" in m:
                        line_segments.append([(pt["lon"], pt["lat"]) for pt in m["geometry"]])
                
                if line_segments:
                    geojson["features"].append({
                        "type": "Feature",
                        "properties": tags,
                        "geometry": {"type": "MultiLineString", "coordinates": line_segments}
                    })
            
            elif geom_type == "Polygon":
                # Your existing Multipolygon logic for buildings/parks
                outers = [[(pt["lon"], pt["lat"]) for pt in m["geometry"]] 
                          for m in el["members"] if m.get("role") == "outer" and "geometry" in m]
                inners = [[(pt["lon"], pt["lat"]) for pt in m["geometry"]] 
                          for m in el["members"] if m.get("role") == "inner" and "geometry" in m]
                if outers:
                    geojson["features"].append({
                        "type": "Feature", "properties": tags,
                        "geometry": {"type": "Polygon", "coordinates": outers + inners}
                    })
                    
    return geojson

def overpass2qgis(large, focus, zoom=True):
    """Harvest buildings and add to project."""
    name = f"Buildings_{focus}"
    query = (f'[out:json][timeout:180];area[name="{large}"]->.L;area[name="{focus}"](area.L)->.a;(way["building"](area.a);relation["building"]["type"="multipolygon"](area.a););out geom;')
    data = _fetch_overpass(query)
    vlayer = QgsVectorLayer(json.dumps(_parse_to_geojson(data, "Polygon")), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    QgsProject.instance().addMapLayer(final)
    
    if zoom:
        from qgis.utils import iface
        iface.setActiveLayer(final)
        iface.zoomToActiveLayer()
    return final

def get_rgb_color(bld):
    """Returns RGB list as a string to match the original notebook format."""
    if bld in ['house', 'semidetached_house', 'terrace']:
        rgb = [255, 255, 204]
    elif bld == 'apartments':
        rgb = [252, 194, 3]
    elif bld in ['residential', 'dormitory', 'cabin']:
        rgb = [119, 3, 252]
    elif bld in ['garage', 'parking']:
        rgb = [3, 132, 252]
    elif bld in ['retail', 'supermarket']:
        rgb = [253, 141, 60]
    elif bld in ['office', 'commercial']:
        rgb = [185, 206, 37]
    elif bld in ['school', 'kindergarten', 'university', 'college']:
        rgb = [128, 0, 38]
    elif bld in ['clinic', 'doctors', 'hospital']:
        rgb = [89, 182, 178]
    elif bld in ['community_centre', 'service', 'post_office', 'hall', 'civic', 
                  'townhall', 'police', 'library', 'fire_station']:
        rgb = [181, 182, 89]
    elif bld in ['warehouse', 'industrial']:
        rgb = [193, 255, 193]
    elif bld == 'hotel':
        rgb = [139, 117, 0]
    elif bld in ['church', 'mosque', 'synagogue']:
        rgb = [225, 225, 51]
    else:
        rgb = [255, 255, 204]
    return str(rgb)

def get_homebaked_plus_code(lat, lon):
    """Computes 11-digit Plus Code based on the Base20 offset formula."""
    alphabet = "23456789CFGHJMPQRVWX"
    lat_val, lon_val = lat + 90.0, lon + 180.0
    lat_digits, lon_digits = [], []
    l_rem, n_rem = lat_val / 20.0, lon_val / 20.0
    for _ in range(5):
        l_idx = int(l_rem)
        l_rem = (l_rem - l_idx) * 20.0
        lat_digits.append(alphabet[max(0, min(19, l_idx))])
        n_idx = int(n_rem)
        n_rem = (n_rem - n_idx) * 20.0
        lon_digits.append(alphabet[max(0, min(19, n_idx))])
    row, col = int(l_rem * 5 / 20), int(n_rem * 4 / 20)
    grid_idx = (row * 4) + col
    last_digit = alphabet[max(0, min(19, grid_idx))]
    return (f"{lat_digits[0]}{lon_digits[0]}{lat_digits[1]}{lon_digits[1]}"
            f"{lat_digits[2]}{lon_digits[2]}{lat_digits[3]}{lon_digits[3]}+"
            f"{lat_digits[4]}{lon_digits[4]}{last_digit}")

def process3D(layer):
    if not layer or not layer.isValid():
        return None

    # SCHEMA: All numeric outputs are set to String to force the "." decimal
    schema = [
        ('osm_id', QVariant.String), ('address', QVariant.String), 
        ('building', QVariant.String), ('building:levels', QVariant.String), 
        ('building:use', QVariant.String), ('building:flats', QVariant.String), 
        ('building:units', QVariant.String), ('beds', QVariant.String), 
        ('rooms', QVariant.String), ('residential', QVariant.String),
        ('amenity', QVariant.String), ('social_facility', QVariant.String), 
        ('operator', QVariant.String), 
        ('building_height', QVariant.String), 
        ('roof_height', QVariant.String), 
        ('ground_height', QVariant.String), 
        ('bottom_bridge_height', QVariant.String), 
        ('bottom_roof_height', QVariant.String),
        ('plus_code', QVariant.String), ('footprint', QVariant.String), 
        ('geometry_wkt', QVariant.String), ('fill_color', QVariant.String)
    ]

    layer.startEditing()
    existing_names = layer.fields().names()
    
    # Add missing columns
    to_add = [QgsField(n, t) for n, t in schema if n not in existing_names]
    if to_add:
        layer.dataProvider().addAttributes(to_add)
        layer.updateFields()
    
    fields = layer.fields()
    
    # Coordinate Transformer for Plus Codes (WGS84)
    xform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())

    for feat in layer.getFeatures():
        geom = feat.geometry()
        if not geom or geom.isEmpty(): continue
        if not geom.isGeosValid(): geom = geom.makeValid()

        # --- 1. RAW INPUT SANITIZATION ---
        def to_f(val):
            if QgsVariantUtils.isNull(val) or val == "": return 0.0
            try: return float(str(val).replace(',', '.'))
            except: return 0.0

        b_type = str(feat['building']) if 'building' in existing_names and not QgsVariantUtils.isNull(feat['building']) else 'house'
        levels = to_f(feat['building:levels']) if 'building:levels' in existing_names else 1.0
        ground_h_num = to_f(feat['mean']) if 'mean' in existing_names else 0.0
        min_h = to_f(feat['min_height']) if 'min_height' in existing_names else 0.0

        # --- 2. HEIGHT CALCULATIONS (Floating Point) ---
        storey_h = 2.8
        b_h = round(levels * storey_h + 1.3, 2)
        r_h = round(b_h + ground_h_num, 2)
        bb_h = None
        br_h = None

        if b_type == 'cabin':
            b_h = round(levels * storey_h, 2)
            r_h = round(b_h + ground_h_num, 2)
        elif b_type == 'bridge':
            bb_h = round(min_h + ground_h_num, 2)
        elif b_type == 'roof':
            br_h = round(levels * storey_h + ground_h_num, 2)
            r_h = round(br_h + 1.3, 2)
            b_h = None

        # --- 3. THE FIX: FORCE DOT FORMATTING ---
        def force_dot(val):
            if val is None: return None
            # Converts the number to string and ensures dot is used
            return "{:.2f}".format(float(val)).replace(',', '.')

        # --- 4. ADDRESS & PLUS CODE LOGIC ---
        address_keys = ['name', 'addr:housename', 'addr:flats', 'addr:housenumber', 'addr:street', 'addr:suburb', 'addr:postcode', 'addr:city', 'addr:province']
        address_parts = [str(feat[k]).strip() for k in address_keys if k in existing_names and not QgsVariantUtils.isNull(feat[k])]
        
        pt_geom = geom.pointOnSurface()
        pt_geom.transform(xform)
        p_code = get_homebaked_plus_code(pt_geom.asPoint().y(), pt_geom.asPoint().x())

        # --- 5. APPLY UPDATES ---
        updates = {
            fields.indexFromName('address'): " ".join(address_parts) if address_parts else None,
            fields.indexFromName('plus_code'): p_code,
            fields.indexFromName('building_height'): force_dot(b_h),
            fields.indexFromName('roof_height'): force_dot(r_h),
            fields.indexFromName('ground_height'): force_dot(ground_h_num),
            fields.indexFromName('bottom_bridge_height'): force_dot(bb_h),
            fields.indexFromName('bottom_roof_height'): force_dot(br_h),
            fields.indexFromName('footprint'): json.dumps(json.loads(geom.asJson())['coordinates']),
            fields.indexFromName('geometry_wkt'): geom.asWkt(),
            fields.indexFromName('fill_color'): get_rgb_color(b_type)
        }
        layer.changeAttributeValues(feat.id(), updates)

    if layer.commitChanges():
        layer.triggerRepaint()
        return layer
    return None

def q_farmland(large, focus):
    """Harvest landuse=farmland and add to project."""
    name = f"Farmland_{focus}"
    query = (f'[out:json][timeout:180];area[name="{large}"]->.L;area[name="{focus}"](area.L)->.a;(way["landuse"="farmland"](area.a);relation["landuse"="farmland"]["type"="multipolygon"](area.a););out geom;')
    data = _fetch_overpass(query)
    vlayer = QgsVectorLayer(json.dumps(_parse_to_geojson(data, "Polygon")), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    # Check if dataset is not empty before adding
    if final.featureCount() > 0:
        QgsProject.instance().addMapLayer(final)
        return final
    else:
        print(f"Skipped {name}: No features found.")
        return None

def q_green_spaces(large, focus):
    """Harvest leisure areas and add to project."""
    name = f"GreenSpaces_{focus}"
    query = (f'[out:json][timeout:180];area[name="{large}"]->.L;area[name="{focus}"](area.L)->.a;(way["leisure"~"park|track|pitch"](area.a);relation["leisure"~"park|track|pitch"]["type"="multipolygon"](area.a););out geom;')
    data = _fetch_overpass(query)
    vlayer = QgsVectorLayer(json.dumps(_parse_to_geojson(data, "Polygon")), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    # Check if dataset is not empty before adding
    if final.featureCount() > 0:
        QgsProject.instance().addMapLayer(final)
        return final
    else:
        print(f"Skipped {name}: No features found.")
        return None

def q_water(large, focus):
    """Harvest water features and add to project."""
    name = f"Water_{focus}"
    query = (f'[out:json][timeout:180];area[name="{large}"]->.L;area[name="{focus}"](area.L)->.a;(way["water"](area.a);way["waterway"="stream"](area.a);relation["water"]["type"="multipolygon"](area.a););out geom;')
    data = _fetch_overpass(query)
    vlayer = QgsVectorLayer(json.dumps(_parse_to_geojson(data, "Polygon")), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    # Check if dataset is not empty before adding
    if final.featureCount() > 0:
        QgsProject.instance().addMapLayer(final)
        return final
    else:
        print(f"Skipped {name}: No features found.")
        return None
    return final

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

def q_Troutes(large, operator='MyCiTi'):
    """Harvest bus routes and apply original RGB tuple conversion."""
    name = f"Transit_{operator}"
    query = (f'[out:json][timeout:180];area[name="{large}"];(relation["type"="route"]["route"="bus"]["operator"="{operator}"]["colour"](area););out geom;')
    
    data = _fetch_overpass(query)
    geojson = _parse_to_geojson(data, "MultiLineString")

    # Apply your hex_to_rgb conversion logic directly
    for feat in geojson["features"]:
        if "colour" in feat["properties"]:
            try:
                # Store the tuple directly as requested
                feat["properties"]["colour"] = hex_to_rgb(feat["properties"]["colour"])
            except Exception:
                feat["properties"]["colour"] = (255, 0, 0) # Fallback
            
    vlayer = QgsVectorLayer(json.dumps(geojson), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    # Check if dataset is not empty before adding
    if final.featureCount() > 0:
        QgsProject.instance().addMapLayer(final)
        return final
    else:
        print(f"Skipped {name}: No features found.")
        return None

def layer_to_geojson_dict(layer):
    """Converts a QGIS layer to a GeoJSON-style dictionary in WGS84."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GeoJSON"
    # Ensure coordinates are in WGS84 for MapLibre
    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    
    # Create a temporary file path
    temp_path = os.path.join(os.path.expanduser("~"), "temp_layer.geojson")
    QgsVectorFileWriter.writeAsVectorFormatV3(layer, temp_path, QgsProject.instance().transformContext(), options)
    
    with open(temp_path, 'r') as f:
        data = json.load(f)
    
    os.remove(temp_path)
    return data

#def create_3Dviz(result_dir, buildings_layer, roads_layer=None):
#def create_city_viz(buildings_layer, roads_layer=None, green_layer=None):

    html_path = os.path.join(result_dir, "interactiveOnly.html")
    #html_path = "/result/interactiveOnly.html"
    
    # Convert layers to Data
    building_data = layer_to_geojson_dict(buildings_layer)
    road_data = layer_to_geojson_dict(roads_layer) if roads_layer else {
        "type": "FeatureCollection", "features": []
    }
    green_data = layer_to_geojson_dict(green_layer) if green_layer else {
        "type": "FeatureCollection", "features": []
    }

    # --------------------------------------------------
    # 3. MAP CENTRE FROM QGIS EXTENT
    # --------------------------------------------------
    extent = buildings_layer.extent()
    center_coords = [
        (extent.xMinimum() + extent.xMaximum()) / 2,
        (extent.yMinimum() + extent.yMaximum()) / 2
    ]

    # --------------------------------------------------
    # 4. HTML + MAPLIBRE
    # --------------------------------------------------
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>geo3D – Interactive City</title>

    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />

    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: system-ui, sans-serif;
        }}
        #map {{
            position: absolute;
            top: 0;
            bottom: 0;
            width: 100%;
        }}
        .maplibregl-popup {{
            max-width: 320px;
        }}
        .popup-content {{
            font-size: 13px;
            line-height: 1.45;
        }}
    </style>
</head>

<body>
<div id="map"></div>

<script>
const map = new maplibregl.Map({{
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: {json.dumps(center_coords)},
    zoom: 16,
    pitch: 60,
    bearing: -15,
    antialias: true
}});

// Enable rotate + pitch
map.addControl(new maplibregl.NavigationControl({{
    visualizePitch: true
}}), 'top-right');

map.on('load', () => {{

    // -----------------------------
    // GREEN SPACES
    // -----------------------------
    map.addSource('green', {{
        type: 'geojson',
        data: {json.dumps(green_data)}
    }});

    map.addLayer({{
        id: 'green-layer',
        type: 'fill',
        source: 'green',
        paint: {{
            'fill-color': '#2e7d32',
            'fill-opacity': 0.4
        }}
    }});

    // -----------------------------
    // ROADS
    // -----------------------------
    map.addSource('roads', {{
        type: 'geojson',
        data: {json.dumps(road_data)}
    }});

    map.addLayer({{
        id: 'roads-layer',
        type: 'line',
        source: 'roads',
        paint: {{
            'line-color': '#555',
            'line-width': 1.5
        }}
    }});

    // -----------------------------
    // BUILDINGS (3D)
    // -----------------------------
    map.addSource('buildings', {{
        type: 'geojson',
        data: {json.dumps(building_data)}
    }});

    map.addLayer({{
        id: '3d-buildings',
        type: 'fill-extrusion',
        source: 'buildings',
        paint: {{
            'fill-extrusion-color': [
                'case',
                ['has', 'fill_color'],
                [
                    'rgba',
                    ['at', 0, ['get', 'fill_color']],
                    ['at', 1, ['get', 'fill_color']],
                    ['at', 2, ['get', 'fill_color']],
                    1
                ],
                '#aaaaaa'
            ],
            'fill-extrusion-height': [
                'coalesce',
                ['to-number', ['get', 'building_height']],
                10
            ],
            'fill-extrusion-base': [
                'coalesce',
                ['to-number', ['get', 'ground_height']],
                0
            ],
            'fill-extrusion-opacity': 0.65
        }}
    }});

    // -----------------------------
    // CLICK POPUPS
    // -----------------------------
    map.on('click', '3d-buildings', (e) => {{
        const p = e.features[0].properties;

        const html = `
            <div class="popup-content">
                <strong>Building:</strong> ${'{'}p.building || 'N/A'{'}'}<br><hr>
                <strong>Address:</strong> ${'{'}p.address || 'N/A'{'}'}<br>
                <strong>Plus Code:</strong> ${'{'}p.plus_code || 'N/A'{'}'}<br>
                <strong>Height:</strong> ${'{'}p.building_height || 0{'}'} m
            </div>
        `;

        new maplibregl.Popup()
            .setLngLat(e.lngLat)
            .setHTML(html)
            .addTo(map);
    }});

    map.on('mouseenter', '3d-buildings', () => {{
        map.getCanvas().style.cursor = 'pointer';
    }});

    map.on('mouseleave', '3d-buildings', () => {{
        map.getCanvas().style.cursor = '';
    }});
    }});
    </script>
    </body>
    </html>
    """

    # --------------------------------------------------
    # 5. WRITE FILE
    # --------------------------------------------------
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

#def create_3Dviz(result_dir, buildings_layer, farmland_layer=None, green_layer=None, water_layer=None, bus_layer=None):
    html_path = os.path.join(result_dir, "interactiveOnly.html")
    
    # 1. Convert layers to Data
    building_data = layer_to_geojson_dict(buildings_layer)
    green_data = layer_to_geojson_dict(green_layer) if green_layer else {
        "type": "FeatureCollection", "features": []
    }
    farmland_data = layer_to_geojson_dict(farmland_layer) if farmland_layer else {
        "type": "FeatureCollection", "features": []
    }
    water_data = layer_to_geojson_dict(water_layer) if water_layer else {
        "type": "FeatureCollection", "features": []
    }
    bus_data = layer_to_geojson_dict(bus_layer) if bus_layer else {
        "type": "FeatureCollection", "features": []
    }

    # --------------------------------------------------
    # 3. MAP CENTRE FROM QGIS EXTENT
    # --------------------------------------------------
    extent = buildings_layer.extent()
    center_coords = [
        (extent.xMinimum() + extent.xMaximum()) / 2,
        (extent.yMinimum() + extent.yMaximum()) / 2
    ]

    # --------------------------------------------------
    # 4. HTML + MAPLIBRE
    # --------------------------------------------------
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>geo3D – Interactive City</title>

    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />

    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: system-ui, sans-serif;
        }}
        #map {{
            position: absolute;
            top: 0;
            bottom: 0;
            width: 100%;
        }}
        .maplibregl-popup {{
            max-width: 320px;
        }}
        .popup-content {{
            font-size: 13px;
            line-height: 1.45;
        }}
    </style>
</head>

<body>
<div id="map"></div>

<script>
const map = new maplibregl.Map({{
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: {json.dumps(center_coords)},
    zoom: 16,
    pitch: 60,
    bearing: -15,
    antialias: true
}});

// Enable rotate + pitch
map.addControl(new maplibregl.NavigationControl({{
    visualizePitch: true
}}), 'top-right');

map.on('load', () => {{

    // -----------------------------
    // WATER
    // -----------------------------
    map.addSource('water', {{
        type: 'geojson',
        data: {json.dumps(water_data)}
    }});

    map.addLayer({{
        id: 'water-layer',
        type: 'fill',
        source: 'water',
        paint: {{
            'fill-color': '#01579b',
            'fill-opacity': 0.5
        }}
    }});

    // -----------------------------
    // FARMLAND
    // -----------------------------
    map.addSource('farmland', {{
        type: 'geojson',
        data: {json.dumps(farmland_data)}
    }});

    map.addLayer({{
        id: 'farmland-layer',
        type: 'fill',
        source: 'farmland',
        paint: {{
            'fill-color': '#3e2723',
            'fill-opacity': 0.3
        }}
    }});

    // -----------------------------
    // GREEN SPACES
    // -----------------------------
    map.addSource('green', {{
        type: 'geojson',
        data: {json.dumps(green_data)}
    }});

    map.addLayer({{
        id: 'green-layer',
        type: 'fill',
        source: 'green',
        paint: {{
            'fill-color': '#66bb6a',
            'fill-opacity': 0.7
        }}
    }});

    // -----------------------------
    // BUS ROUTES (BRT)
    // -----------------------------
    map.addSource('bus', {{
        type: 'geojson',
        data: {json.dumps(bus_data)}
    }});

    map.addLayer({{
        id: 'bus-layer',
        type: 'line',
        source: 'bus',
        layout: {{
            'line-join': 'round',
            'line-cap': 'round'
        }},
        paint: {{
            'line-color': [
                'case',
                ['has', 'colour'],
                [
                    'rgb',
                    ['at', 0, ['get', 'colour']],
                    ['at', 1, ['get', 'colour']],
                    ['at', 2, ['get', 'colour']]
                ],
                '#FF0000' // Fallback Red if no colour column exists
            ],
            'line-width': 4,
            'line-opacity': 0.9
        }}
    }});

    // -----------------------------
    // BUILDINGS (3D)
    // -----------------------------
    map.addSource('buildings', {{
        type: 'geojson',
        data: {json.dumps(building_data)}
    }});

    map.addLayer({{
        id: '3d-buildings',
        type: 'fill-extrusion',
        source: 'buildings',
        paint: {{
            'fill-extrusion-color': [
                'case',
                ['has', 'fill_color'],
                [
                    'rgba',
                    ['at', 0, ['get', 'fill_color']],
                    ['at', 1, ['get', 'fill_color']],
                    ['at', 2, ['get', 'fill_color']],
                    1
                ],
                '#aaaaaa'
            ],
            'fill-extrusion-height': [
                'coalesce',
                ['to-number', ['get', 'building_height']],
                10
            ],
            'fill-extrusion-base': [
                'coalesce',
                ['to-number', ['get', 'ground_height']],
                0
            ],
            'fill-extrusion-opacity': 0.65
        }}
    }});

    // -----------------------------
    // CLICK POPUPS
    // -----------------------------
    map.on('click', '3d-buildings', (e) => {{
        const p = e.features[0].properties;

        const html = `
            <div class="popup-content">
                <strong>Building:</strong> ${'{'}p.building || 'N/A'{'}'}<br><hr>
                <strong>Address:</strong> ${'{'}p.address || 'N/A'{'}'}<br>
                <strong>Plus Code:</strong> ${'{'}p.plus_code || 'N/A'{'}'}<br>
                <strong>Height:</strong> ${'{'}p.building_height || 0{'}'} m
            </div>
        `;

        new maplibregl.Popup()
            .setLngLat(e.lngLat)
            .setHTML(html)
            .addTo(map);
    }});

    map.on('mouseenter', '3d-buildings', () => {{
        map.getCanvas().style.cursor = 'pointer';
    }});

    map.on('mouseleave', '3d-buildings', () => {{
        map.getCanvas().style.cursor = '';
    }});
    }});
    </script>
    </body>
    </html>
    """

    # --------------------------------------------------
    # 5. WRITE FILE
    # --------------------------------------------------
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

def create_3Dviz(result_dir, buildings_layer, farmland_layer=None, green_layer=None, water_layer=None, bus_layer=None):
    html_path = os.path.join(result_dir, "interactiveOnly.html")
    
    # 1. Convert layers to GeoJSON Data
    building_data = layer_to_geojson_dict(buildings_layer)
    green_data = layer_to_geojson_dict(green_layer) if green_layer else {"type": "FeatureCollection", "features": []}
    farmland_data = layer_to_geojson_dict(farmland_layer) if farmland_layer else {"type": "FeatureCollection", "features": []}
    water_data = layer_to_geojson_dict(water_layer) if water_layer else {"type": "FeatureCollection", "features": []}
    bus_data = layer_to_geojson_dict(bus_layer) if bus_layer else {"type": "FeatureCollection", "features": []}

    # 2. Get Map Center
    extent = buildings_layer.extent()
    center_coords = [
        (extent.xMinimum() + extent.xMaximum()) / 2,
        (extent.yMinimum() + extent.yMaximum()) / 2
    ]

    # 3. HTML Content
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <title>geo3D – Interactive City</title>
    <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet" />
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
        .popup-content {{ font-size: 13px; line-height: 1.45; font-family: sans-serif; }}
    </style>
</head>
<body>
<div id="map"></div>
<script>
const map = new maplibregl.Map({{
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: {json.dumps(center_coords)},
    zoom: 16,
    pitch: 60,
    antialias: true
}});

// Enable rotate + pitch
map.addControl(new maplibregl.NavigationControl({{
    visualizePitch: true
}}), 'top-right');

map.on('load', () => {{
    // Add Sources
    map.addSource('water', {{ type: 'geojson', data: {json.dumps(water_data)} }});
    map.addSource('green', {{ type: 'geojson', data: {json.dumps(green_data)} }});
    map.addSource('bus', {{ type: 'geojson', data: {json.dumps(bus_data)} }});
    map.addSource('buildings', {{ type: 'geojson', data: {json.dumps(building_data)} }});

    // Water
    map.addLayer({{
        id: 'water-layer', type: 'fill', source: 'water',
        paint: {{ 'fill-color': '#01579b', 'fill-opacity': 0.5 }}
    }});

    // Green
    map.addLayer({{
        id: 'green-layer', type: 'fill', source: 'green',
        paint: {{ 'fill-color': '#66bb6a', 'fill-opacity': 0.5 }}
    }});

    // Bus Routes (Styled by the RGB column you created)
    map.addLayer({{
        id: 'bus-layer',
        type: 'line',
        source: 'bus',
        layout: {{ 'line-join': 'round', 'line-cap': 'round' }},
        paint: {{
            'line-color': [
                'case',
                ['has', 'colour'],
                ['rgb', ['at', 0, ['get', 'colour']], ['at', 1, ['get', 'colour']], ['at', 2, ['get', 'colour']]],
                '#FF4500'
            ],
            'line-width': 3
        }}
    }});

    // 3D Buildings
    map.addLayer({{
        id: '3d-buildings',
        type: 'fill-extrusion',
        source: 'buildings',
        paint: {{
            'fill-extrusion-color': [
                'case',
                ['has', 'fill_color'],
                ['rgb', ['at', 0, ['get', 'fill_color']], ['at', 1, ['get', 'fill_color']], ['at', 2, ['get', 'fill_color']]],
                '#aaaaaa'
            ],
            'fill-extrusion-height': ['coalesce', ['to-number', ['get', 'building_height']], 10],
            'fill-extrusion-opacity': 0.65
        }}
    }});

    // Simple Click Popup (Buildings Only)
    map.on('click', '3d-buildings', (e) => {{
        const p = e.features[0].properties;
        const html = `
            <div class="popup-content">
                <strong>Building:</strong> ${'{'}p.building || 'N/A'{'}'}<br><hr>
                <strong>Address:</strong> ${'{'}p.address || 'N/A'{'}'}<br>
                <strong>Plus Code:</strong> ${'{'}p.plus_code || 'N/A'{'}'}<br>
                <strong>Height:</strong> ${'{'}p.building_height || 0{'}'} m
            </div>`;

        new maplibregl.Popup()
            .setLngLat(e.lngLat)
            .setHTML(html)
            .addTo(map);
    }});

    map.on('mouseenter', '3d-buildings', () => {{ map.getCanvas().style.cursor = 'pointer'; }});
    map.on('mouseleave', '3d-buildings', () => {{ map.getCanvas().style.cursor = ''; }});
}});
</script>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return html_path

def get_utm_crs(gdf):
    # 1. Calculate center point to determine UTM zone
    # We use WGS84 coordinates for the calculation
    wgs84_gdf = gdf.to_crs("EPSG:4326")
    avg_lon = wgs84_gdf.geometry.centroid.x.mean()
    avg_lat = wgs84_gdf.geometry.centroid.y.mean()
    
    utm_zone = int((avg_lon + 180) / 6) + 1
    # 326xx for North, 327xx for South
    epsg_prefix = 32600 if avg_lat >= 0 else 32700
    epsg_code = epsg_prefix + utm_zone
    
    # 2. Initialize the CRS object from the EPSG
    utm_crs = CRS.from_epsg(epsg_code)
    
    # 3. Print the full detailed report
    # Using 'print(utm_crs)' in some environments only shows the code.
    # To force the full report, we can use the __repr__ or specifically format it.
    #print("-" * 30)
    print(utm_crs.to_string()) # This usually gives the detailed block
    #print("-" * 30)
    
    # If the above is still short, this is the guaranteed full metadata:
    # (Matches the exact printout you requested)
    return epsg_code

def q_solar(large, focus):
    """Harvest solar (power=generator) and add to project."""
    name = f"Solar_{focus}"
    query = (f'[out:json][timeout:180];area[name="{large}"]->.L;area[name="{focus}"](area.L)->.a;(way["power"="generator"]["generator:source"="solar"](area.a););out geom;')
    data = _fetch_overpass(query)
    vlayer = QgsVectorLayer(json.dumps(_parse_to_geojson(data, "Polygon")), name, "ogr")
    final = vlayer.materialize(QgsFeatureRequest())
    
    _remove_layer_by_name(name)
    QgsProject.instance().addMapLayer(final)
    return final

def save_to_geopackage(gpkg_path, target_crs_string):
    # 1. Delete the old file if it exists to avoid locking/append issues
    if os.path.exists(gpkg_path):
        try:
            os.remove(gpkg_path)
            print(f"Cleared existing file: {gpkg_path}")
        except Exception as e:
            print(f"Could not delete existing file (it may be open in QGIS): {e}")
            return

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.destCRS = QgsCoordinateReferenceSystem(target_crs_string)
    context = QgsCoordinateTransformContext()
    
    # 2. Iterate and write
    for i, layer in enumerate(QgsProject.instance().mapLayers().values()):
        if layer.type() == QgsMapLayer.VectorLayer:
            options.layerName = layer.name().replace(" ", "_").lower()
            
            # The first layer creates the file, subsequent layers add to it
            if i == 0:
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            else:
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            
            error, message = QgsVectorFileWriter.writeAsVectorFormatV2(
                layer, gpkg_path, context, options
            )
            
            if error == QgsVectorFileWriter.NoError:
                print(f"Exported: {layer.name()}")
            else:
                print(f"Failed {layer.name()}: {message}")