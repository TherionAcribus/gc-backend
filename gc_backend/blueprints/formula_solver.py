"""
Blueprint Formula Solver
Routes API pour la résolution de formules de coordonnées GPS
"""

from flask import Blueprint, jsonify, request, current_app
from loguru import logger
from typing import Dict, Any

from ..database import db
from ..geocaches.models import Geocache
from ..services.formula_questions_service import formula_questions_service
from ..utils.coordinate_calculator import CoordinateCalculator

bp = Blueprint('formula_solver', __name__)


@bp.post('/api/formula-solver/detect-formulas')
def detect_formulas():
    """
    Détecte les formules de coordonnées dans une géocache ou un texte brut.
    
    Body JSON:
        {
            "geocache_id": 123,  // OU
            "text": "N 47° 5E.FTN E 006° 5A.JVF"
        }
    
    Returns:
        {
            "status": "success",
            "formulas": [
                {
                    "id": "result_1",
                    "north": "N 47° 5E.FTN",
                    "east": "E 006° 5A.JVF",
                    "text_output": "N 47° 5E.FTN E 006° 5A.JVF",
                    "confidence": 0.9
                }
            ],
            "summary": "1 formule détectée"
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        
        geocache_id = data.get('geocache_id')
        text = data.get('text')
        
        # Validation : au moins un paramètre requis
        if not geocache_id and not text:
            return jsonify({
                'status': 'error',
                'error': 'Paramètre geocache_id ou text requis'
            }), 400
        
        # Cas 1 : Détection depuis une géocache
        if geocache_id:
            geocache = Geocache.query.get(geocache_id)
            if not geocache:
                return jsonify({
                    'status': 'error',
                    'error': f'Géocache {geocache_id} introuvable'
                }), 404
            
            # Préparer le texte : description + waypoints
            text_parts = []
            
            if geocache.description:
                text_parts.append(geocache.description)
            
            if geocache.additional_waypoints:
                for wp in geocache.additional_waypoints:
                    if wp.note:
                        text_parts.append(wp.note)
            
            text = "\n\n".join(text_parts)
            
            logger.info(f"Détection de formules pour geocache {geocache.gc_code} (id={geocache_id})")
        
        # Appeler le plugin formula_parser
        plugin_manager = current_app.plugin_manager
        result = plugin_manager.execute_plugin('formula_parser', {'text': text})
        
        if result.get('status') == 'error':
            return jsonify({
                'status': 'error',
                'error': result.get('error', {}).get('message', 'Erreur inconnue')
            }), 500
        
        logger.info(f"Formules détectées : {result.get('summary')}")
        
        return jsonify({
            'status': 'success',
            'formulas': result.get('results', []),
            'summary': result.get('summary', '')
        })
    
    except Exception as e:
        logger.error(f"Erreur lors de la détection de formules : {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@bp.post('/api/formula-solver/extract-questions')
def extract_questions():
    """
    Extrait les questions associées aux variables d'une formule.
    
    Body JSON:
        {
            "geocache_id": 123,  // OU "text": "..."
            "letters": ["A", "B", "C", "D"],
            "method": "regex"  // ou "ai" (non supporté pour l'instant)
        }
    
    Returns:
        {
            "status": "success",
            "questions": {
                "A": "Nombre de fenêtres",
                "B": "Année de construction",
                "C": "",
                "D": "Numéro de la rue"
            },
            "found_count": 3
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        
        geocache_id = data.get('geocache_id')
        text = data.get('text')
        letters = data.get('letters', [])
        method = data.get('method', 'regex')
        
        # Validation
        if not geocache_id and not text:
            return jsonify({
                'status': 'error',
                'error': 'Paramètre geocache_id ou text requis'
            }), 400
        
        if not letters:
            return jsonify({
                'status': 'error',
                'error': 'Paramètre letters requis (liste de lettres)'
            }), 400
        
        if method not in ['regex', 'ai']:
            return jsonify({
                'status': 'error',
                'error': 'Paramètre method doit être "regex" ou "ai"'
            }), 400
        
        if method == 'ai':
            return jsonify({
                'status': 'error',
                'error': 'La méthode AI n\'est pas encore implémentée. Utilisez "regex".'
            }), 400
        
        # Récupérer le contenu
        if geocache_id:
            geocache = Geocache.query.get(geocache_id)
            if not geocache:
                return jsonify({
                    'status': 'error',
                    'error': f'Géocache {geocache_id} introuvable'
                }), 404
            
            content = geocache
            logger.info(f"Extraction de questions pour geocache {geocache.gc_code}, lettres: {letters}")
        else:
            content = text
            logger.info(f"Extraction de questions depuis texte, lettres: {letters}")
        
        # Extraire les questions avec la méthode choisie
        if method == 'regex':
            questions = formula_questions_service.extract_questions_with_regex(content, letters)
        
        # Compter les questions trouvées
        found_count = len([q for q in questions.values() if q])
        
        logger.info(f"Questions extraites : {found_count}/{len(letters)}")
        
        return jsonify({
            'status': 'success',
            'questions': questions,
            'found_count': found_count,
            'method': method
        })
    
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction de questions : {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@bp.post('/api/formula-solver/calculate')
def calculate_coordinates():
    """
    Calcule les coordonnées finales à partir d'une formule et des valeurs.
    
    Body JSON:
        {
            "north_formula": "N 47° 5E.FTN",
            "east_formula": "E 006° 5A.JVF",
            "values": {
                "A": 3,
                "E": 8,
                "F": 1,
                "J": 2,
                "N": 5,
                "T": 9,
                "V": 0
            },
            "origin_lat": 47.123,  // Optionnel (pour calculer la distance)
            "origin_lon": 6.456    // Optionnel
        }
    
    Returns:
        {
            "status": "success",
            "coordinates": {
                "latitude": 47.89833333,
                "longitude": 6.08333333,
                "ddm": "N 47° 53.900 E 006° 05.000",
                "dms": "N 47° 53' 54.0\" E 006° 05' 00.0\"",
                "decimal": "47.89833333, 6.08333333"
            },
            "distance": {
                "km": 123.45,
                "miles": 76.72
            },
            "calculation_steps": {
                "north_substituted": "N 47° 58.195",
                "east_substituted": "E 006° 53.120"
            }
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        
        north_formula = data.get('north_formula')
        east_formula = data.get('east_formula')
        values = data.get('values', {})
        origin_lat = data.get('origin_lat')
        origin_lon = data.get('origin_lon')
        
        # Validation
        if not north_formula or not east_formula:
            return jsonify({
                'status': 'error',
                'error': 'Paramètres north_formula et east_formula requis'
            }), 400
        
        if not values:
            return jsonify({
                'status': 'error',
                'error': 'Paramètre values requis (dictionnaire lettre -> valeur)'
            }), 400
        
        logger.info(f"Calcul de coordonnées : N={north_formula}, E={east_formula}, values={values}")
        
        # Calculer les coordonnées
        calculator = CoordinateCalculator()
        result = calculator.calculate_coordinates(north_formula, east_formula, values)
        
        if result.get('status') == 'error':
            return jsonify(result), 400
        
        # Calculer la distance si origine fournie
        if origin_lat is not None and origin_lon is not None:
            distance_km = calculator.calculate_distance(
                origin_lat, origin_lon,
                result['coordinates']['latitude'],
                result['coordinates']['longitude']
            )
            result['distance'] = {
                'km': round(distance_km, 2),
                'miles': round(distance_km * 0.621371, 2)
            }
            logger.info(f"Distance depuis origine : {distance_km:.2f} km")
        
        logger.info(f"Coordonnées calculées : {result['coordinates']['decimal']}")
        
        return jsonify(result)
    
    except ValueError as e:
        logger.warning(f"Erreur de validation lors du calcul : {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 400
    
    except Exception as e:
        logger.error(f"Erreur lors du calcul de coordonnées : {e}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500
