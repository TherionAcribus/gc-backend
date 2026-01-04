"""
Module de calcul de coordonnées GPS
Parse et évalue les formules de coordonnées avec variables
"""

import re
import math
from typing import Dict, Any, Tuple
from loguru import logger


class CoordinateCalculator:
    """Calculateur de coordonnées GPS à partir de formules avec variables"""
    
    def __init__(self):
        """Initialise le calculateur"""
        pass
    
    def calculate_coordinates(
        self,
        north_formula: str,
        east_formula: str,
        values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calcule les coordonnées finales à partir des formules et valeurs.
        
        Args:
            north_formula: Formule Nord (ex: "N 47° 5E.FTN")
            east_formula: Formule Est (ex: "E 006° 5A.JVF")
            values: Dictionnaire des valeurs (ex: {'A': 3, 'E': 8, ...})
        
        Returns:
            Dict avec status, coordinates, calculation_steps
        
        Raises:
            ValueError: Si formule invalide ou valeur manquante
        """
        try:
            # Substituer les variables
            north_substituted = self.substitute_variables(north_formula, values)
            east_substituted = self.substitute_variables(east_formula, values)
            
            logger.debug(f"Formules substituées: N={north_substituted}, E={east_substituted}")
            
            # Parser et calculer
            lat = self._parse_coordinate(north_substituted, 'N')
            lon = self._parse_coordinate(east_substituted, 'E')
            
            # Formater dans différents formats
            coordinates = {
                'latitude': lat,
                'longitude': lon,
                'ddm': self._format_ddm(lat, lon),
                'dms': self._format_dms(lat, lon),
                'decimal': f"{lat}, {lon}"
            }
            
            return {
                'status': 'success',
                'coordinates': coordinates,
                'calculation_steps': {
                    'north_original': north_formula,
                    'east_original': east_formula,
                    'north_substituted': north_substituted,
                    'east_substituted': east_substituted
                }
            }
        
        except Exception as e:
            logger.error(f"Erreur lors du calcul de coordonnées: {e}")
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def substitute_variables(
        self,
        formula: str,
        values: Dict[str, Any]
    ) -> str:
        """
        Substitue les variables par leurs valeurs dans la formule.

        Args:
            formula: Formule avec variables (ex: "N 47° 5E.FTN")
            values: Dictionnaire lettre -> valeur

        Returns:
            Formule avec valeurs substituées (ex: "N 47° 58.195")

        Raises:
            ValueError: Si une variable n'a pas de valeur
        """
        result = formula

        # Normaliser les espaces (y compris NBSP, etc.)
        result = re.sub(r'\s+', ' ', result).strip().replace('\u00C2', '')

        # Nettoyer les formules qui commencent par "X=" où X est une direction cardinale
        # (Cas d'erreur de génération AI)
        for cardinal in ['N', 'S', 'E', 'W']:
            if result.startswith(f'{cardinal}='):
                result = result[2:]  # Supprimer "N=" etc.
                break

        # IMPORTANT: ne jamais remplacer le cardinal (N/E/S/W) de tête, même si la lettre est une variable
        # (ex: "E 002° 51. DEF" avec E=3 doit devenir "E 002° 51. 333", pas "3 002° ...").
        prefix = ''
        body = result
        m = re.match(r'^([NSEWO])(\s*)', result, flags=re.IGNORECASE)
        if m:
            prefix = m.group(1).upper() + (m.group(2) or '')
            body = result[m.end():]

        # Détecter les variables uniquement dans le corps (sans le cardinal de tête)
        variables = set(re.findall(r'([A-Z])', body))
        
        # Vérifier que toutes les variables ont une valeur
        missing = []
        for var in variables:
            if var not in values:
                missing.append(var)
        
        if missing:
            raise ValueError(f"Valeurs manquantes pour les variables: {', '.join(missing)}")
        
        # Substituer les variables (ordre décroissant pour éviter les conflits)
        # Ex: substituer Z avant Y pour éviter que "Y" ne devienne "5Z" si Y=5
        for var in sorted(variables, reverse=True):
            value = values[var]
            # Remplacer toutes les occurrences de la lettre dans le corps uniquement
            body = body.replace(var, str(value))
        
        # Évaluer les expressions entre parenthèses ou après le point
        body = self._evaluate_expressions(body)

        # Normaliser les séparateurs DDM (évite "49. 333" qui serait parsé comme "49.000")
        body = re.sub(r'\s*\.\s*', '.', body)
        
        return f"{prefix}{body}"
    
    def _evaluate_expressions(self, formula: str) -> str:
        """
        Évalue les expressions arithmétiques dans la formule de manière sécurisée.
        
        Args:
            formula: Formule avec expressions (ex: "N 47° 5(3+2).195")
        
        Returns:
            Formule avec expressions évaluées (ex: "N 47° 55.195")
        """
        # Pattern pour détecter les expressions entre parenthèses
        # ou les opérations simples après le point décimal
        
        # 1. Évaluer les expressions entre parenthèses
        while True:
            match = re.search(r'\(([0-9+\-*/\s]+)\)', formula)
            if not match:
                break
            
            expr = match.group(1)
            try:
                # Évaluation sécurisée (seulement nombres et opérateurs)
                result = self._safe_eval(expr)
                formula = formula[:match.start()] + str(result) + formula[match.end():]
            except Exception as e:
                logger.warning(f"Impossible d'évaluer l'expression '{expr}': {e}")
                break
        
        # 2. Gérer les cas comme "5E.FTN" -> "58.195" si E=8, F=1, T=9, N=5
        # Remplacer les chiffres/lettres consécutifs par leur concaténation
        # Ce cas est déjà géré par substitute_variables
        
        return formula
    
    def _safe_eval(self, expr: str) -> float:
        """
        Évalue une expression arithmétique de manière sécurisée.
        
        Args:
            expr: Expression à évaluer (ex: "3+2*4")
        
        Returns:
            Résultat de l'évaluation
        
        Raises:
            ValueError: Si expression invalide ou dangereuse
        """
        # Nettoyer l'expression
        expr = expr.strip()
        
        # Vérifier que l'expression ne contient que des caractères autorisés
        if not re.match(r'^[0-9+\-*/\s().]+$', expr):
            raise ValueError(f"Expression invalide ou dangereuse: {expr}")
        
        # Vérifier qu'il n'y a pas de double opérateurs (ex: "++", "**")
        if re.search(r'[+\-*/]{2,}', expr.replace('**', '')):  # ** est autorisé pour puissance
            raise ValueError(f"Opérateurs consécutifs invalides: {expr}")
        
        # Évaluer avec un namespace restreint (pas d'accès aux builtins)
        try:
            # Créer un namespace vide pour la sécurité
            safe_dict = {
                '__builtins__': {},
                'abs': abs,
                'round': round,
                'int': int,
                'float': float
            }
            
            result = eval(expr, safe_dict, {})
            
            # Vérifier le résultat
            if not isinstance(result, (int, float)):
                raise ValueError(f"Résultat invalide: {result}")
            
            # Arrondir si nécessaire
            if isinstance(result, float):
                result = round(result, 6)
            
            return result
        
        except ZeroDivisionError:
            raise ValueError("Division par zéro détectée")
        except Exception as e:
            raise ValueError(f"Erreur d'évaluation: {e}")
    
    def _parse_coordinate(self, coord_str: str, hemisphere: str) -> float:
        """
        Parse une coordonnée au format DDM en décimal.
        
        Args:
            coord_str: Coordonnée (ex: "N 47° 53.900")
            hemisphere: 'N', 'S', 'E', ou 'W'
        
        Returns:
            Coordonnée en décimal
        
        Raises:
            ValueError: Si format invalide
        """
        # Pattern pour DDM: N 47° 53.900
        pattern = r'[NSEW]?\s*(\d{1,3})\s*°\s*(\d{1,2}(?:\.\d+)?)'
        
        match = re.search(pattern, coord_str)
        if not match:
            raise ValueError(f"Format de coordonnée invalide: {coord_str}")
        
        degrees = int(match.group(1))
        minutes = float(match.group(2))
        
        # Convertir en décimal
        decimal = degrees + (minutes / 60.0)
        
        # Appliquer le signe selon l'hémisphère
        if hemisphere in ['S', 'W']:
            decimal = -decimal
        
        # Valider les limites
        if hemisphere in ['N', 'S']:
            if not -90 <= decimal <= 90:
                raise ValueError(f"Latitude hors limites: {decimal}")
        else:  # E, W
            if not -180 <= decimal <= 180:
                raise ValueError(f"Longitude hors limites: {decimal}")
        
        return round(decimal, 8)
    
    def _format_ddm(self, lat: float, lon: float) -> str:
        """
        Formate les coordonnées en DDM (Degrees Decimal Minutes).
        
        Args:
            lat: Latitude en décimal
            lon: Longitude en décimal
        
        Returns:
            Chaîne formatée (ex: "N 47° 53.900 E 006° 05.000")
        """
        # Latitude
        lat_hem = 'N' if lat >= 0 else 'S'
        lat_abs = abs(lat)
        lat_deg = int(lat_abs)
        lat_min = (lat_abs - lat_deg) * 60
        
        # Longitude
        lon_hem = 'E' if lon >= 0 else 'W'
        lon_abs = abs(lon)
        lon_deg = int(lon_abs)
        lon_min = (lon_abs - lon_deg) * 60
        
        return f"{lat_hem} {lat_deg:02d}° {lat_min:06.3f} {lon_hem} {lon_deg:03d}° {lon_min:06.3f}"
    
    def _format_dms(self, lat: float, lon: float) -> str:
        """
        Formate les coordonnées en DMS (Degrees Minutes Seconds).
        
        Args:
            lat: Latitude en décimal
            lon: Longitude en décimal
        
        Returns:
            Chaîne formatée (ex: "N 47° 53' 54.0\" E 006° 05' 00.0\"")
        """
        # Latitude
        lat_hem = 'N' if lat >= 0 else 'S'
        lat_abs = abs(lat)
        lat_deg = int(lat_abs)
        lat_min_dec = (lat_abs - lat_deg) * 60
        lat_min = int(lat_min_dec)
        lat_sec = (lat_min_dec - lat_min) * 60
        
        # Longitude
        lon_hem = 'E' if lon >= 0 else 'W'
        lon_abs = abs(lon)
        lon_deg = int(lon_abs)
        lon_min_dec = (lon_abs - lon_deg) * 60
        lon_min = int(lon_min_dec)
        lon_sec = (lon_min_dec - lon_min) * 60
        
        return f"{lat_hem} {lat_deg:02d}° {lat_min:02d}' {lat_sec:04.1f}\" {lon_hem} {lon_deg:03d}° {lon_min:02d}' {lon_sec:04.1f}\""
    
    def calculate_distance(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        """
        Calcule la distance entre deux points GPS (formule de Haversine).
        
        Args:
            lat1: Latitude du point 1
            lon1: Longitude du point 1
            lat2: Latitude du point 2
            lon2: Longitude du point 2
        
        Returns:
            Distance en kilomètres
        """
        # Rayon de la Terre en km
        R = 6371.0
        
        # Convertir en radians
        lat1_rad = math.radians(lat1)
        lon1_rad = math.radians(lon1)
        lat2_rad = math.radians(lat2)
        lon2_rad = math.radians(lon2)
        
        # Différences
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        
        # Formule de Haversine
        a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        distance = R * c
        
        return distance
