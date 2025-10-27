#!/usr/bin/env python3
"""Analyse détaillée du HTML de GC40."""

import re

def analyze_html():
    with open('gc40_debug.html', 'r', encoding='utf-8') as f:
        html = f.read()

    print('=== RECHERCHE COORDONNÉES DANS HTML ===')

    # Chercher uxLatLon avec son contenu
    ux_matches = re.findall(r'<span[^>]*id="uxLatLon"[^>]*>(.*?)</span>', html, re.IGNORECASE | re.DOTALL)
    print(f'uxLatLon spans trouvés: {len(ux_matches)}')
    for i, match in enumerate(ux_matches[:3]):
        print(f'  {i+1}: "{match.strip()}"')

    # Chercher les coordonnées dans les meta tags
    meta_lat = re.search(r'<meta[^>]*property="place:location:latitude"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    meta_lon = re.search(r'<meta[^>]*property="place:location:longitude"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if meta_lat and meta_lon:
        print(f'Coordonnées meta: {meta_lat.group(1)}, {meta_lon.group(1)}')

    # Chercher dans userDefinedCoords
    user_coords = re.search(r'userDefinedCoords.*?newLatLng.*?[\s]*([\d.-]+)[\s]*,[\s]*([\d.-]+)[\s]*', html, re.IGNORECASE | re.DOTALL)
    if user_coords:
        print(f'Coordonnées userDefinedCoords: {user_coords.group(1)}, {user_coords.group(2)}')

    # Chercher toutes les coordonnées dans le texte
    coord_text = re.findall(r'N\s*\d+°\s*\d+\.\d+\s*E\s*\d+°\s*\d+\.\d+', html)
    if coord_text:
        print(f'Coordonnées texte trouvées: {coord_text}')

    print('\n=== RECHERCHE FAVORIS ===')
    fav_matches = re.findall(r'(\d+)\s*favorites?', html, re.IGNORECASE)
    if fav_matches:
        print(f'Favoris trouvés: {fav_matches}')

    # Chercher dans les spans
    fav_spans = re.findall(r'<span[^>]*>(\d+)</span>', html)
    fav_spans = [x for x in fav_spans if x and x.isdigit()]
    if fav_spans:
        print(f'Chiffres dans spans: {fav_spans}')

    print('\n=== RECHERCHE LOGS ===')
    log_matches = re.findall(r'(\d+)\s*log', html, re.IGNORECASE)
    if log_matches:
        print(f'Logs trouvés: {log_matches}')

    # Chercher tous les liens
    links = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', html, re.IGNORECASE)
    geocache_links = [link for link in links if 'geocache_logs' in link[0]]
    if geocache_links:
        print(f'Liens geocache_logs: {geocache_links}')

    print('\n=== STRUCTURE GÉNÉRALE ===')
    if 'ctl00_ContentBody_CacheName' in html:
        print('✓ Ancien format ASP.NET détecté')
    else:
        print('✗ Ancien format ASP.NET non détecté')

    if 'data-testid' in html:
        print('✓ Nouveau format React détecté')
    else:
        print('✗ Nouveau format React non détecté')

    print('\n=== ÉLÉMENTS ASP.NET TROUVÉS ===')
    ctl_elements = re.findall(r'ctl00_ContentBody_[^"\s>]+', html)
    unique_ctl = sorted(list(set(ctl_elements)))
    print(f'Éléments ctl00_ContentBody_: {unique_ctl}')

    print('\n=== COORDONNÉES DANS SCRIPTS ===')
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
    for i, script in enumerate(scripts):
        if 'lat' in script.lower() and ('lon' in script.lower() or 'lng' in script.lower()):
            print(f'Script {i+1} contient coordonnées:')
            # Chercher les vraies coordonnées
            lat_match = re.search(r'lat[^=]*=\s*([-\d.]+)', script, re.IGNORECASE)
            lon_match = re.search(r'lon[^=]*=\s*([-\d.]+)', script, re.IGNORECASE)
            lng_match = re.search(r'lng[^=]*=\s*([-\d.]+)', script, re.IGNORECASE)
            if lat_match:
                print(f'  Latitude: {lat_match.group(1)}')
            if lon_match:
                print(f'  Longitude: {lon_match.group(1)}')
            elif lng_match:
                print(f'  Longitude (lng): {lng_match.group(1)}')

if __name__ == '__main__':
    analyze_html()
