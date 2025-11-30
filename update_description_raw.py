#!/usr/bin/env python3
"""
Script pour mettre à jour le champ description_raw des géocaches existantes.

Ce script parcoure toutes les géocaches qui ont description_html
mais pas description_raw, et extrait le texte brut du HTML.
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from gc_backend.database import db
from gc_backend.geocaches.models import Geocache
from bs4 import BeautifulSoup
from loguru import logger

def update_description_raw():
    """Met à jour le champ description_raw pour les géocaches existantes."""
    logger.info("Début mise à jour description_raw...")

    try:
        # Initialiser la base de données
        db.create_all()

        # Trouver les géocaches qui ont description_html mais pas description_raw
        geocaches_to_update = Geocache.query.filter(
            Geocache.description_html.isnot(None),
            Geocache.description_raw.is_(None)
        ).all()

        logger.info(f"Trouvé {len(geocaches_to_update)} géocaches à mettre à jour")

        updated_count = 0
        error_count = 0

        for geocache in geocaches_to_update:
            try:
                # Extraire le texte brut du HTML
                soup = BeautifulSoup(geocache.description_html, 'html.parser')
                description_raw = soup.get_text(strip=True)

                if description_raw and len(description_raw.strip()) > 0:
                    geocache.description_raw = description_raw
                    updated_count += 1

                    if updated_count % 100 == 0:
                        logger.info(f"Progression: {updated_count}/{len(geocaches_to_update)} géocaches mises à jour")
                else:
                    logger.warning(f"Geocache {geocache.gc_code}: description_raw vide après extraction")
                    error_count += 1

            except Exception as e:
                logger.warning(f"Erreur traitement geocache {geocache.gc_code}: {e}")
                error_count += 1
                continue

        # Commit des changements
        if updated_count > 0:
            db.session.commit()
            logger.info(f"Mise à jour terminée: {updated_count} géocaches mises à jour avec succès")

            if error_count > 0:
                logger.warning(f"{error_count} géocaches ont eu des erreurs lors de la mise à jour")
        else:
            logger.info("Aucune géocache à mettre à jour")

        return updated_count, error_count

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erreur lors de la mise à jour: {e}")
        raise

if __name__ == "__main__":
    print("Mise à jour du champ description_raw des géocaches...")
    print("=" * 60)

    try:
        updated, errors = update_description_raw()

        print("=" * 60)
        print(f"RÉSULTAT: {updated} géocaches mises à jour")

        if errors > 0:
            print(f"ATTENTION: {errors} erreurs rencontrées")

        print("\nLe Formula Solver utilisera maintenant le texte propre (sans HTML)")
        print("pour la détection de formules et l'extraction de questions !")

    except Exception as e:
        print(f"ERREUR: {e}")
        sys.exit(1)

