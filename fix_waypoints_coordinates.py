#!/usr/bin/env python3
"""
Script pour corriger les coordonnées décimales des waypoints
en les recalculant à partir des coordonnées au format Geocaching
"""

import re
import sys
from pathlib import Path

# Ajouter le répertoire parent au path pour importer les modules
sys.path.insert(0, str(Path(__file__).parent))

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.geocaches.models import GeocacheWaypoint


def parse_gc_coords(gc_coords: str) -> tuple[float, float] | None:
    """
    Parse les coordonnées au format Geocaching vers décimal
    
    Exemples:
        "N 48° 38.204, E 006° 07.945" → (48.63673333, 6.13241666)
        "N 48° 38.104 E 006° 07.445" → (48.63506666, 6.12408333)
    """
    if not gc_coords:
        return None
    
    # Séparer latitude et longitude (avec ou sans virgule)
    parts = re.split(r'[,\s]+(?=[NSEW])', gc_coords.strip())
    if len(parts) < 2:
        return None
    
    lat_str = parts[0].strip()
    lon_str = parts[1].strip()
    
    # Parser latitude
    lat_match = re.match(r'([NS])\s*(\d+)°\s*([\d.]+)', lat_str)
    if not lat_match:
        return None
    
    # Parser longitude
    lon_match = re.match(r'([EW])\s*(\d+)°\s*([\d.]+)', lon_str)
    if not lon_match:
        return None
    
    # Calculer latitude décimale
    lat_deg = int(lat_match.group(2))
    lat_min = float(lat_match.group(3))
    lat = lat_deg + (lat_min / 60.0)
    if lat_match.group(1) == 'S':
        lat = -lat
    
    # Calculer longitude décimale
    lon_deg = int(lon_match.group(2))
    lon_min = float(lon_match.group(3))
    lon = lon_deg + (lon_min / 60.0)
    if lon_match.group(1) == 'W':
        lon = -lon
    
    return (lat, lon)


def fix_waypoints():
    """Corrige les coordonnées décimales de tous les waypoints"""
    
    app = create_app()
    
    with app.app_context():
        # Récupérer tous les waypoints qui ont des coordonnées GC
        waypoints = GeocacheWaypoint.query.filter(
            GeocacheWaypoint.gc_coords.isnot(None),
            GeocacheWaypoint.gc_coords != ''
        ).all()
        
        print(f"Trouvé {len(waypoints)} waypoints avec coordonnées GC")
        print()
        
        fixed_count = 0
        error_count = 0
        
        for wp in waypoints:
            print(f"Waypoint #{wp.id}: {wp.name}")
            print(f"  GC coords: {wp.gc_coords}")
            print(f"  Avant: lat={wp.latitude}, lon={wp.longitude}")
            
            # Parser les coordonnées GC
            result = parse_gc_coords(wp.gc_coords)
            
            if result:
                new_lat, new_lon = result
                
                # Vérifier si les coordonnées ont changé
                if wp.latitude != new_lat or wp.longitude != new_lon:
                    wp.latitude = new_lat
                    wp.longitude = new_lon
                    print(f"  Après:  lat={new_lat:.8f}, lon={new_lon:.8f} ✅ CORRIGÉ")
                    fixed_count += 1
                else:
                    print(f"  Après:  lat={new_lat:.8f}, lon={new_lon:.8f} ✓ Déjà correct")
            else:
                print(f"  ❌ ERREUR: Impossible de parser les coordonnées GC")
                error_count += 1
            
            print()
        
        # Sauvegarder les modifications
        if fixed_count > 0:
            try:
                db.session.commit()
                print(f"✅ {fixed_count} waypoint(s) corrigé(s)")
            except Exception as e:
                db.session.rollback()
                print(f"❌ Erreur lors de la sauvegarde: {e}")
                return
        else:
            print("✓ Aucune correction nécessaire")
        
        if error_count > 0:
            print(f"⚠️  {error_count} waypoint(s) avec erreur de parsing")
        
        print()
        print("Terminé !")


if __name__ == '__main__':
    fix_waypoints()
