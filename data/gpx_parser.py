"""
CAIRN GPX Parser
Analysiert eine GPX-Datei und gibt Kennzahlen zurück die der Coach für die Plangestaltung nutzt.
"""
import xml.etree.ElementTree as ET
import math

def parse_gpx(gpx_content: str) -> dict:
    """
    Parst eine GPX-Datei und gibt Streckenkennzahlen zurück.
    gpx_content: GPX-Datei als String
    """
    try:
        root = ET.fromstring(gpx_content)
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}

        # Trackpoints sammeln
        points = []
        for trkpt in root.findall('.//gpx:trkpt', ns):
            lat = float(trkpt.get('lat', 0))
            lon = float(trkpt.get('lon', 0))
            ele_el = trkpt.find('gpx:ele', ns)
            ele = float(ele_el.text) if ele_el is not None else 0
            points.append({'lat': lat, 'lon': lon, 'ele': ele})

        if len(points) < 2:
            return {"error": "Zu wenige Trackpoints"}

        # Distanz berechnen (Haversine)
        total_dist_km = 0
        for i in range(1, len(points)):
            total_dist_km += haversine(
                points[i-1]['lat'], points[i-1]['lon'],
                points[i]['lat'], points[i]['lon']
            )

        # Höhenmeter
        total_gain = 0
        total_loss = 0
        for i in range(1, len(points)):
            diff = points[i]['ele'] - points[i-1]['ele']
            if diff > 0:
                total_gain += diff
            else:
                total_loss += abs(diff)

        # Min/Max Elevation
        elevations = [p['ele'] for p in points if p['ele'] > 0]
        min_ele = min(elevations) if elevations else 0
        max_ele = max(elevations) if elevations else 0

        # Maximale Steigung (pro 100m Abschnitte)
        max_grade = 0
        segment_dist = 0
        segment_gain = 0
        for i in range(1, len(points)):
            d = haversine(points[i-1]['lat'], points[i-1]['lon'], points[i]['lat'], points[i]['lon'])
            segment_dist += d
            ele_diff = points[i]['ele'] - points[i-1]['ele']
            if ele_diff > 0:
                segment_gain += ele_diff
            if segment_dist >= 0.1:  # 100m Abschnitte
                if segment_dist > 0:
                    grade = (segment_gain / (segment_dist * 1000)) * 100
                    max_grade = max(max_grade, grade)
                segment_dist = 0
                segment_gain = 0

        # Streckenprofil kategorisieren
        gain_per_km = total_gain / total_dist_km if total_dist_km > 0 else 0
        if gain_per_km < 20:
            profile = "flat"
            profile_de = "flach"
        elif gain_per_km < 60:
            profile = "rolling"
            profile_de = "hügelig"
        elif gain_per_km < 120:
            profile = "hilly"
            profile_de = "bergig"
        else:
            profile = "mountainous"
            profile_de = "alpin"

        return {
            "distance_km": round(total_dist_km, 1),
            "elevation_gain_m": round(total_gain),
            "elevation_loss_m": round(total_loss),
            "min_elevation_m": round(min_ele),
            "max_elevation_m": round(max_ele),
            "gain_per_km": round(gain_per_km, 1),
            "max_grade_pct": round(max_grade, 1),
            "profile": profile,
            "profile_de": profile_de,
            "point_count": len(points),
            "summary": f"{round(total_dist_km, 1)} km · +{round(total_gain)} m / -{round(total_loss)} m · {profile_de}"
        }

    except Exception as e:
        return {"error": str(e)}

def haversine(lat1, lon1, lat2, lon2):
    """Distanz zwischen zwei GPS-Punkten in km"""
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            content = f.read()
        result = parse_gpx(content)
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))