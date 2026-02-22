"""
Plugin Caesar (ROT-N) pour MysterAI.

Ce plugin implémente le chiffrement Caesar classique qui décale chaque lettre
de N positions dans l'alphabet. Il supporte :
- L'encodage et le décodage
- Le mode bruteforce (test de tous les décalages possibles)
- La détection de code Caesar
"""

import time
from typing import Dict, Any, List

try:
    from gc_backend.plugins.scoring import score_text_fast

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text_fast = None
    _SCORING_AVAILABLE = False


class CaesarPlugin:
    """
    Plugin de chiffrement/déchiffrement Caesar.
    
    Le chiffrement Caesar décale chaque lettre de N positions dans l'alphabet.
    Par exemple, avec un décalage de 3 : A → D, B → E, C → F, etc.
    """
    
    def __init__(self):
        self.name = "caesar"
        self.version = "1.0.0"
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Point d'entrée principal du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée contenant :
                - text (str): Texte à traiter
                - mode (str): 'encode', 'decode' ou 'detect'
                - shift (int): Décalage (1-25)
                - brute_force (bool, optionnel): Activer le mode bruteforce
                
        Returns:
            dict: Résultat au format standardisé
        """
        start_time = time.time()
        
        # Extraction des paramètres
        text = inputs.get('text', '')
        mode = inputs.get('mode', 'decode').lower()
        shift = int(inputs.get('shift', 13))
        brute_force = inputs.get('brute_force', False)
        
        # Validation
        if not text:
            return self._error_response("Aucun texte fourni")
        
        if shift < 1 or shift > 25:
            return self._error_response("Le décalage doit être entre 1 et 25")
        
        # Traitement selon le mode
        results = []
        
        if brute_force and mode == 'decode':
            # Mode bruteforce : tester tous les décalages
            results = self._bruteforce_decode(text)
        elif mode == 'encode':
            # Encodage simple
            encoded_text = self._caesar_shift(text, shift)
            results = [{
                "id": "result_1",
                "text_output": encoded_text,
                "confidence": 1.0,
                "parameters": {
                    "mode": "encode",
                    "shift": shift
                },
                "metadata": {
                    "processed_chars": len(text)
                }
            }]
        elif mode == 'decode':
            # Décodage simple
            decoded_text = self._caesar_shift(text, -shift)
            results = [{
                "id": "result_1",
                "text_output": decoded_text,
                "confidence": 0.5,  # Confiance neutre, sera réévaluée par le scoring
                "parameters": {
                    "mode": "decode",
                    "shift": shift
                },
                "metadata": {
                    "processed_chars": len(text)
                }
            }]
        elif mode == 'detect':
            # Mode détection : vérifier si le texte pourrait être du Caesar
            is_match, score = self._detect_caesar(text)
            
            return {
                "status": "ok",
                "summary": f"Code Caesar {'détecté' if is_match else 'non détecté'}",
                "results": [{
                    "id": "result_1",
                    "text_output": f"Probabilité Caesar: {score:.2%}",
                    "confidence": score,
                    "parameters": {"mode": "detect"},
                    "metadata": {
                        "is_match": is_match,
                        "detection_score": score
                    }
                }],
                "plugin_info": self._get_plugin_info(start_time)
            }
        else:
            return self._error_response(f"Mode inconnu: {mode}")
        
        # Construire réponse
        execution_time = (time.time() - start_time) * 1000
        
        return {
            "status": "ok",
            "summary": f"{len(results)} résultat(s) généré(s)",
            "results": results,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round(execution_time, 2)
            }
        }
    
    def _caesar_shift(self, text: str, shift: int) -> str:
        """
        Applique un décalage Caesar au texte.
        
        Args:
            text (str): Texte à décaler
            shift (int): Nombre de positions de décalage (positif ou négatif)
            
        Returns:
            str: Texte décalé
        """
        result = []
        
        for char in text:
            if char.isupper():
                # Lettre majuscule
                shifted = chr((ord(char) - ord('A') + shift) % 26 + ord('A'))
                result.append(shifted)
            elif char.islower():
                # Lettre minuscule
                shifted = chr((ord(char) - ord('a') + shift) % 26 + ord('a'))
                result.append(shifted)
            else:
                # Caractère non alphabétique : conserver tel quel
                result.append(char)
        
        return ''.join(result)
    
    @staticmethod
    def _get_score_fast(text: str) -> float:
        if not _SCORING_AVAILABLE or not score_text_fast:
            return 0.5
        try:
            return score_text_fast(text)
        except Exception:
            return 0.5

    def _bruteforce_decode(self, text: str) -> List[Dict]:
        """
        Teste tous les décalages possibles (1 à 25).
        
        Args:
            text (str): Texte chiffré
            
        Returns:
            List[Dict]: Liste de résultats pour chaque décalage
        """
        results = []
        
        for shift in range(1, 26):
            decoded_text = self._caesar_shift(text, -shift)
            confidence = self._get_score_fast(decoded_text)
            
            results.append({
                "id": f"result_{shift}",
                "text_output": decoded_text,
                "confidence": confidence,
                "parameters": {
                    "mode": "decode",
                    "shift": shift
                },
                "metadata": {
                    "bruteforce": True,
                    "shift_tested": shift
                }
            })
        
        results.sort(key=lambda r: r["confidence"], reverse=True)
        
        return results
    
    def _detect_caesar(self, text: str) -> tuple:
        """
        Détecte si le texte pourrait être du code Caesar.
        
        Heuristique simple : vérifier si le texte contient principalement
        des lettres (les chiffres Caesar préservent la structure alphabétique).
        
        Args:
            text (str): Texte à analyser
            
        Returns:
            tuple: (is_match: bool, confidence: float)
        """
        if not text:
            return False, 0.0
        
        # Compter les lettres
        letter_count = sum(1 for c in text if c.isalpha())
        total_chars = len(text.strip())
        
        if total_chars == 0:
            return False, 0.0
        
        # Ratio de lettres
        letter_ratio = letter_count / total_chars
        
        # Si plus de 70% de lettres, probablement du texte (donc potentiellement Caesar)
        is_match = letter_ratio > 0.7
        confidence = min(letter_ratio, 0.8)  # Plafond à 0.8 car c'est juste une heuristique
        
        return is_match, confidence
    
    def _error_response(self, message: str) -> Dict:
        """
        Construit une réponse d'erreur standardisée.
        
        Args:
            message (str): Message d'erreur
            
        Returns:
            dict: Réponse d'erreur au format standardisé
        """
        return {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": 0
            }
        }
    
    def _get_plugin_info(self, start_time: float) -> Dict:
        """
        Construit les informations du plugin.
        
        Args:
            start_time (float): Timestamp de début d'exécution
            
        Returns:
            dict: Informations du plugin
        """
        execution_time = (time.time() - start_time) * 1000
        
        return {
            "name": self.name,
            "version": self.version,
            "execution_time_ms": round(execution_time, 2)
        }
