#!/usr/bin/env python3
"""Analyse des coordonnées dans GC40."""

import re

def analyze_coords():
    with open('gc40_debug.html', 'r', encoding='utf-8') as f:
        html = f.read()

    print('=== RECHERCHE COORDONNÉES RÉELLES ===')

    # Chercher toutes les occurrences de coordonnées numériques
    coords = re.findall(r'\b\d+\.\d+\b', html)
    unique_coords = sorted(list(set(coords)))[:20]  # Les 20 premières uniques
    print(f'Coordonnées numériques trouvées: {unique_coords}')

    # Chercher spécifiquement les patterns de latitude/longitude
    lat_lon_patterns = [
        r'N\s*\d+°\s*\d+\.\d+',
        r'E\s*\d+°\s*\d+\.\d+',
        r'S\s*\d+°\s*\d+\.\d+',
        r'W\s*\d+°\s*\d+\.\d+',
    ]

    for pattern in lat_lon_patterns:
        matches = re.findall(pattern, html)
        if matches:
            print(f'Pattern {pattern}: {matches[:5]}')

    # Chercher dans les éléments avec ID spécifique
    print('\n=== ÉLÉMENTS SPÉCIFIQUES ===')
    if 'ctl00_ContentBody_LatLon' in html:
        print('✓ ctl00_ContentBody_LatLon trouvé')
        latlon_match = re.search(r'id="ctl00_ContentBody_LatLon"[^>]*>([^<]+)</span>', html, re.IGNORECASE)
        if latlon_match:
            print(f'Contenu: "{latlon_match.group(1).strip()}"')
    else:
        print('✗ ctl00_ContentBody_LatLon pas trouvé')

    # Chercher les vraies coordonnées dans le texte visible
    print('\n=== TEXTE VISIBLE AVEC COORDONNÉES ===')
    visible_coords = re.findall(r'[NS]\s*\d+°\s*\d+\.\d+\s*[EW]\s*\d+°\s*\d+\.\d+', html)
    if visible_coords:
        print(f'Coordonnées complètes trouvées: {visible_coords}')
    else:
        print('Aucune coordonnée complète trouvée dans le texte visible')

    # Chercher dans les scripts JavaScript
    print('\n=== COORDONNÉES DANS SCRIPTS ===')
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL)
    for i, script in enumerate(scripts):
        if len(script) > 1000:  # Scripts longs seulement
            lat_matches = re.findall(r'([+-]?\d+\.\d+)', script)
            unique_lats = list(set(lat_matches))
            if unique_lats:
                print(f'Script {i}: coordonnées possibles: {unique_lats[:10]}')

    # Chercher dans les variables JavaScript spécifiques
    print('\n=== VARIABLES JAVASCRIPT COORDONNÉES ===')
    js_vars = re.findall(r'var\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([^;]+)', html)
    coord_related = []
    for name, value in js_vars:
        if any(keyword in name.lower() for keyword in ['lat', 'lon', 'coord', 'latitude', 'longitude']):
            coord_related.append((name, value[:50] + '...' if len(value) > 50 else value))

    if coord_related:
        print('Variables liées aux coordonnées:')
        for name, value in coord_related[:10]:
            print(f'  {name} = {value}')
    else:
        print('Aucune variable JavaScript liée aux coordonnées trouvée')

if __name__ == '__main__':
    analyze_coords()
