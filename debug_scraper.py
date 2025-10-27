#!/usr/bin/env python3
"""Script de debug pour analyser le HTML de GC40."""

import sys
import os
import re
import requests

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(__file__))

def debug_gc40():
    print('=== Analyse HTML de GC40 ===')

    try:
        url = 'https://www.geocaching.com/geocache/GC40'
        print(f'Fetching {url}...')

        resp = requests.get(url, timeout=30)
        print(f'Status: {resp.status_code}')
        print(f'Content length: {len(resp.text)}')

        # Sauvegarder le HTML pour analyse
        with open('gc40_debug.html', 'w', encoding='utf-8') as f:
            f.write(resp.text)
        print('HTML sauvegardé dans gc40_debug.html')

        # Chercher les coordonnées
        print('\n=== COORDONNÉES ===')
        if 'uxLatLon' in resp.text:
            print('✓ uxLatLon trouvé dans le HTML')
            ux_match = re.search(r'id="uxLatLon"[^>]*>([^<]+)</span>', resp.text)
            if ux_match:
                coords = ux_match.group(1).strip()
                print(f'Coordonnées extraites: "{coords}"')
            else:
                print('✗ uxLatLon trouvé mais pas de contenu')
                # Montrer le contexte autour de uxLatLon
                ux_context = re.search(r'.{100}uxLatLon.{100}', resp.text, re.DOTALL)
                if ux_context:
                    print(f'Contexte: {ux_context.group(0)}')
        else:
            print('✗ uxLatLon pas trouvé')

        # Chercher favoris
        print('\n=== FAVORIS ===')
        if 'favorite-value' in resp.text:
            print('✓ favorite-value trouvé dans le HTML')
            fav_match = re.search(r'class="favorite-value"[^>]*>(\d+)</span>', resp.text)
            if fav_match:
                favs = fav_match.group(1)
                print(f'Favoris extraits: {favs}')
            else:
                print('✗ favorite-value trouvé mais pas de nombre')
                # Montrer le contexte
                fav_context = re.search(r'.{100}favorite-value.{100}', resp.text, re.DOTALL)
                if fav_context:
                    print(f'Contexte: {fav_context.group(0)}')
        else:
            print('✗ favorite-value pas trouvé')

        # Chercher logs
        print('\n=== LOGS ===')
        if 'geocache_logs.aspx' in resp.text:
            print('✓ geocache_logs.aspx trouvé dans le HTML')
            logs_match = re.search(r'href="[^"]*geocache_logs\.aspx[^"]*">([^<]+)</a>', resp.text)
            if logs_match:
                logs_text = logs_match.group(1)
                print(f'Texte logs extrait: "{logs_text}"')
            else:
                print('✗ geocache_logs.aspx trouvé mais pas de texte')
                # Montrer le contexte
                logs_context = re.search(r'.{100}geocache_logs\.aspx.{100}', resp.text, re.DOTALL)
                if logs_context:
                    print(f'Contexte: {logs_context.group(0)}')
        else:
            print('✗ geocache_logs.aspx pas trouvé')

        # Chercher attributs
        print('\n=== ATTRIBUTS ===')
        if 'WidgetBody' in resp.text:
            print('✓ WidgetBody trouvé')
        else:
            print('✗ WidgetBody pas trouvé')

        # Chercher description
        print('\n=== DESCRIPTION ===')
        if 'ctl00_ContentBody_LongDescription' in resp.text:
            print('✓ LongDescription trouvé')
        else:
            print('✗ LongDescription pas trouvé')

    except Exception as e:
        print(f'Erreur: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    debug_gc40()
