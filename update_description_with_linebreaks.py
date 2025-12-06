#!/usr/bin/env python3
"""
Script pour mettre à jour le champ description_raw des géocaches existantes
en préservant les sauts de ligne du HTML.

Ce script parcoure toutes les géocaches qui ont description_html
et met à jour description_raw avec le texte brut incluant les sauts de ligne.
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from gc_backend.database import db
from gc_backend.geocaches.models import Geocache
from gc_backend.utils.html_cleaner import html_to_text_with_linebreaks
from loguru import logger


def update_description_raw_with_linebreaks(force_update: bool = False):
    """
    Met à jour le champ description_raw pour les géocaches existantes
    en préservant les sauts de ligne.
    
    Args:
        force_update: Si True, met à jour toutes les géocaches même si description_raw existe déjà
    """
    logger.info("Début mise à jour description_raw avec sauts de ligne...")

    try:
        # Initialiser la base de données
        db.create_all()

        # Trouver les géocaches à mettre à jour
        if force_update:
            # Mettre à jour toutes les géocaches avec description_html
            geocaches_to_update = Geocache.query.filter(
                Geocache.description_html.isnot(None)
            ).all()
            logger.info(f"Mode force: mise à jour de toutes les {len(geocaches_to_update)} géocaches avec description_html")
        else:
            # Mettre à jour seulement celles qui n'ont pas de sauts de ligne
            geocaches_to_update = Geocache.query.filter(
                Geocache.description_html.isnot(None)
            ).all()
            # Filtrer celles qui n'ont probablement pas de sauts de ligne
            geocaches_to_update = [
                gc for gc in geocaches_to_update 
                if gc.description_raw and '\n' not in gc.description_raw
            ]
            logger.info(f"Trouvé {len(geocaches_to_update)} géocaches sans sauts de ligne à mettre à jour")

        updated_count = 0
        error_count = 0

        for geocache in geocaches_to_update:
            try:
                # Extraire le texte brut du HTML avec sauts de ligne
                description_raw = html_to_text_with_linebreaks(geocache.description_html)

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
    import argparse
    
    parser = argparse.ArgumentParser(description="Met à jour description_raw avec sauts de ligne")
    parser.add_argument('--force', action='store_true', 
                        help="Force la mise à jour de toutes les géocaches")
    args = parser.parse_args()
    
    print("Mise à jour du champ description_raw avec sauts de ligne...")
    print("=" * 60)

    # Créer le contexte Flask
    from app import create_app
    app = create_app()
    
    with app.app_context():
        try:
            updated, errors = update_description_raw_with_linebreaks(force_update=args.force)

            print("=" * 60)
            print(f"RÉSULTAT: {updated} géocaches mises à jour")

            if errors > 0:
                print(f"ATTENTION: {errors} erreurs rencontrées")

            print("\nLes descriptions contiennent maintenant les sauts de ligne !")
            print("Les plugins pourront mieux analyser le texte structuré.")

        except Exception as e:
            print(f"ERREUR: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
