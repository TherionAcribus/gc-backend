from typing import Optional, Dict
import re
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from ..database import db
from ..geocaches.models import Geocache
import traceback
try:
    from pyproj import Geod
except ModuleNotFoundError:  # pragma: no cover
    Geod = None

coordinates_bp = Blueprint('coordinates', __name__)


DDM_COMPONENT_REGEX = re.compile(
    r'([NSWE])\s*(\d{1,3})\s*(?:[°º]|deg|degrees)?\s*([0-5]?\d(?:[.,]\d+)?)\s*[\'\'’′]?'
)


def _ddm_component_to_decimal(component: str) -> float:
    """Convertit une composante DDM (ex: "N 48° 39.286'") en décimal."""
    if not component:
        raise ValueError("Composante DDM vide")

    match = DDM_COMPONENT_REGEX.search(component.strip())
    if not match:
        raise ValueError(f"Format DDM invalide: {component}")

    direction, degrees_str, minutes_str = match.groups()
    degrees = int(degrees_str)
    minutes = float(minutes_str.replace(',', '.'))

    if minutes < 0 or minutes >= 60:
        raise ValueError(f"Minutes hors bornes: {minutes}")

    direction = direction.upper()
    if direction in ('N', 'S') and not 0 <= degrees <= 90:
        raise ValueError(f"Latitude hors bornes: {degrees}")
    if direction in ('E', 'W') and not 0 <= degrees <= 180:
        raise ValueError(f"Longitude hors bornes: {degrees}")

    decimal = degrees + (minutes / 60.0)
    if direction in ('S', 'W'):
        decimal = -decimal

    return round(decimal, 8)


def convert_ddm_to_decimal(lat_ddm: str, lon_ddm: str) -> Dict[str, Optional[float]]:
    """Convertit deux coordonnées DDM (latitude, longitude) en décimal."""
    latitude = None
    longitude = None

    try:
        latitude = _ddm_component_to_decimal(lat_ddm)
    except Exception as exc:
        print(f"[DEBUG] convert_ddm_to_decimal: impossible de convertir la latitude '{lat_ddm}': {exc}")

    try:
        longitude = _ddm_component_to_decimal(lon_ddm)
    except Exception as exc:
        print(f"[DEBUG] convert_ddm_to_decimal: impossible de convertir la longitude '{lon_ddm}': {exc}")

    return {
        "latitude": latitude,
        "longitude": longitude
    }

# A PRIORI PLUS UTILISé..... Ne pas l'utiliser.... A supprimer....
@coordinates_bp.route('/api/geocaches/save/<int:geocache_id>/coordinates', methods=['POST'])
def save_geocache_coordinates(geocache_id):
    """
    Sauvegarde les coordonnées corrigées d'une géocache.
    """
    print(f"[DEBUG] Sauvegarde des coordonnées pour la géocache {geocache_id}")
    try:
        data = request.get_json()
        
        geocache = Geocache.query.get_or_404(geocache_id)
        
        # Mise à jour des coordonnées
        if 'gc_lat_corrected' in data and 'gc_lon_corrected' in data:
            geocache.gc_lat_corrected = data['gc_lat_corrected']
            geocache.gc_lon_corrected = data['gc_lon_corrected']
        
        # Mise à jour du statut solved et de la date
        if 'solved' in data:
            geocache.solved = data['solved']
            geocache.solved_date = datetime.now(timezone.utc)
        
        db.session.commit()
        
        return jsonify({
            'message': 'Coordonnées mises à jour avec succès',
            'gc_lat_corrected': geocache.gc_lat_corrected,
            'gc_lon_corrected': geocache.gc_lon_corrected,
            'solved': geocache.solved,
            'solved_date': geocache.solved_date.isoformat() if geocache.solved_date else None
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@coordinates_bp.route('/api/calculate_coordinates', methods=['POST'])
def calculate_coordinates():
    """
    Calcule les coordonnées à partir d'une formule.
    Peut calculer partiellement les coordonnées même si toutes les variables ne sont pas définies.
    
    Vérifie également que les coordonnées sont au format standard de géocaching:
    - Exactement 3 chiffres après le point pour les décimales des minutes
    - Si plus de 3 chiffres, erreur indiquée (valeurs incorrectes)
    - Si moins de 3 chiffres, complète avec des zéros (ex: 94 devient 094)
    
    Exemple de données attendues:
    {
        "formula": "N48° 39.(8/4)(27/9)(2x2x2) E06°11.(3x2)(16x2/4)(25/5)",
        "variables": {"A": 5, "B": 10, ...},  # optionnel
        "origin_lat": "N48° 40.123",  # optionnel
        "origin_lon": "E06° 10.456"   # optionnel
    }
    
    Retourne:
    {
        "coordinates": "N48° 39.286 E06°11.685",
        "latitude": "N48° 39.286",
        "longitude": "E06°11.685",
        "status": "complete" | "partial" | "error",
        "lat_status": {
            "status": "complete" | "partial" | "error",
            "message": "Message d'erreur détaillé si applicable"
        },
        "lon_status": {
            "status": "complete" | "partial" | "error",
            "message": "Message d'erreur détaillé si applicable"
        },
        "distance_from_origin": {
            "meters": 1234.56,
            "miles": 0.76,
            "status": "ok" | "warning" | "far"
        },  # optionnel, présent uniquement si origin_lat et origin_lon sont fournis
        "decimal_latitude": 48.586,
        "decimal_longitude": 6.195
    }
    """
    try:
        data = request.get_json()
        formula = data.get('formula', '')
        variables = data.get('variables', {})
        origin_lat = data.get('origin_lat')
        origin_lon = data.get('origin_lon')
        
        if not formula:
            return jsonify({"error": "Aucune formule fournie"}), 400
        
        print(f"[DEBUG] Calcul des coordonnées pour la formule: {formula}")
        print(f"[DEBUG] Variables fournies: {variables}")
        
        # Extraire les parties de latitude et longitude (accepte lettres et chiffres)
        lat_match = re.search(r'([NS])\s*([A-Z0-9]+)°\s*([A-Z0-9]+)\.([A-Z0-9()x*/+-]+)', formula)
        lon_match = re.search(r'([EW])\s*([A-Z0-9]+)°\s*([A-Z0-9]+)\.([A-Z0-9()x*/+-]+)', formula)
        
        if not lat_match or not lon_match:
            print(f"[ERROR] Format de formule invalide: lat_match={lat_match}, lon_match={lon_match}")
            return jsonify({"error": "Format de formule invalide"}), 400
        
        # Substitution et calcul pour chaque partie
        lat_dir, lat_deg_raw, lat_min_raw, lat_decimal_raw = lat_match.groups()
        lon_dir, lon_deg_raw, lon_min_raw, lon_decimal_raw = lon_match.groups()
        
        print(f"[DEBUG] Parties de latitude: dir={lat_dir}, deg={lat_deg_raw}, min={lat_min_raw}, decimal={lat_decimal_raw}")
        print(f"[DEBUG] Parties de longitude: dir={lon_dir}, deg={lon_deg_raw}, min={lon_min_raw}, decimal={lon_decimal_raw}")
        
        # Appliquer la substitution sur chaque partie
        lat_deg = _process_formula_part(lat_deg_raw, variables)
        lat_min = _process_formula_part(lat_min_raw, variables)
        lat_decimal = _process_formula_part(lat_decimal_raw, variables)
        lon_deg = _process_formula_part(lon_deg_raw, variables)
        lon_min = _process_formula_part(lon_min_raw, variables)
        lon_decimal = _process_formula_part(lon_decimal_raw, variables)
        
        print(f"[DEBUG] Valeurs calculées: lat_deg={lat_deg}, lat_min={lat_min}, lat_decimal={lat_decimal}")
        print(f"[DEBUG] Valeurs calculées: lon_deg={lon_deg}, lon_min={lon_min}, lon_decimal={lon_decimal}")
        
        # Statuts individuels
        def part_status(val, label):
            # Vérifie si la valeur contient encore des lettres (résolution partielle)
            if isinstance(val, str) and re.search(r'[A-Z]', val):
                # Vérifier si c'est une expression avec des parenthèses qui n'a pas pu être évaluée
                if '(' in val and ')' in val:
                    return ("error", f"La partie {label} contient une expression mathématique non évaluable: {val}")
                return ("partial", f"La partie {label} contient encore des lettres")
            
            # Vérifie si la valeur est une expression entre parenthèses (erreur de calcul)
            if isinstance(val, str) and val.startswith('(') and val.endswith(')'):
                # C'est une expression qui n'a pas pu être calculée comme un entier
                return ("error", f"L'expression {val} dans {label} ne donne pas un nombre entier. Vérifiez vos valeurs.")
            
            # Vérifie si la valeur est négative
            if isinstance(val, (int, float)):
                if int(val) < 0:
                    return ("error", f"La partie {label} est négative")
                
                # Vérification spécifique pour la partie décimale des minutes (format de géocaching)
                if "décimales" in label:
                    # Convertir en chaîne pour vérifier le nombre de chiffres
                    val_str = str(val)
                    
                    # Si on a plus de 3 chiffres, c'est une erreur en géocaching
                    if len(val_str) > 3:
                        return ("error", f"La partie {label} a plus de 3 chiffres ({val_str}). Ceci indique une erreur dans vos valeurs, car en géocaching, il faut exactement 3 chiffres après le point.")
            
            return ("complete", "")
        
        lat_deg_status, lat_deg_msg = part_status(lat_deg, "degrés latitude")
        lat_min_status, lat_min_msg = part_status(lat_min, "minutes latitude")
        lat_decimal_status, lat_decimal_msg = part_status(lat_decimal, "décimales latitude")
        lon_deg_status, lon_deg_msg = part_status(lon_deg, "degrés longitude")
        lon_min_status, lon_min_msg = part_status(lon_min, "minutes longitude")
        lon_decimal_status, lon_decimal_msg = part_status(lon_decimal, "décimales longitude")
        
        # Statut global pour chaque coordonnée
        lat_status = {"status": "complete", "message": ""}
        lon_status = {"status": "complete", "message": ""}
        
        for st, msg in [(lat_deg_status, lat_deg_msg), (lat_min_status, lat_min_msg), (lat_decimal_status, lat_decimal_msg)]:
            if st == "error":
                lat_status = {"status": "error", "message": msg}
                break
            elif st == "partial" and lat_status["status"] != "error":
                lat_status = {"status": "partial", "message": msg}
        for st, msg in [(lon_deg_status, lon_deg_msg), (lon_min_status, lon_min_msg), (lon_decimal_status, lon_decimal_msg)]:
            if st == "error":
                lon_status = {"status": "error", "message": msg}
                break
            elif st == "partial" and lon_status["status"] != "error":
                lon_status = {"status": "partial", "message": msg}
        
        # Statut global
        global_status = "complete"
        if lat_status["status"] != "complete" or lon_status["status"] != "complete":
            if lat_status["status"] == "error" or lon_status["status"] == "error":
                global_status = "error"
            else:
                global_status = "partial"
        
        # Formatage final
        def format_part(val, digits):
            if isinstance(val, (int, float)):
                # Convertir en chaîne
                val_str = str(val)
                
                # Pour les décimales (quand digits=3), on s'assure d'avoir au moins 3 chiffres
                # mais on ne tronque pas s'il y en a plus (pour afficher l'erreur)
                if digits == 3:
                    # Pour des valeurs avec moins de 3 chiffres, on complète avec des zéros
                    if len(val_str) < 3:
                        val_str = val_str.zfill(digits)
                    # Si plus de 3 chiffres, on laisse tel quel pour montrer l'erreur
                else:
                    # Pour les autres parties (degrés, minutes), on utilise simplement zfill
                    val_str = val_str.zfill(digits)
                
                return val_str
            
            # Si ce n'est pas un nombre, le retourner tel quel
            return str(val)
        
        lat_formatted = f"{lat_dir}{format_part(lat_deg,2)}° {format_part(lat_min,2)}.{format_part(lat_decimal,3)}"
        lon_formatted = f"{lon_dir}{format_part(lon_deg,3)}° {format_part(lon_min,2)}.{format_part(lon_decimal,3)}"
        
        print(f"[DEBUG] Coordonnées formatées: {lat_formatted} {lon_formatted}")
        
        result = {
            "coordinates": f"{lat_formatted} {lon_formatted}",
            "latitude": lat_formatted,
            "longitude": lon_formatted,
            "status": global_status,
            "lat_status": lat_status,
            "lon_status": lon_status
        }
        
        # Ajouter les coordonnées décimales pour l'affichage automatique sur la carte
        if global_status == "complete":
            try:
                decimal_coords = convert_ddm_to_decimal(lat_formatted, lon_formatted)
                result["decimal_latitude"] = decimal_coords["latitude"]
                result["decimal_longitude"] = decimal_coords["longitude"]
                print(f"[DEBUG] Coordonnées décimales calculées: lat={decimal_coords['latitude']}, lon={decimal_coords['longitude']}")
            except Exception as e:
                print(f"[WARNING] Erreur lors de la conversion en coordonnées décimales: {str(e)}")
                # Ne pas faire échouer l'appel si la conversion échoue
        
        # Si nous avons des coordonnées d'origine et que les coordonnées calculées sont complètes, 
        # calculer la distance entre les deux
        print("[DEBUG] Vérification des conditions pour le calcul de distance:")
        print(f"[DEBUG] - origin_lat: {origin_lat} ({type(origin_lat).__name__ if origin_lat else 'None'})")
        print(f"[DEBUG] - origin_lon: {origin_lon} ({type(origin_lon).__name__ if origin_lon else 'None'})")
        print(f"[DEBUG] - global_status: {global_status}")
        print(f"[DEBUG] - lat_decimal_value: {lat_decimal} ({type(lat_decimal).__name__})")
        print(f"[DEBUG] - lon_decimal_value: {lon_decimal} ({type(lon_decimal).__name__})")
        
        if origin_lat and origin_lon and global_status == "complete" and isinstance(lat_decimal, (int, float)) and isinstance(lon_decimal, (int, float)):
            try:
                print("[DEBUG] Conditions remplies pour calculer la distance entre les coordonnées")
                print(f"[DEBUG] Origin lat: {origin_lat}, Origin lon: {origin_lon}")
                print(f"[DEBUG] Destination lat: {lat_formatted}, Destination lon: {lon_formatted}")
                distance_info = calculate_distance_between_coords(
                    origin_lat=origin_lat,
                    origin_lon=origin_lon,
                    dest_lat=lat_formatted,
                    dest_lon=lon_formatted
                )
                result["distance_from_origin"] = distance_info
                print(f"[DEBUG] Distance calculée: {distance_info}")
            except Exception as e:
                print(f"[ERROR] Erreur lors du calcul de la distance: {str(e)}")
                traceback.print_exc()
        else:
            print(f"[DEBUG] Calcul de distance impossible: origin_lat={origin_lat}, origin_lon={origin_lon}, global_status={global_status}")
            print(f"[DEBUG] lat_decimal_value est de type {type(lat_decimal).__name__}: {lat_decimal}")
            print(f"[DEBUG] lon_decimal_value est de type {type(lon_decimal).__name__}: {lon_decimal}")
        
        print(f"[DEBUG] Résultat final: {result}")
        return jsonify(result)
        
    except Exception as e:
        print(f"[ERROR] Erreur lors du calcul des coordonnées: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def calculate_distance_between_coords(origin_lat, origin_lon, dest_lat, dest_lon):
    """
    Calcule la distance entre deux coordonnées au format DDM
    
    Args:
        origin_lat (str): Latitude d'origine au format DDM (ex: "N48° 40.123")
        origin_lon (str): Longitude d'origine au format DDM (ex: "E06° 10.456")
        dest_lat (str): Latitude de destination au format DDM (ex: "N48° 39.286")
        dest_lon (str): Longitude de destination au format DDM (ex: "E06°11.685")
        
    Returns:
        dict: Dictionnaire contenant la distance en mètres, en miles et un statut
    """
    print("[DEBUG] *** DÉBUT CALCUL DISTANCE ***")
    print(f"[DEBUG] Origine: {origin_lat} {origin_lon}")
    print(f"[DEBUG] Destination: {dest_lat} {dest_lon}")
    
    # Convertir les coordonnées DDM en format décimal
    origin_coords = convert_ddm_to_decimal(origin_lat, origin_lon)
    dest_coords = convert_ddm_to_decimal(dest_lat, dest_lon)
    
    print(f"[DEBUG] Origine (décimal): lat={origin_coords['latitude']}, lon={origin_coords['longitude']}")
    print(f"[DEBUG] Destination (décimal): lat={dest_coords['latitude']}, lon={dest_coords['longitude']}")
    
    if not origin_coords['latitude'] or not origin_coords['longitude'] or not dest_coords['latitude'] or not dest_coords['longitude']:
        print("[ERROR] Conversion des coordonnées en décimal impossible")
        raise ValueError("Impossible de convertir les coordonnées en format décimal")
    
    # Calculer la distance avec Geod
    if Geod is None:
        raise RuntimeError(
            "Le module 'pyproj' n'est pas installé. "
            "Installez-le pour activer le calcul de distance (pip install pyproj)."
        )
    geod = Geod(ellps="WGS84")
    _, _, distance_m = geod.inv(
        origin_coords['longitude'], origin_coords['latitude'],
        dest_coords['longitude'], dest_coords['latitude']
    )
    
    # Convertir en miles (1 mile = 1609.344 mètres)
    distance_miles = distance_m / 1609.344
    
    print(f"[DEBUG] Distance calculée: {distance_m} mètres ({distance_miles} miles)")
    
    # Déterminer le statut en fonction de la distance
    # Moins de 2 miles = ok
    # Entre 2 et 2.5 miles = warning
    # Plus de 2.5 miles = far
    status = "ok"
    if distance_miles > 2.5:
        status = "far"
        print("[DEBUG] Statut: FAR - Distance > 2.5 miles")
    elif distance_miles > 2.0:
        status = "warning"
        print("[DEBUG] Statut: WARNING - Distance entre 2.0 et 2.5 miles")
    else:
        print("[DEBUG] Statut: OK - Distance < 2.0 miles")
    
    print("[DEBUG] *** FIN CALCUL DISTANCE ***")
    
    return {
        "meters": round(distance_m, 2),
        "miles": round(distance_miles, 2),
        "status": status
    }

def _process_formula_part(formula_part, variables):
    """
    Traite une partie de formule (généralement la partie décimale des minutes)
    et remplace les variables par leurs valeurs si disponibles.
    
    Args:
        formula_part: Partie de la formule à traiter (peut être une expression entre parenthèses)
        variables: Dictionnaire des variables avec leurs valeurs
        
    Returns:
        Le résultat calculé ou l'expression partiellement résolue
    """
    print(f"[DEBUG] _process_formula_part: Traitement de '{formula_part}' avec variables {variables}")
    
    # Si ce n'est pas une expression entre parenthèses initiale, mais peut contenir des sous-expressions
    if not formula_part.startswith('('):
        # Si c'est juste un chiffre, le retourner directement
        if formula_part.isdigit():
            return int(formula_part)
        
        # Si la formule contient des lettres majuscules, la traiter comme une expression
        if re.search(r'[A-Z]', formula_part):
            print(f"[DEBUG] Détection de lettres dans '{formula_part}', traitement comme expression")
            # Remplacer les lettres par leurs valeurs si disponibles
            expression = formula_part
            has_letters_with_values = False
            
            for var, value in variables.items():
                if var in expression:
                    has_letters_with_values = True
                    expression = expression.replace(var, str(value))
            
            # Si aucune lettre n'a été remplacée, retourner l'expression telle quelle
            if not has_letters_with_values:
                print(f"[DEBUG] Aucune variable disponible pour '{formula_part}', retour tel quel")
                return formula_part
            
            # Si l'expression contient des parenthèses après le remplacement, essayer de résoudre les sous-expressions
            if '(' in expression and ')' in expression:
                # Chercher toutes les sous-expressions entre parenthèses simples
                sub_expr_pattern = r'\(([^()]+)\)'
                while re.search(sub_expr_pattern, expression):
                    # Évaluer la sous-expression
                    expression = re.sub(sub_expr_pattern, lambda m: str(_evaluate_math_expression(m.group(1).replace('x', '*'))), expression)
                    
                    # Si l'expression contient un marqueur d'erreur, arrêter le traitement
                    if 'ERR:' in expression:
                        # Extraire et retourner l'expression originale avec parenthèses
                        parts = expression.split(':')
                        if len(parts) >= 3:
                            return f"({parts[2]})"
                
                # Si après résolution des sous-expressions il ne reste plus de lettres, évaluer l'expression complète
                if not re.search(r'[A-Z]', expression):
                    result = _evaluate_math_expression(expression)
                    # Vérifier si le résultat contient un marqueur d'erreur
                    if isinstance(result, str) and result.startswith('ERR:'):
                        # Extraire et retourner l'expression originale avec parenthèses
                        parts = result.split(':')
                        if len(parts) >= 3:
                            return f"({parts[2]})"
                    print(f"[DEBUG] Résultat final: {result}")
                    return result
        
        # Sinon, retourner tel quel
        return formula_part
    
    # Si la partie commence et se termine par des parenthèses, vérifier si c'est une concaténation de sous-expressions
    # Exemple: (A+B)(C-D)(E/2)
    if re.fullmatch(r'(\([^()]+\))+', formula_part):
        sub_expressions = re.findall(r'\(([^()]+)\)', formula_part)
        evaluated_parts = []
        has_error = False
        rebuilt_on_error = []
        for sub in sub_expressions:
            sub_expr = sub.replace('x', '*')
            # Remplacer les variables
            for var, value in variables.items():
                if var in sub_expr:
                    sub_expr = sub_expr.replace(var, str(value))
            # Si des lettres restent, on ne peut pas évaluer cette sous-expression
            if re.search(r'[A-DF-Z]', sub_expr):
                has_error = True
                rebuilt_on_error.append(f'({sub_expr})')
                continue
            # Évaluer la sous-expression
            sub_result = _evaluate_math_expression(sub_expr)
            if isinstance(sub_result, str) and sub_result.startswith('ERR:'):
                has_error = True
                # Conserver la sous-expression fautive telle quelle avec parenthèses
                rebuilt_on_error.append(f'({sub_expr})')
            else:
                evaluated_parts.append(str(sub_result))
                rebuilt_on_error.append(str(sub_result))
        # Si une erreur ou une lettre non résolue existe, retourner l'expression reconstruite entre parenthèses
        if has_error:
            return '(' + ''.join(rebuilt_on_error) + ')'
        # Sinon, concaténer les résultats numériques et retourner un entier pour permettre le formatage (zfill)
        concatenated = ''.join(evaluated_parts) if evaluated_parts else ''
        try:
            return int(concatenated) if concatenated != '' else 0
        except ValueError:
            # Fallback prudente
            return '(' + ''.join(rebuilt_on_error) + ')'

    # Cas standard: une seule parenthèse englobante, traiter comme expression unique
    # Extraire l'expression entre parenthèses
    expression = formula_part[1:-1] if formula_part.endswith(')') else formula_part[1:]
    
    # Remplacer les multiplications 'x' par '*' pour l'évaluation
    expression = expression.replace('x', '*')
    
    # Remplacer les variables par leurs valeurs
    for var, value in variables.items():
        if var in expression:
            expression = expression.replace(var, str(value))
    
    # Vérifier si toutes les variables ont été remplacées
    # Si l'expression contient encore des lettres majuscules (sauf E pour notation scientifique)
    if re.search(r'[A-DF-Z]', expression):
        # Retourner l'expression partiellement résolue
        return f"({expression})"
    
    # Évaluer l'expression mathématique
    result = _evaluate_math_expression(expression)
    # Vérifier si le résultat contient un marqueur d'erreur
    if isinstance(result, str) and result.startswith('ERR:'):
        # Extraire et retourner l'expression originale avec parenthèses
        parts = result.split(':')
        if len(parts) >= 3:
            return f"({parts[2]})"
    return result

def _evaluate_math_expression(expression):
    """
    Évalue une expression mathématique et vérifie que le résultat est un entier.
    
    Args:
        expression: L'expression mathématique à évaluer (sans lettres, uniquement des chiffres et opérateurs)
        
    Returns:
        Le résultat arrondi si c'est un nombre entier ou proche d'un entier
        Sinon, retourne l'expression originale entre parenthèses avec un marqueur d'erreur
    """
    print(f"[DEBUG] _evaluate_math_expression: Évaluation de '{expression}'")
    
    try:
        # Évaluer l'expression
        result = eval(expression)
        print(f"[DEBUG] _evaluate_math_expression: Résultat brut = {result}")
        
        # Vérifier si le résultat est un nombre
        if isinstance(result, (int, float)):
            # Vérifier si c'est un entier ou très proche d'un entier (tolérance pour les erreurs de virgule flottante)
            if abs(result - round(result)) < 1e-10:
                return round(result)
            else:
                # Si c'est une valeur décimale significative, signaler l'erreur mais ne pas lever d'exception
                error_msg = f"L'expression '{expression}' donne un résultat non entier: {result}"
                print(f"[ERROR] {error_msg}")
                # Retourner l'expression originale avec un marqueur spécial pour la gestion des erreurs
                return f"ERR:NONINTEGER:{expression}"
    except Exception as e:
        print(f"[ERROR] Impossible d'évaluer l'expression '{expression}': {str(e)}")
        # Retourner l'expression originale avec un marqueur d'erreur
        return f"ERR:SYNTAX:{expression}"
    
    return result

# ------------------------------------------------------------------------------
# Configuration : listes/mappings pour les directions (Nord, Est, ...)
# ------------------------------------------------------------------------------

NORTH_VARIANTS = ["NORD", "Nord", "nord", "NORTH", "North", "north", "N"]
EAST_VARIANTS  = ["EST", "Est", "est", "EAST", "East", "east", "E"]

DIRECTION_MAP = {
    "NORD": "N", "Nord": "N", "nord": "N",
    "NORTH": "N", "North": "N", "north": "N",
    "N": "N",
    "EST": "E", "Est": "E", "est": "E",
    "EAST": "E", "East": "E", "east": "E",
    "E": "E"
}

# ------------------------------------------------------------------------------
# Utilitaires de validation des composantes DDM
# ------------------------------------------------------------------------------

def _is_valid_degrees_minutes(lat_deg: int, lat_min: float, lon_deg: int, lon_min: float) -> bool:
    """
    Valide les bornes des degrés et minutes pour une coordonnée DDM.
    - Latitude degrés: 0 à 90
    - Longitude degrés: 0 à 180
    - Minutes: 0 à < 60
    """
    try:
        if lat_deg < 0 or lat_deg > 90:
            return False
        if lon_deg < 0 or lon_deg > 180:
            return False
        if lat_min < 0 or lat_min >= 60:
            return False
        if lon_min < 0 or lon_min >= 60:
            return False
        return True
    except Exception:
        return False

# ------------------------------------------------------------------------------
# Fonction utilitaire pour formater une chaîne de chiffres en DDM
# ------------------------------------------------------------------------------

def _format_coordinate(coord_str: str, expected_deg_digits: int) -> str:
    """
    Transforme une chaîne de chiffres en une chaîne formatée en DDM.
    
    Exemple :
      - Pour la latitude "4833787" (expected_deg_digits=2), cela donne "48° 33.787'".
      - Pour la longitude "00638803" (expected_deg_digits=3), cela donne "006° 38.803'".
    
    :param coord_str: La chaîne de chiffres (sans séparateur).
    :param expected_deg_digits: Nombre de chiffres pour la partie degrés (2 pour la latitude, 3 pour la longitude).
    :return: La coordonnée formatée en DDM.
    :raises ValueError: Si la chaîne est trop courte.
    """
    print(f"[DEBUG] _format_coordinate: Formatage de '{coord_str}' avec {expected_deg_digits} chiffres pour les degrés")
    
    if len(coord_str) < expected_deg_digits + 2:
        print(f"[DEBUG] _format_coordinate: Erreur - Chaîne trop courte ({len(coord_str)} < {expected_deg_digits + 2})")
        raise ValueError("Chaîne de coordonnées trop courte pour le format attendu.")
    
    # Les degrés sont les premiers chiffres
    deg = coord_str[:expected_deg_digits]
    # Les deux chiffres suivants correspondent à la partie entière des minutes
    minutes_int = coord_str[expected_deg_digits:expected_deg_digits+2]
    # Le reste (s'il existe) correspond à la partie décimale des minutes
    decimal_part = coord_str[expected_deg_digits+2:]
    
    print(f"[DEBUG] _format_coordinate: Décomposition - degrés={deg}, minutes_int={minutes_int}, decimal_part={decimal_part}")
    
    if decimal_part:
        minutes = f"{minutes_int}.{decimal_part}"
    else:
        minutes = minutes_int
        
    result = f"{deg}° {minutes}'"
    print(f"[DEBUG] _format_coordinate: Résultat formaté: {result}")
    return result

# ------------------------------------------------------------------------------
# Détection du format DMM classique (par exemple "N 48° 33.787' E 006° 38.803'")
# ------------------------------------------------------------------------------

def _detect_dmm_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    print(f"[DEBUG] _detect_dmm_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    dmm_regex = (
        r'([NS])\s*(\d{1,2})\s*[°º]\s*(\d{1,2}(?:\.\d+)?)[\'"]?\s*'
        r'([EW])\s*(\d{1,3})\s*[°º]\s*(\d{1,2}(?:\.\d+)?)[\'"]?'
    )
    print(f"[DEBUG] _detect_dmm_coordinates: Regex utilisée: {dmm_regex}")
    match = re.search(dmm_regex, text)
    if match:
        print(f"[DEBUG] _detect_dmm_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lon_dir, lon_deg, lon_min = match.groups()
        # Normaliser minutes en float pour validation bornes
        try:
            lat_min_f = float(lat_min)
            lon_min_f = float(lon_min)
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_dmm_coordinates: Bornes invalides, rejet")
                return None
        except Exception:
            return None
        ddm_lat = f"{lat_dir} {lat_deg}° {lat_min}'"
        ddm_lon = f"{lon_dir} {lon_deg}° {lon_min}'"
        print(f"[DEBUG] _detect_dmm_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    print("[DEBUG] _detect_dmm_coordinates: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format avec tabulations et espaces (ex: "N 48 ° 32 . 296 E 6 ° 40 . 636")
# ------------------------------------------------------------------------------

def _detect_tabspace_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format avec tabulations et espaces, par exemple :
      - "N    48 ° 32 .  296\nE    6  ° 40 .  636"
      - "N\t48 ° 32 . 296\r\nE\t6 ° 40 . 636"
    
    Ce format est plus souple et accepte des variations dans les espaces et la ponctuation.
    """
    print(f"[DEBUG] _detect_tabspace_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Pattern plus souple qui accepte des variations dans les espaces et la ponctuation
    tabspace_regex = (
        r'([NS])\s*(\d{1,2})\s*°\s*(\d{1,2})\s*[.,]\s*(\d{1,3})\s*'
        r'([EW])\s*(\d{1,3})\s*°\s*(\d{1,2})\s*[.,]\s*(\d{1,3})'
    )
    
    print(f"[DEBUG] _detect_tabspace_coordinates: Regex utilisée: {tabspace_regex}")
    
    match = re.search(tabspace_regex, text, re.DOTALL)
    if match:
        print(f"[DEBUG] _detect_tabspace_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lat_sec, lon_dir, lon_deg, lon_min, lon_sec = match.groups()
        
        # Formatage des coordonnées
        try:
            lat_min_f = float(f"{lat_min}.{lat_sec}")
            lon_min_f = float(f"{lon_min}.{lon_sec}")
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_tabspace_coordinates: Bornes invalides, rejet")
                return None
        except Exception:
            return None

        ddm_lat = f"{lat_dir} {lat_deg}° {lat_min}.{lat_sec}'"
        ddm_lon = f"{lon_dir} {lon_deg}° {lon_min}.{lon_sec}'"
        
        print(f"[DEBUG] _detect_tabspace_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_tabspace_coordinates: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format variant (ex : "NORD 4833787 EST 638803")
# ------------------------------------------------------------------------------

def _detect_variant_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format variant, par exemple :
      - "NORD 4833787 EST 638803"
      - "NORD\n4833787\nEST\n638803"
      - "Nord / 4833787 / Est / 638803"
    
    On se base sur les listes NORTH_VARIANTS et EAST_VARIANTS.
    Pour la latitude, on attend une chaîne de 7 chiffres, et pour la longitude une chaîne de 6 à 8 chiffres.
    Si la longitude comporte moins de 8 chiffres, on la complète avec des zéros à gauche.
    """
    print(f"[DEBUG] _detect_variant_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Construction des patterns pour les directions
    north_pattern = '|'.join(NORTH_VARIANTS)
    east_pattern  = '|'.join(EAST_VARIANTS)
    
    # Le pattern autorise espaces, retours à la ligne ou "/" comme séparateurs
    pattern = rf"(?P<lat_dir>{north_pattern})\s*[/\n\s]*\s*(?P<lat>\d{{7}})\s*(?P<lon_dir>{east_pattern})\s*[/\n\s]*\s*(?P<lon>\d{{6,8}})"
    
    print(f"[DEBUG] _detect_variant_coordinates: Pattern utilisé: {pattern}")
    
    match = re.search(pattern, text)
    if match:
        print(f"[DEBUG] _detect_variant_coordinates: Match trouvé! Groupes: {match.groupdict()}")
        lat_dir_raw = match.group("lat_dir")
        lon_dir_raw = match.group("lon_dir")
        lat_digits  = match.group("lat")
        lon_digits  = match.group("lon")
        
        print(f"[DEBUG] _detect_variant_coordinates: Directions brutes: lat={lat_dir_raw}, lon={lon_dir_raw}")
        print(f"[DEBUG] _detect_variant_coordinates: Digits: lat={lat_digits}, lon={lon_digits}")
        
        # Normalisation des directions pour obtenir "N" et "E"
        lat_dir = DIRECTION_MAP.get(lat_dir_raw, lat_dir_raw[0].upper())
        lon_dir = DIRECTION_MAP.get(lon_dir_raw, lon_dir_raw[0].upper())
        
        print(f"[DEBUG] _detect_variant_coordinates: Directions normalisées: lat={lat_dir}, lon={lon_dir}")
        
        try:
            # Pour la latitude, si nécessaire, compléter à 7 chiffres
            if len(lat_digits) < 7:
                lat_digits = lat_digits.zfill(7)
            ddm_lat = f"{lat_dir} " + _format_coordinate(lat_digits, expected_deg_digits=2)
            print(f"[DEBUG] _detect_variant_coordinates: Latitude formatée: {ddm_lat}")
        except ValueError as e:
            print(f"[DEBUG] _detect_variant_coordinates: Erreur lors du formatage de la latitude: {e}")
            ddm_lat = None
        
        try:
            # Pour la longitude, compléter à 8 chiffres si nécessaire
            if len(lon_digits) < 8:
                lon_digits = lon_digits.zfill(8)
            ddm_lon = f"{lon_dir} " + _format_coordinate(lon_digits, expected_deg_digits=3)
            print(f"[DEBUG] _detect_variant_coordinates: Longitude formatée: {ddm_lon}")
        except ValueError as e:
            print(f"[DEBUG] _detect_variant_coordinates: Erreur lors du formatage de la longitude: {e}")
            ddm_lon = None
        
        if ddm_lat and ddm_lon:
            print(f"[DEBUG] _detect_variant_coordinates: Coordonnées complètes détectées: {ddm_lat} {ddm_lon}")
            return {
                "exist": True,
                "ddm_lat": ddm_lat,
                "ddm_lon": ddm_lon,
                "ddm": f"{ddm_lat} {ddm_lon}"
            }
    print("[DEBUG] _detect_variant_coordinates: Aucun match trouvé ou coordonnées incomplètes")
    return None

# ------------------------------------------------------------------------------
# Détection du format spécifique avec tabulations et points (ex: "N\t48 ° 32 . 296\nE\t6 ° 40 . 636")
# ------------------------------------------------------------------------------

def _detect_specific_tabpoint_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format très spécifique avec tabulations et points, comme dans l'exemple :
      - "N\t48 ° 32 .  296\r\nE\t6  ° 40 .  636"
    
    Cette fonction est optimisée pour ce format particulier.
    """
    print(f"[DEBUG] _detect_specific_tabpoint_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Essayons d'abord avec un pattern très spécifique
    specific_regex = r'N\s*(\d{1,2})\s*°\s*(\d{1,2})\s*\.\s*(\d{1,3}).*?E\s*(\d{1,3})\s*°\s*(\d{1,2})\s*\.\s*(\d{1,3})'
    
    print(f"[DEBUG] _detect_specific_tabpoint_coordinates: Regex utilisée: {specific_regex}")
    
    match = re.search(specific_regex, text, re.DOTALL)
    if match:
        print(f"[DEBUG] _detect_specific_tabpoint_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_deg, lat_min, lat_sec, lon_deg, lon_min, lon_sec = match.groups()
        
        # Formatage des coordonnées
        ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
        ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
        
        print(f"[DEBUG] _detect_specific_tabpoint_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_specific_tabpoint_coordinates: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection simplifiée pour le format exact de l'exemple
# ------------------------------------------------------------------------------

def _detect_simplified_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détection simplifiée pour le format exact de l'exemple.
    Cette fonction utilise une approche plus directe pour détecter les coordonnées.
    """
    print(f"[DEBUG] _detect_simplified_coordinates: Analyse du texte: '{text}'")
    
    # Extraction des lignes
    lines = text.strip().split('\n')
    print(f"[DEBUG] _detect_simplified_coordinates: Lignes extraites: {lines}")
    
    if len(lines) >= 2:
        # Vérifier si la première ligne commence par N et la deuxième par E
        lat_line = lines[0].strip()
        lon_line = lines[1].strip()
        
        print(f"[DEBUG] _detect_simplified_coordinates: Ligne latitude: '{lat_line}'")
        print(f"[DEBUG] _detect_simplified_coordinates: Ligne longitude: '{lon_line}'")
        
        if lat_line.startswith('N') and lon_line.startswith('E'):
            # Extraction des nombres de la latitude
            lat_parts = re.findall(r'\d+', lat_line)
            lon_parts = re.findall(r'\d+', lon_line)
            
            print(f"[DEBUG] _detect_simplified_coordinates: Parties latitude: {lat_parts}")
            print(f"[DEBUG] _detect_simplified_coordinates: Parties longitude: {lon_parts}")
            
            if len(lat_parts) >= 3 and len(lon_parts) >= 3:
                lat_deg, lat_min, lat_sec = lat_parts[0], lat_parts[1], lat_parts[2]
                lon_deg, lon_min, lon_sec = lon_parts[0], lon_parts[1], lon_parts[2]
                
                # Formatage des coordonnées
                ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
                ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
                
                print(f"[DEBUG] _detect_simplified_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
                
                return {
                    "exist": True,
                    "ddm_lat": ddm_lat,
                    "ddm_lon": ddm_lon,
                    "ddm": f"{ddm_lat} {ddm_lon}"
                }
    
    print("[DEBUG] _detect_simplified_coordinates: Aucune coordonnée détectée")
    return None

# ------------------------------------------------------------------------------
# Détection ultra-flexible pour tout format avec N/E et degrés
# ------------------------------------------------------------------------------

def _detect_flexible_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détection ultra-flexible pour tout format contenant N/E et des degrés.
    Cette fonction est conçue pour être très tolérante aux variations de format.
    """
    print(f"[DEBUG] _detect_flexible_coordinates: Analyse du texte: '{text}'")
    
    # Recherche de N suivi de nombres et de degrés
    lat_match = re.search(r'N\s*(\d{1,2})(?:\s*[°º]|\s+deg|\s+degrees)\s*(\d{1,2})(?:[.,]\s*|\s+)(\d{1,3})', text, re.IGNORECASE)
    # Recherche de E suivi de nombres et de degrés
    lon_match = re.search(r'E\s*(\d{1,3})(?:\s*[°º]|\s+deg|\s+degrees)\s*(\d{1,2})(?:[.,]\s*|\s+)(\d{1,3})', text, re.IGNORECASE)
    
    print(f"[DEBUG] _detect_flexible_coordinates: Match latitude: {lat_match.groups() if lat_match else None}")
    print(f"[DEBUG] _detect_flexible_coordinates: Match longitude: {lon_match.groups() if lon_match else None}")
    
    if lat_match and lon_match:
        lat_deg, lat_min, lat_sec = lat_match.groups()
        lon_deg, lon_min, lon_sec = lon_match.groups()
        
        # Formatage des coordonnées
        ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
        ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
        
        print(f"[DEBUG] _detect_flexible_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_flexible_coordinates: Aucune coordonnée détectée")
    return None

# ------------------------------------------------------------------------------
# Détection du format NORD/EST avec chiffres séparés par des espaces
# ------------------------------------------------------------------------------

def _detect_nord_est_format(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format NORD/EST avec chiffres séparés par des espaces, par exemple :
      - "NORD 48 32 296 EST 6 40 636"
      - "Nord 48 32 296 Est 6 40 636"
    
    Ce format est spécifique aux coordonnées normalisées après décodage.
    """
    print(f"[DEBUG] _detect_nord_est_format: Analyse du texte: '{text}'")
    
    # Pattern pour détecter NORD/EST suivi de chiffres séparés par des espaces
    nord_est_regex = r'(?:NORD|Nord|nord)\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,3})(?:\s+|\s*[/\n]\s*)(?:EST|Est|est)\s+(\d{1,3})\s+(\d{1,2})\s+(\d{1,3})'
    
    print(f"[DEBUG] _detect_nord_est_format: Regex utilisée: {nord_est_regex}")
    
    match = re.search(nord_est_regex, text, re.IGNORECASE)
    if match:
        print(f"[DEBUG] _detect_nord_est_format: Match trouvé! Groupes: {match.groups()}")
        lat_deg, lat_min, lat_sec, lon_deg, lon_min, lon_sec = match.groups()
        
        # Formatage des coordonnées
        ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
        ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
        
        print(f"[DEBUG] _detect_nord_est_format: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_nord_est_format: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format NORD/EST avec variations
# ------------------------------------------------------------------------------

def _detect_nord_est_variations(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format NORD/EST avec diverses variations possibles, par exemple :
      - "NORD 48 32 296 EST 6 40 636"
      - "NORD48 32 296EST6 40 636"
      - "NORD48.32.296 EST6.40.636"
    
    Cette fonction est très flexible et tolérante aux variations de format.
    """
    print(f"[DEBUG] _detect_nord_est_variations: Analyse du texte: '{text}'")
    
    # Extraction des nombres après NORD et EST
    nord_match = re.search(r'(?:NORD|Nord|nord)[^\d]*(\d{1,2})[^\d]*(\d{1,2})[^\d]*(\d{1,3})', text, re.IGNORECASE)
    est_match = re.search(r'(?:EST|Est|est)[^\d]*(\d{1,3})[^\d]*(\d{1,2})[^\d]*(\d{1,3})', text, re.IGNORECASE)
    
    print(f"[DEBUG] _detect_nord_est_variations: Match NORD: {nord_match.groups() if nord_match else None}")
    print(f"[DEBUG] _detect_nord_est_variations: Match EST: {est_match.groups() if est_match else None}")
    
    if nord_match and est_match:
        lat_deg, lat_min, lat_sec = nord_match.groups()
        lon_deg, lon_min, lon_sec = est_match.groups()
        
        # Formatage des coordonnées
        ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
        ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
        
        print(f"[DEBUG] _detect_nord_est_variations: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_nord_est_variations: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format DMS (degrés, minutes, secondes)
# ------------------------------------------------------------------------------

def _detect_dms_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format DMS (degrés, minutes, secondes), par exemple :
      - "N 48° 51' 24.12\" E 002° 17' 26.1\""
    
    Ce format est couramment utilisé dans le géocaching.
    """
    print(f"[DEBUG] _detect_dms_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Pattern pour détecter le format DMS
    dms_regex = (
        r'([NS])\s*(\d{1,2})\s*[°º]\s*(\d{1,2})\s*[\']\s*(\d{1,2}(?:\.\d+)?)[\"\']*\s*'
        r'([EW])\s*(\d{1,3})\s*[°º]\s*(\d{1,2})\s*[\']\s*(\d{1,2}(?:\.\d+)?)[\"\']*'
    )
    
    print(f"[DEBUG] _detect_dms_coordinates: Regex utilisée: {dms_regex}")
    
    match = re.search(dms_regex, text)
    if match:
        print(f"[DEBUG] _detect_dms_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lat_sec, lon_dir, lon_deg, lon_min, lon_sec = match.groups()
        
        # Convertir DMS en DDM
        lat_min_decimal = float(lat_min) + float(lat_sec) / 60
        lon_min_decimal = float(lon_min) + float(lon_sec) / 60
        
        # Formatage des coordonnées
        ddm_lat = f"{lat_dir} {lat_deg}° {lat_min_decimal:.3f}'"
        ddm_lon = f"{lon_dir} {lon_deg}° {lon_min_decimal:.3f}'"
        
        print(f"[DEBUG] _detect_dms_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_dms_coordinates: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format avec chiffres romains
# ------------------------------------------------------------------------------

def _detect_roman_numerals_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées avec des chiffres romains, par exemple :
      - "NORD XLVIII XXXII CCXCVI EST VI XL DCXXXVI"
      - "N XLVIII° XXXII.CCXCVI' E VI° XL.DCXXXVI'"
    
    Cette fonction convertit d'abord les chiffres romains en chiffres arabes.
    """
    print(f"[DEBUG] _detect_roman_numerals_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Dictionnaire de conversion des chiffres romains
    roman_to_arabic = {
        'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000,
        'IV': 4, 'IX': 9, 'XL': 40, 'XC': 90, 'CD': 400, 'CM': 900
    }
    
    # Fonction pour convertir un nombre romain en nombre arabe
    def roman_to_int(roman: str) -> int:
        i, result = 0, 0
        while i < len(roman):
            # Vérifier les sous-chaînes de 2 caractères
            if i + 1 < len(roman) and roman[i:i+2] in roman_to_arabic:
                result += roman_to_arabic[roman[i:i+2]]
                i += 2
            else:
                # Vérifier les caractères individuels
                if roman[i] in roman_to_arabic:
                    result += roman_to_arabic[roman[i]]
                i += 1
        return result
    
    # Rechercher les motifs de chiffres romains
    roman_pattern = r'(NORD|Nord|nord|N)\s+((?:[IVXLCDM]+)\s+(?:[IVXLCDM]+)\s+(?:[IVXLCDM]+))\s+(EST|Est|est|E)\s+((?:[IVXLCDM]+)\s+(?:[IVXLCDM]+)\s+(?:[IVXLCDM]+))'
    
    print(f"[DEBUG] _detect_roman_numerals_coordinates: Pattern utilisé: {roman_pattern}")
    
    match = re.search(roman_pattern, text, re.IGNORECASE)
    if match:
        print(f"[DEBUG] _detect_roman_numerals_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_romans, lon_dir, lon_romans = match.groups()
        
        # Extraire les chiffres romains individuels
        lat_parts = lat_romans.split()
        lon_parts = lon_romans.split()
        
        if len(lat_parts) >= 3 and len(lon_parts) >= 3:
            try:
                # Convertir les chiffres romains en chiffres arabes
                lat_deg = roman_to_int(lat_parts[0])
                lat_min = roman_to_int(lat_parts[1])
                lat_sec = roman_to_int(lat_parts[2])
                
                lon_deg = roman_to_int(lon_parts[0])
                lon_min = roman_to_int(lon_parts[1])
                lon_sec = roman_to_int(lon_parts[2])
                
                print(f"[DEBUG] _detect_roman_numerals_coordinates: Conversion - lat: {lat_deg} {lat_min} {lat_sec}, lon: {lon_deg} {lon_min} {lon_sec}")
                
                # Formatage des coordonnées
                ddm_lat = f"N {lat_deg}° {lat_min}.{lat_sec}'"
                ddm_lon = f"E {lon_deg}° {lon_min}.{lon_sec}'"
                
                print(f"[DEBUG] _detect_roman_numerals_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
                
                return {
                    "exist": True,
                    "ddm_lat": ddm_lat,
                    "ddm_lon": ddm_lon,
                    "ddm": f"{ddm_lat} {ddm_lon}"
                }
            except Exception as e:
                print(f"[DEBUG] _detect_roman_numerals_coordinates: Erreur lors de la conversion: {e}")
    
    print("[DEBUG] _detect_roman_numerals_coordinates: Aucun match trouvé ou erreur de conversion")
    return None

# ------------------------------------------------------------------------------
# Détection du format numérique pur (ex: "4912123 00612123")
# ------------------------------------------------------------------------------

def _detect_numeric_only_coordinates(text: str, origin_coords: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format numérique pur, sans lettres cardinales ni symboles.
    Format attendu: "4912123 00612123" ou "4912123 612123" ou "4912123 0612123"
    pour N 49° 12.123' E 006° 12.123'
    
    La première partie représente la latitude: 2 chiffres degrés + 2 chiffres minutes + 3 chiffres décimales
    La deuxième partie représente la longitude: 3 chiffres degrés (avec éventuellement des 0 en tête) + 2 chiffres minutes + 3 chiffres décimales
    
    Si origin_coords est fourni, utilise les directions cardinales de ces coordonnées (N/S, E/W)
    plutôt que de supposer N et E par défaut.
    """
    print(f"[DEBUG] _detect_numeric_only_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    print(f"[DEBUG] _detect_numeric_only_coordinates: Coordonnées d'origine: {origin_coords}")
    
    # Pattern pour capturer deux groupes de chiffres séparés par un espace
    # Premier groupe: exactement 7 chiffres (latitude)
    # Deuxième groupe: 6 à 8 chiffres (longitude)
    numeric_pattern = r'(\d{7})\s+(\d{6,8})'
    
    print(f"[DEBUG] _detect_numeric_only_coordinates: Pattern utilisé: {numeric_pattern}")
    
    match = re.search(numeric_pattern, text)
    if match:
        print(f"[DEBUG] _detect_numeric_only_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_digits = match.group(1)
        lon_digits = match.group(2)
        
        print(f"[DEBUG] _detect_numeric_only_coordinates: Latitude digits: {lat_digits}, Longitude digits: {lon_digits}")
        # Anti-faux-positifs: rejeter les groupes purement binaires (0/1)
        try:
            has_lat_nonbinary = any(ch in '23456789' for ch in lat_digits)
            has_lon_nonbinary = any(ch in '23456789' for ch in lon_digits)
            if not has_lat_nonbinary or not has_lon_nonbinary:
                print("[DEBUG] _detect_numeric_only_coordinates: Groupes composés uniquement de 0/1 détectés, rejet")
                return None
        except Exception:
            return None
        
        try:
            # Pour la latitude
            lat_deg = lat_digits[0:2]
            lat_min = lat_digits[2:4]
            lat_dec = lat_digits[4:7]
            
            # Pour la longitude, nous devons nous assurer que les 3 premiers chiffres représentent les degrés
            # mais en tenant compte des zéros en tête possibles
            
            # Compléter la longitude avec des zéros en tête si nécessaire pour avoir 8 chiffres
            padded_lon_digits = lon_digits.zfill(8)
            print(f"[DEBUG] _detect_numeric_only_coordinates: Longitude avec padding: {padded_lon_digits}")
            
            # Maintenant nous pouvons découper correctement
            lon_deg = padded_lon_digits[0:3]
            lon_min = padded_lon_digits[3:5]
            lon_dec = padded_lon_digits[5:8]
            
            print(f"[DEBUG] _detect_numeric_only_coordinates: Découpage - lon_deg={lon_deg}, lon_min={lon_min}, lon_dec={lon_dec}")
            
            # Déterminer les directions cardinales (N/S, E/W) en fonction des coordonnées d'origine
            lat_dir = "N"  # Direction par défaut
            lon_dir = "E"  # Direction par défaut
            
            if origin_coords and 'ddm_lat' in origin_coords and 'ddm_lon' in origin_coords:
                try:
                    # Extraire les directions des coordonnées d'origine
                    origin_lat_match = re.search(r'([NS])', origin_coords['ddm_lat'])
                    origin_lon_match = re.search(r'([EW])', origin_coords['ddm_lon'])
                    
                    if origin_lat_match:
                        lat_dir = origin_lat_match.group(1)
                        print(f"[DEBUG] Direction latitude depuis origine: {lat_dir}")
                    
                    if origin_lon_match:
                        lon_dir = origin_lon_match.group(1)
                        print(f"[DEBUG] Direction longitude depuis origine: {lon_dir}")
                except Exception as e:
                    print(f"[WARNING] Erreur lors de l'extraction des directions depuis les coordonnées d'origine: {e}")
                    # En cas d'erreur, on garde les directions par défaut
            
            # Validation bornes
            try:
                if not _is_valid_degrees_minutes(int(lat_deg), float(f"{lat_min}.{lat_dec}"), int(lon_deg), float(f"{lon_min}.{lon_dec}")):
                    print("[DEBUG] _detect_numeric_only_coordinates: Bornes invalides, rejet")
                    return None
            except Exception:
                return None

            # Formatage des coordonnées
            ddm_lat = f"{lat_dir} {lat_deg}° {lat_min}.{lat_dec}'"
            ddm_lon = f"{lon_dir} {lon_deg}° {lon_min}.{lon_dec}'"
            
            print(f"[DEBUG] _detect_numeric_only_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
            
            return {
                "exist": True,
                "ddm_lat": ddm_lat,
                "ddm_lon": ddm_lon,
                "ddm": f"{ddm_lat} {ddm_lon}"
            }
        except Exception as e:
            print(f"[ERROR] _detect_numeric_only_coordinates: Erreur lors du formatage: {e}")
            traceback.print_exc()
    
    print("[DEBUG] _detect_numeric_only_coordinates: Aucun match trouvé ou erreur de conversion")
    return None

# ------------------------------------------------------------------------------
# Détection du format compact sans séparateurs (ex: "N4812123E00612123")
# ------------------------------------------------------------------------------

def _detect_compact_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format compact sans séparateurs entre les composants, par exemple :
      - "N4812123E00612123"
      - "N 4812123 E 00612123"
      - "N4812123 E00612123"
      - "S4812123W00612123"
    
    Ce format est très compact et ne contient pas de symboles de degrés ni de minutes.
    """
    print(f"[DEBUG] _detect_compact_coordinates: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Pattern pour détecter le format compact
    # Capture: direction latitude, 7 chiffres, direction longitude, 6-8 chiffres
    # Accepte des espaces optionnels entre les composants
    compact_regex = r'([NS])\s*(\d{7})\s*([EW])\s*(\d{6,8})'
    
    print(f"[DEBUG] _detect_compact_coordinates: Regex utilisée: {compact_regex}")
    
    match = re.search(compact_regex, text, re.IGNORECASE)
    if match:
        print(f"[DEBUG] _detect_compact_coordinates: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_digits, lon_dir, lon_digits = match.groups()
        
        print(f"[DEBUG] _detect_compact_coordinates: Latitude: {lat_dir}{lat_digits}, Longitude: {lon_dir}{lon_digits}")
        # Anti-faux-positifs: rejeter les groupes purement binaires (0/1)
        try:
            has_lat_nonbinary = any(ch in '23456789' for ch in lat_digits)
            has_lon_nonbinary = any(ch in '23456789' for ch in lon_digits)
            if not has_lat_nonbinary or not has_lon_nonbinary:
                print("[DEBUG] _detect_compact_coordinates: Groupes composés uniquement de 0/1 détectés, rejet")
                return None
        except Exception:
            return None
        
        try:
            # Pour la latitude
            lat_deg = lat_digits[0:2]
            lat_min = lat_digits[2:4]
            lat_dec = lat_digits[4:7]
            
            # Pour la longitude, compléter avec des zéros en tête si nécessaire
            padded_lon_digits = lon_digits.zfill(8)
            lon_deg = padded_lon_digits[0:3]
            lon_min = padded_lon_digits[3:5]
            lon_dec = padded_lon_digits[5:8]
            
            # Validation bornes
            try:
                if not _is_valid_degrees_minutes(int(lat_deg), float(f"{lat_min}.{lat_dec}"), int(lon_deg), float(f"{lon_min}.{lon_dec}")):
                    print("[DEBUG] _detect_compact_coordinates: Bornes invalides, rejet")
                    return None
            except Exception:
                return None

            # Formatage des coordonnées
            ddm_lat = f"{lat_dir} {lat_deg}° {lat_min}.{lat_dec}'"
            ddm_lon = f"{lon_dir} {lon_deg}° {lon_min}.{lon_dec}'"
            
            print(f"[DEBUG] _detect_compact_coordinates: Coordonnées formatées: {ddm_lat} {ddm_lon}")
            
            return {
                "exist": True,
                "ddm_lat": ddm_lat,
                "ddm_lon": ddm_lon,
                "ddm": f"{ddm_lat} {ddm_lon}"
            }
        except Exception as e:
            print(f"[ERROR] _detect_compact_coordinates: Erreur lors du formatage: {e}")
            traceback.print_exc()
    
    print("[DEBUG] _detect_compact_coordinates: Aucun match trouvé ou erreur de conversion")
    return None

# ------------------------------------------------------------------------------
# Détection du format DDM avec points à la place du symbole °
# Exemple : "N50.02.117 e004.52.677"
# ------------------------------------------------------------------------------

def _detect_dmm_dot_separator(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées DDM où les séparateurs de degrés et minutes sont des points :
      - N50.02.117 e004.52.677
    Les lettres cardinales peuvent être en majuscule ou minuscule.
    """
    print(f"[DEBUG] _detect_dmm_dot_separator: Analyse du texte: '{text[:100]}...' (tronqué)")

    pattern = (
        r'([ns])\s*(\d{1,2})\.(\d{2})\.(\d{3})\s*'  # Latitude : dir, deg, minutes, décimales
        r'([ew])\s*(\d{1,3})\.(\d{2})\.(\d{3})'     # Longitude : dir, deg, minutes, décimales
    )

    print(f"[DEBUG] _detect_dmm_dot_separator: Regex utilisée: {pattern}")
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        print(f"[DEBUG] _detect_dmm_dot_separator: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lat_dec, lon_dir, lon_deg, lon_min, lon_dec = match.groups()

        # Normaliser les directions
        lat_dir = lat_dir.upper()
        lon_dir = lon_dir.upper()

        try:
            lat_min_f = float(f"{lat_min}.{lat_dec}")
            lon_min_f = float(f"{lon_min}.{lon_dec}")
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_dmm_dot_separator: Bornes invalides, rejet")
                return None
        except Exception:
            return None

        ddm_lat = f"{lat_dir} {lat_deg.zfill(2)}° {lat_min}.{lat_dec}'"
        ddm_lon = f"{lon_dir} {lon_deg.zfill(3)}° {lon_min}.{lon_dec}'"

        print(f"[DEBUG] _detect_dmm_dot_separator: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }

    print("[DEBUG] _detect_dmm_dot_separator: Aucun match trouvé")
    return None
# Fonction principale de détection multi-format
# ------------------------------------------------------------------------------

def detect_gps_coordinates(text: str, include_numeric_only: bool = False, origin_coords: Optional[Dict[str, str]] = None, include_written: bool = False) -> Dict[str, Optional[str]]:
    """
    Recherche des coordonnées GPS dans un texte en testant plusieurs formats.
    
    Args:
        text (str): Le texte dans lequel rechercher des coordonnées
        include_numeric_only (bool): Si True, inclut la détection de coordonnées au format numérique pur (sans lettres cardinales ni symboles)
        origin_coords (Optional[Dict[str, str]]): Coordonnées d'origine au format DDM (optionnel)
        include_written (bool): Si True, inclut la détection de coordonnées écrites en toutes lettres
    
    Le dictionnaire retourné contient :
      - 'exist': True si une coordonnée a été trouvée, sinon False.
      - 'ddm_lat': Latitude formatée en DDM.
      - 'ddm_lon': Longitude formatée en DDM.
      - 'ddm': Latitude et longitude combinées.
    """
    print(f"[DEBUG] detect_gps_coordinates: Début de la détection sur texte de {len(text)} caractères")
    print(f"[DEBUG] detect_gps_coordinates: Extrait du texte: '{text[:100]}...' (tronqué)")
    print(f"[DEBUG] detect_gps_coordinates: include_numeric_only={include_numeric_only}, origin_coords={origin_coords}, include_written={include_written}")
    
    # ------------------------------------------------------------------
    # Carte <fonction de détection> -> score de confiance (0-1)
    # Plus la valeur est élevée, plus le format est considéré fiable.
    # Ces valeurs peuvent être ajustées facilement pour faire évoluer la
    # pondération sans toucher au reste du code.
    # ------------------------------------------------------------------
    confidence_map = {
        _detect_geocaching_standard_format:  0.96,  # Nouvelle fonction avec haute priorité
        _detect_dmm_dot_separator:           0.95,
        _detect_compact_coordinates:         0.95,
        _detect_dmm_coordinates:             0.95,
        _detect_dms_coordinates:             0.92,
        _detect_roman_numerals_coordinates:  0.90,
        _detect_tabspace_coordinates:        0.90,
        _detect_dmm_no_degree_symbol:        0.90,
        _detect_nord_est_variations:         0.88,
        _detect_nord_est_format:             0.85,
        _detect_dmm_no_symbol_no_dot:        0.85,
        _detect_specific_tabpoint_coordinates:0.82,
        _detect_simplified_coordinates:      0.80,
        _detect_flexible_coordinates:        0.75,
        _detect_variant_coordinates:         0.70,
    }

    detection_functions = list(confidence_map.keys())
    
    if include_numeric_only:
        print("[DEBUG] detect_gps_coordinates: Détection de coordonnées numériques pures activée")
        # Pour la détection numérique, on passe les coordonnées d'origine
        result = _detect_numeric_only_coordinates(text, origin_coords)
        if result and result.get("exist"):
            print(f"[DEBUG] detect_gps_coordinates: Coordonnées numériques pures trouvées: {result}")
            # Attribuer une confiance légèrement inférieure au format compact complet
            result["source"] = _detect_numeric_only_coordinates.__name__
            result["confidence"] = 0.90
            return result
    
    for i, detect_func in enumerate(detection_functions):
        print(f"[DEBUG] detect_gps_coordinates: Essai de la fonction de détection #{i+1}: {detect_func.__name__}")
        result = detect_func(text)
        if result and result.get("exist"):
            # Injecter la provenance et la confiance dans le résultat avant de le renvoyer
            result["source"] = detect_func.__name__
            result["confidence"] = confidence_map.get(detect_func, 0.75)
            # 🆕 Conversion décimale centralisée côté backend pour éviter de la recalculer côté frontend
            if result.get("decimal_latitude") is None or result.get("decimal_longitude") is None:
                ddm_lat = result.get("ddm_lat")
                ddm_lon = result.get("ddm_lon")

                # Si nécessaire, tenter d'extraire latitude/longitude à partir du format combiné
                if (not ddm_lat or not ddm_lon) and result.get("ddm"):
                    combined_match = re.match(r"^([NS][^NSWE]+)[\s,]+([EW].+)$", result["ddm"].strip(), re.IGNORECASE)
                    if combined_match:
                        ddm_lat = ddm_lat or combined_match.group(1).strip()
                        ddm_lon = ddm_lon or combined_match.group(2).strip()

                if ddm_lat and ddm_lon:
                    try:
                        decimal_coords = convert_ddm_to_decimal(ddm_lat, ddm_lon)
                    except Exception as e:
                        print(f"[DEBUG] detect_gps_coordinates: Erreur conversion DDM->décimal: {e}")
                        decimal_coords = {}

                    if decimal_coords.get('latitude') is not None and decimal_coords.get('longitude') is not None:
                        result.setdefault('decimal_latitude', decimal_coords['latitude'])
                        result.setdefault('decimal_longitude', decimal_coords['longitude'])

            print(f"[DEBUG] detect_gps_coordinates: Coordonnées trouvées par {detect_func.__name__}: {result}")
            result["matched_text"] = text
            result["extract"] = {"plugin": detect_func.__name__, "version": "1.0"}

            return result
    
    if include_written:
        try:
            written_result = _detect_word_coordinates(text)
        except Exception:
            written_result = None
        if written_result and written_result.get("exist"):
            written_result.setdefault("source", _detect_word_coordinates.__name__)
            written_result.setdefault("confidence", 0.98)
            written_result.setdefault("matched_text", text)
            written_result.setdefault("extract", {"plugin": _detect_word_coordinates.__name__, "version": "1.0"})
            return written_result

    print("[DEBUG] detect_gps_coordinates: Aucune coordonnée GPS détectée")
    return {
        "exist": False,
        "ddm_lat": None,
        "ddm_lon": None,
        "ddm": None,
        "source": None,
        "confidence": 0.0,
        "matched_text": None,
        "extract": None
    }

# Nouvelle route API pour détecter les coordonnées dans un texte
@coordinates_bp.route('/api/detect_coordinates', methods=['POST'])
def detect_coordinates_in_text():
    """
    Analyse un texte pour détecter des coordonnées GPS.
    
    Attend un JSON avec:
    - 'text': Texte à analyser
    - 'include_numeric_only' (optionnel): Si True, active la détection de coordonnées au format numérique pur
    - 'include_written' (optionnel): Si True, active la détection de coordonnées écrites en toutes lettres
    - 'origin_coords' (optionnel): Coordonnées d'origine au format DDM pour déterminer les directions cardinales
      Format attendu: {'ddm_lat': 'N 48° 40.123\'', 'ddm_lon': 'E 06° 10.456\''}
    
    Retourne :
    {
        "exist": true/false,
        "ddm_lat": "Latitude formatée",
        "ddm_lon": "Longitude formatée",
        "ddm": "Coordonnées combinées"
    }
    """
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({"error": "Le champ 'text' est requis"}), 400
            
        text = data['text']
        # Récupérer le paramètre include_numeric_only (False par défaut)
        include_numeric_only = data.get('include_numeric_only', False)
        # Récupérer le paramètre include_written (False par défaut)
        include_written = data.get('include_written', False)
        written_languages = data.get('written_languages', ['fr'])
        written_max_candidates = data.get('written_max_candidates', 20)
        written_include_deconcat = data.get('written_include_deconcat', True)
        # Récupérer les coordonnées d'origine (None par défaut)
        origin_coords = data.get('origin_coords')
        
        print(f"[DEBUG] Analyse du texte pour détecter des coordonnées: '{text[:50]}...' (tronqué)")
        print(f"[DEBUG] Détection de format numérique pur activée: {include_numeric_only}")
        print(f"[DEBUG] Détection de coordonnées écrites activée: {include_written}")
        print(f"[DEBUG] Coordonnées d'origine fournies: {origin_coords}")
        
        result = detect_gps_coordinates(
            text,
            include_numeric_only=include_numeric_only,
            origin_coords=origin_coords,
            include_written=False,
        )

        if not include_written:
            return jsonify(result)

        enriched = dict(result or {})
        enriched['written'] = {
            'enabled': True,
            'attempted': False,
            'languages': written_languages,
            'max_candidates': written_max_candidates,
            'include_deconcat': written_include_deconcat,
            'candidates': [],
            'combined_results': {},
        }

        if enriched.get('exist'):
            return jsonify(enriched)

        try:
            from gc_backend.blueprints.plugins import get_plugin_manager
            from gc_backend.services import WrittenCoordinatesService

            plugin_manager = get_plugin_manager()
            service = WrittenCoordinatesService(plugin_manager)

            langs = written_languages
            if isinstance(langs, str):
                langs = [p.strip() for p in langs.split(',') if p.strip()]
            if not isinstance(langs, list) or not langs:
                langs = ['fr']

            try:
                max_cand = int(written_max_candidates)
            except Exception:
                max_cand = 20

            wr = service.find(
                text,
                languages=[str(x).strip().lower() for x in langs if str(x).strip()],
                max_candidates=max_cand,
                include_deconcat=bool(written_include_deconcat),
                origin_coords=origin_coords,
            )

            enriched['written']['attempted'] = True
            enriched['written']['candidates'] = wr.candidates
            enriched['written']['combined_results'] = wr.combined_results

            if wr.exist and wr.primary_coordinates:
                merged = dict(wr.primary_coordinates)
                merged['written'] = enriched['written']
                return jsonify(merged)

            return jsonify(enriched)
        except Exception as e:
            enriched['written']['attempted'] = True
            enriched['written']['error'] = str(e)
            return jsonify(enriched)
        
    except Exception as e:
        print(f"[ERROR] Erreur lors de la détection des coordonnées: {str(e)}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def _detect_word_coordinates(text: str) -> Optional[Dict[str, Optional[str]]]:
    """Tente de détecter des coordonnées GPS exprimées en toutes lettres en utilisant
    le plugin `written_coords_converter`. Retourne un dict au même format que les autres
    fonctions de détection ou None si aucune coordonnée n'est trouvée."""
    try:
        from gc_backend.blueprints.plugins import get_plugin_manager

        plugin_manager = get_plugin_manager()
        plugin_result = plugin_manager.execute_plugin(
            "written_coords_converter",
            {
                "text": text,
                "languages": ["fr"],
                "max_candidates": 20,
                "include_deconcat": True,
            },
        )

        primary = (plugin_result or {}).get("primary_coordinates")
        if isinstance(primary, dict) and primary.get("exist"):
            return primary

        for item in (plugin_result or {}).get("results", []) or []:
            if not isinstance(item, dict):
                continue
            coords = item.get("coordinates")
            if isinstance(coords, dict) and coords.get("exist"):
                return coords

        return None
    except Exception:
        traceback.print_exc()
        return None

def _detect_dmm_no_degree_symbol(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées DDM où le symbole ° est absent, par exemple :
      - "N 38 32.460 W 075 43.659"
      - "N38 32.460 W075 43.659"
    Le séparateur entre degrés et minutes est simplement un ou plusieurs espaces.
    """
    print(f"[DEBUG] _detect_dmm_no_degree_symbol: Analyse du texte: '{text[:100]}...' (tronqué)")

    # Regex tolérante aux espaces optionnels après la direction et au formatage varié.
    # Latitude : N/S, 1-2 chiffres degrés, espace(s), minutes avec/sans décimales
    # Longitude : E/W, 1-3 chiffres degrés, espace(s), minutes avec/sans décimales
    dmm_nodg_regex = (
        r'([NS])\s*(\d{1,2})\s+(\d{1,2}(?:[.,]\d+)?)\s*'  # Latitude
        r'([EW])\s*(\d{1,3})\s+(\d{1,2}(?:[.,]\d+)?)'       # Longitude
    )

    print(f"[DEBUG] _detect_dmm_no_degree_symbol: Regex utilisée: {dmm_nodg_regex}")
    match = re.search(dmm_nodg_regex, text)
    if match:
        print(f"[DEBUG] _detect_dmm_no_degree_symbol: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lon_dir, lon_deg, lon_min = match.groups()

        # Remplacer la virgule éventuelle par un point pour les décimales
        lat_min = lat_min.replace(',', '.')
        lon_min = lon_min.replace(',', '.')

        # S'assurer que les valeurs minutes ont bien 3 décimales (format géocaching)
        def _format_minutes(min_val_str: str) -> str:
            if '.' in min_val_str:
                whole, dec = min_val_str.split('.')
                return f"{whole.zfill(2)}.{dec.ljust(3, '0')[:3]}"
            else:
                # Pas de décimales – on complète
                return f"{min_val_str.zfill(2)}.000"

        lat_min_fmt = _format_minutes(lat_min)
        lon_min_fmt = _format_minutes(lon_min)

        # Validation des bornes
        try:
            lat_min_f = float(lat_min_fmt)
            lon_min_f = float(lon_min_fmt)
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_dmm_no_degree_symbol: Bornes invalides, rejet")
                return None
        except Exception:
            return None

        ddm_lat = f"{lat_dir} {lat_deg.zfill(2)}° {lat_min_fmt}'"
        ddm_lon = f"{lon_dir} {lon_deg.zfill(3)}° {lon_min_fmt}'"

        print(f"[DEBUG] _detect_dmm_no_degree_symbol: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }

    print("[DEBUG] _detect_dmm_no_degree_symbol: Aucun match trouvé")
    return None

# ----------------------------------------------------------------------------
# Détection du format DMM sans ° ni . (ex : "N 38 32 460 W 075 43 659")
# ----------------------------------------------------------------------------

def _detect_dmm_no_symbol_no_dot(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées DDM sans symbole ° ni point décimal :
      - "N 38 32 460 W 075 43 659"
    Séquence attendue :
      N/S  deg  min  dec   E/W  deg   min  dec
    avec espaces comme séparateurs.
    """
    print(f"[DEBUG] _detect_dmm_no_symbol_no_dot: Analyse du texte: '{text[:100]}...' (tronqué)")

    pattern = (
        r'([NS])\s*(\d{1,2})\s+(\d{1,2})\s+(\d{1,3})\s*'  # Latitude
        r'([EW])\s*(\d{1,3})\s+(\d{1,2})\s+(\d{1,3})'      # Longitude
    )

    print(f"[DEBUG] _detect_dmm_no_symbol_no_dot: Regex utilisée: {pattern}")
    match = re.search(pattern, text)
    if match:
        print(f"[DEBUG] _detect_dmm_no_symbol_no_dot: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lat_dec, lon_dir, lon_deg, lon_min, lon_dec = match.groups()

        # Mise en forme des composantes minutes + décimales
        lat_min_fmt = lat_min.zfill(2)
        lon_min_fmt = lon_min.zfill(2)
        lat_dec_fmt = lat_dec.ljust(3, '0')[:3]
        lon_dec_fmt = lon_dec.ljust(3, '0')[:3]

        # Validation des bornes
        try:
            lat_min_f = float(f"{lat_min_fmt}.{lat_dec_fmt}")
            lon_min_f = float(f"{lon_min_fmt}.{lon_dec_fmt}")
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_dmm_no_symbol_no_dot: Bornes invalides, rejet")
                return None
        except Exception:
            return None

        ddm_lat = f"{lat_dir} {lat_deg.zfill(2)}° {lat_min_fmt}.{lat_dec_fmt}'"
        ddm_lon = f"{lon_dir} {lon_deg.zfill(3)}° {lon_min_fmt}.{lon_dec_fmt}'"

        print(f"[DEBUG] _detect_dmm_no_symbol_no_dot: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }

    print("[DEBUG] _detect_dmm_no_symbol_no_dot: Aucun match trouvé")
    return None

# ------------------------------------------------------------------------------
# Détection du format géocaching standard (ex: "N29 02.879 W98 01.304")
# ------------------------------------------------------------------------------

def _detect_geocaching_standard_format(text: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Détecte les coordonnées au format géocaching standard, par exemple :
      - "N29 02.879 W98 01.304"
      - "N48 33.787 E006 38.803"
    Format : Direction + Degrés + Espace + Minutes.Décimales + Espace + Direction + Degrés + Espace + Minutes.Décimales
    """
    print(f"[DEBUG] _detect_geocaching_standard_format: Analyse du texte: '{text[:100]}...' (tronqué)")
    
    # Regex spécialement conçue pour le format géocaching standard
    # Accepte : N/S + 1-2 chiffres + espace + minutes.décimales + espace + E/W + 1-3 chiffres + espace + minutes.décimales
    geocaching_regex = r'([NS])(\d{1,2})\s+(\d{1,2}\.\d{1,3})\s+([EW])(\d{1,3})\s+(\d{1,2}\.\d{1,3})'
    
    print(f"[DEBUG] _detect_geocaching_standard_format: Regex utilisée: {geocaching_regex}")
    match = re.search(geocaching_regex, text)
    if match:
        print(f"[DEBUG] _detect_geocaching_standard_format: Match trouvé! Groupes: {match.groups()}")
        lat_dir, lat_deg, lat_min, lon_dir, lon_deg, lon_min = match.groups()
        
        # Formatage des coordonnées (s'assurer que les minutes ont 3 décimales)
        def format_minutes(min_str):
            if '.' in min_str:
                whole, dec = min_str.split('.')
                return f"{whole.zfill(2)}.{dec.ljust(3, '0')[:3]}"
            return f"{min_str.zfill(2)}.000"
        
        lat_min_fmt = format_minutes(lat_min)
        lon_min_fmt = format_minutes(lon_min)

        # Validation des bornes
        try:
            lat_min_f = float(lat_min_fmt)
            lon_min_f = float(lon_min_fmt)
            if not _is_valid_degrees_minutes(int(lat_deg), lat_min_f, int(lon_deg), lon_min_f):
                print("[DEBUG] _detect_geocaching_standard_format: Bornes invalides, rejet")
                return None
        except Exception:
            return None
        
        ddm_lat = f"{lat_dir} {lat_deg.zfill(2)}° {lat_min_fmt}'"
        ddm_lon = f"{lon_dir} {lon_deg.zfill(3)}° {lon_min_fmt}'"
        
        print(f"[DEBUG] _detect_geocaching_standard_format: Coordonnées formatées: {ddm_lat} {ddm_lon}")
        
        return {
            "exist": True,
            "ddm_lat": ddm_lat,
            "ddm_lon": ddm_lon,
            "ddm": f"{ddm_lat} {ddm_lon}"
        }
    
    print("[DEBUG] _detect_geocaching_standard_format: Aucun match trouvé")
    return None
