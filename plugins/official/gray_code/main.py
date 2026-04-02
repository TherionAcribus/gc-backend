"""
Plugin Gray Code pour MysterAI.

Ce plugin implémente la conversion entre code binaire standard et code Gray
(code binaire réfléchi). Le code Gray est un système de numération binaire
où deux valeurs successives ne diffèrent que d'un seul bit.

Conversions supportées:
- Binaire → Gray (encode)
- Gray → Binaire (decode)

Formats d'entrée/sortie:
- Binaire (séquences de 0 et 1)
- Décimal (nombres)
- ASCII (caractères)

Référence: https://en.wikipedia.org/wiki/Gray_code
"""

import time
import re
from typing import Dict, Any, List, Tuple, Optional


class GrayCodePlugin:
    """
    Plugin de conversion Gray Code.
    
    Le code Gray (ou code binaire réfléchi) est un code binaire où deux
    valeurs consécutives ne diffèrent que d'un seul bit. Il est utilisé
    pour minimiser les erreurs lors des transitions.
    """
    
    def __init__(self):
        self.name = "gray_code"
        self.version = "1.0.0"
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Point d'entrée principal du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée contenant :
                - text (str): Texte à traiter
                - mode (str): 'encode' (binary→gray), 'decode' (gray→binary), 'detect'
                
        Returns:
            dict: Résultat au format standardisé avec sorties en binaire, décimal et ASCII
        """
        start_time = time.time()
        
        text = inputs.get('text', '')
        mode = inputs.get('mode', 'decode').lower()
        
        if not text:
            return self._error_response("Aucun texte fourni")
        
        if mode == 'detect':
            is_match, score, details = self._detect(text)
            return {
                "status": "ok",
                "summary": f"Code Gray {'possible' if is_match else 'peu probable'}",
                "results": [{
                    "id": "result_1",
                    "text_output": f"Probabilité code Gray: {score:.2%}",
                    "confidence": score,
                    "parameters": {"mode": "detect"},
                    "metadata": {
                        "is_match": is_match,
                        "detection_score": score,
                        **details
                    }
                }],
                "plugin_info": self._get_plugin_info(start_time)
            }
        
        try:
            if mode == 'encode':
                results = self._encode_auto(text)
            elif mode == 'decode':
                results = self._decode_auto(text)
            else:
                return self._error_response(f"Mode inconnu: {mode}")
        except ValueError as e:
            return self._error_response(str(e))
        
        execution_time = (time.time() - start_time) * 1000
        
        return {
            "status": "ok",
            "summary": f"Conversion {mode} réussie",
            "results": results,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round(execution_time, 2)
            }
        }
    
    @staticmethod
    def binary_to_gray(n: int) -> int:
        """
        Convertit un nombre binaire en code Gray.
        
        Formule: gray = n ^ (n >> 1)
        
        Args:
            n (int): Nombre en binaire standard
            
        Returns:
            int: Nombre en code Gray
        """
        return n ^ (n >> 1)
    
    @staticmethod
    def gray_to_binary(gray: int) -> int:
        """
        Convertit un code Gray en nombre binaire.
        
        Algorithme: XOR cumulatif des bits du plus significatif au moins significatif.
        
        Args:
            gray (int): Nombre en code Gray
            
        Returns:
            int: Nombre en binaire standard
        """
        mask = gray
        while mask:
            mask >>= 1
            gray ^= mask
        return gray
    
    def _detect_input_format_for_encode(self, text: str) -> Tuple[str, List[int], int]:
        """
        Auto-détecte le format d'entrée pour l'encodage.
        
        Priorité de détection:
        1. Binaire pur (uniquement 0 et 1 avec séparateurs)
        2. Décimal (nombres séparés)
        3. ASCII (texte)
        
        Args:
            text: Texte d'entrée
            
        Returns:
            tuple: (format_détecté, liste_valeurs, bit_length)
        """
        text = text.strip()
        
        binary_sequences = re.findall(r'[01]+', text)
        if binary_sequences:
            cleaned = re.sub(r'[\s,;|\-]+', '', text)
            if all(c in '01' for c in cleaned):
                values = [int(seq, 2) for seq in binary_sequences if seq]
                if values:
                    bit_length = max(len(seq) for seq in binary_sequences)
                    return 'binary', values, bit_length
        
        decimal_matches = re.findall(r'\d+', text)
        if decimal_matches:
            non_digit_chars = re.sub(r'[\d\s,;|\-]+', '', text)
            if not non_digit_chars:
                values = [int(d) for d in decimal_matches]
                max_val = max(values) if values else 0
                bit_length = max(8, max_val.bit_length()) if max_val > 0 else 8
                return 'decimal', values, bit_length
        
        values = [ord(c) for c in text]
        max_val = max(values) if values else 0
        bit_length = max(8, max_val.bit_length()) if max_val > 0 else 8
        return 'ascii', values, bit_length
    
    def _encode_auto(self, text: str) -> List[Dict]:
        """
        Encode avec auto-détection: Binaire standard → Code Gray
        
        Détecte automatiquement le format d'entrée (binaire, décimal ou ASCII)
        et produit le code Gray en sortie (binaire).
        
        Args:
            text: Texte d'entrée
            
        Returns:
            Liste de résultats (Gray Code en binaire)
        """
        detected_format, values, bit_length = self._detect_input_format_for_encode(text)
        
        if not values:
            raise ValueError("Aucune valeur valide trouvée dans l'entrée")
        
        gray_values = [self.binary_to_gray(v) for v in values]
        binary_output = ' '.join(format(g, f'0{bit_length}b') for g in gray_values)
        
        results = [
            {
                "id": "result_gray",
                "text_output": binary_output,
                "confidence": 1.0,
                "parameters": {"mode": "encode"},
                "metadata": {
                    "detected_input_format": detected_format,
                    "detected_bit_length": bit_length,
                    "values_count": len(values)
                }
            }
        ]
        
        return results
    
    def _detect_input_format(self, text: str) -> Tuple[str, List[int], int]:
        """
        Auto-détecte le format d'entrée et extrait les valeurs Gray.
        
        Args:
            text: Texte d'entrée
            
        Returns:
            tuple: (format_détecté, liste_valeurs, bit_length_détecté)
        """
        text = text.strip()
        
        binary_sequences = re.findall(r'[01]+', text)
        if binary_sequences:
            only_binary = re.sub(r'[^01\s,;|\-]', '', text)
            if len(only_binary.replace(' ', '').replace(',', '').replace(';', '').replace('|', '').replace('-', '')) == len(re.sub(r'[\s,;|\-]', '', text)):
                values = []
                bit_lengths = []
                for seq in binary_sequences:
                    if seq:
                        values.append(int(seq, 2))
                        bit_lengths.append(len(seq))
                if values:
                    bit_length = max(bit_lengths) if bit_lengths else 8
                    return 'binary', values, bit_length
        
        decimal_matches = re.findall(r'\d+', text)
        if decimal_matches:
            values = [int(d) for d in decimal_matches]
            max_val = max(values) if values else 0
            bit_length = max(8, max_val.bit_length()) if max_val > 0 else 8
            return 'decimal', values, bit_length
        
        raise ValueError("Impossible de détecter le format d'entrée. Utilisez des valeurs binaires (ex: 01101100) ou décimales (ex: 108).")
    
    def _decode_auto(self, text: str) -> List[Dict]:
        """
        Decode avec auto-détection: Code Gray → Binaire standard
        
        Détecte automatiquement:
        - Le format d'entrée (binaire ou décimal)
        - La longueur des bits
        
        Args:
            text: Texte d'entrée (en code Gray)
            
        Returns:
            Liste de résultats
        """
        detected_format, gray_values, bit_length = self._detect_input_format(text)
        
        if not gray_values:
            raise ValueError("Aucune valeur valide trouvée dans l'entrée")
        
        binary_values = [self.gray_to_binary(g) for g in gray_values]
        
        binary_output = ' '.join(format(b, f'0{bit_length}b') for b in binary_values)
        decimal_output = ' '.join(str(b) for b in binary_values)
        
        ascii_output = ""
        ascii_valid = True
        for b in binary_values:
            if 32 <= b <= 126:
                ascii_output += chr(b)
            elif b == 10 or b == 13:
                ascii_output += chr(b)
            else:
                ascii_valid = False
                ascii_output += f"[{b}]"
        
        results = [
            {
                "id": "result_decimal",
                "text_output": decimal_output,
                "confidence": 1.0,
                "parameters": {"mode": "decode", "output_format": "decimal"},
                "metadata": {
                    "detected_input_format": detected_format,
                    "detected_bit_length": bit_length,
                    "values_count": len(gray_values)
                }
            },
            {
                "id": "result_ascii",
                "text_output": ascii_output,
                "confidence": 0.95 if ascii_valid else 0.4,
                "parameters": {"mode": "decode", "output_format": "ascii"},
                "metadata": {
                    "detected_input_format": detected_format,
                    "ascii_valid": ascii_valid,
                    "values_count": len(gray_values)
                }
            },
            {
                "id": "result_binary",
                "text_output": binary_output,
                "confidence": 1.0,
                "parameters": {"mode": "decode", "output_format": "binary"},
                "metadata": {
                    "detected_input_format": detected_format,
                    "detected_bit_length": bit_length,
                    "values_count": len(gray_values)
                }
            }
        ]
        
        return results
    
    def _detect(self, text: str) -> Tuple[bool, float, Dict]:
        """
        Détecte si le texte pourrait être du code Gray.
        
        Heuristique: vérifie si l'entrée contient des séquences binaires
        ou des nombres qui pourraient être du code Gray.
        
        Args:
            text: Texte à analyser
            
        Returns:
            tuple: (is_match, confidence, details)
        """
        if not text:
            return False, 0.0, {}
        
        binary_pattern = re.findall(r'\b[01]{4,}\b', text)
        decimal_pattern = re.findall(r'\b\d+\b', text)
        
        if binary_pattern:
            total_bits = sum(len(p) for p in binary_pattern)
            is_match = len(binary_pattern) >= 1 and total_bits >= 8
            confidence = min(0.7, len(binary_pattern) * 0.1 + total_bits * 0.01)
            return is_match, confidence, {
                "binary_sequences": len(binary_pattern),
                "total_bits": total_bits,
                "detected_format": "binary"
            }
        elif decimal_pattern:
            valid_decimals = [int(d) for d in decimal_pattern if int(d) < 256]
            is_match = len(valid_decimals) >= 2
            confidence = min(0.5, len(valid_decimals) * 0.05)
            return is_match, confidence, {
                "decimal_values": len(decimal_pattern),
                "valid_byte_values": len(valid_decimals),
                "detected_format": "decimal"
            }
        
        return False, 0.1, {"detected_format": "unknown"}
    
    def _error_response(self, message: str) -> Dict:
        """
        Construit une réponse d'erreur standardisée.
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
        """
        execution_time = (time.time() - start_time) * 1000
        return {
            "name": self.name,
            "version": self.version,
            "execution_time_ms": round(execution_time, 2)
        }
