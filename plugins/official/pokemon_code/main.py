"""
Plugin Pokemon Code pour MysterAI.

Ce plugin implémente le code Pokémon utilisé en géocaching.
Chaque lettre est représentée par une syllabe issue des cris/sons des Pokémon.

Mapping:
A=PI, B=MON, C=FLA, D=LU, E=SA, F=ME, G=AR, H=KA, I=FLOR, J=CHU,
K=MAN, L=KAR, M=SON, N=TU, O=SAM, P=REG, Q=PA, R=KLA, S=SE, T=DA,
U=LUFF, V=AS, W=MO, X=GE, Y=TRON, Z=ZU

Source: GC Wizard (https://github.com/S-Man42/GCWizard)
Référence: https://blog.gcwizard.net/manual/en/pokemon/00-what-is-the-pokemon-code/
"""

import time
import re
from typing import Dict, Any, List, Tuple

try:
    from gc_backend.plugins.scoring import score_text_fast
    _SCORING_AVAILABLE = True
except Exception:
    score_text_fast = None
    _SCORING_AVAILABLE = False


class PokemonCodePlugin:
    """
    Plugin de chiffrement/déchiffrement du code Pokémon.
    
    Le code Pokémon utilise des syllabes issues des cris des Pokémon
    pour représenter les lettres de l'alphabet.
    """
    
    ENCODE_MAP = {
        'a': 'pi',
        'b': 'mon',
        'c': 'fla',
        'd': 'lu',
        'e': 'sa',
        'f': 'me',
        'g': 'ar',
        'h': 'ka',
        'i': 'flor',
        'j': 'chu',
        'k': 'man',
        'l': 'kar',
        'm': 'son',
        'n': 'tu',
        'o': 'sam',
        'p': 'reg',
        'q': 'pa',
        'r': 'kla',
        's': 'se',
        't': 'da',
        'u': 'luff',
        'v': 'as',
        'w': 'mo',
        'x': 'ge',
        'y': 'tron',
        'z': 'zu',
        ' ': ' ',
    }
    
    DECODE_PATTERNS = [
        ('same', 'ef'),
        ('saman', 'ek'),
        ('samon', 'eb'),
        ('samo', 'ew'),
        ('sasam', 'eo'),
        ('klason', 'rm'),
        ('klase', 'rs'),
        ('kason', 'hm'),
        ('kase', 'hs'),
        ('kasam', 'he'),
        ('kasa', 'he'),
        ('flason', 'cm'),
        ('flase', 'cs'),
        ('flasa', 'ce'),
        ('sason', 'em'),
        ('sase', 'es'),
        ('sasa', 'ee'),
        ('dason', 'tm'),
        ('dase', 'ts'),
        ('dasa', 'te'),
        ('pason', 'qm'),
        ('pase', 'qs'),
        ('pasa', 'qe'),
        ('as', 'v'),
        ('pareg', 'qp'),
        ('kareg', 'hp'),
        ('klareg', 'rp'),
        ('flareg', 'cp'),
        ('sareg', 'ep'),
        ('dareg', 'tp'),
        ('reg', 'p'),
        ('kar', 'l'),
        ('kas', 'h'),
        ('kla', 'r'),
        ('ka', 'h'),
        ('fla', 'c'),
        ('sam', 'o'),
        ('sa', 'e'),
        ('da', 't'),
        ('pa', 'q'),
        ('ar', 'g'),
        ('zu', 'z'),
        ('tu', 'n'),
        ('tron', 'y'),
        ('son', 'm'),
        ('se', 's'),
        ('pi', 'a'),
        ('luff', 'u'),
        ('lu', 'd'),
        ('flor', 'i'),
        ('chu', 'j'),
        ('mon', 'b'),
        ('mo', 'w'),
        ('me', 'f'),
        ('man', 'k'),
        ('ge', 'x'),
    ]
    
    POKEMON_SYLLABLES = {'pi', 'mon', 'fla', 'lu', 'sa', 'me', 'ar', 'ka', 'flor', 
                         'chu', 'man', 'kar', 'son', 'tu', 'sam', 'reg', 'pa', 'kla', 
                         'se', 'da', 'luff', 'as', 'mo', 'ge', 'tron', 'zu'}
    
    def __init__(self):
        self.name = "pokemon_code"
        self.version = "1.0.0"
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Point d'entrée principal du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée contenant :
                - text (str): Texte à traiter
                - mode (str): 'encode', 'decode' ou 'detect'
                
        Returns:
            dict: Résultat au format standardisé
        """
        start_time = time.time()
        
        text = inputs.get('text', '')
        mode = inputs.get('mode', 'decode').lower()
        
        if not text:
            return self._error_response("Aucun texte fourni")
        
        results = []
        
        if mode == 'encode':
            encoded_text = self._encode(text)
            results = [{
                "id": "result_1",
                "text_output": encoded_text,
                "confidence": 1.0,
                "parameters": {"mode": "encode"},
                "metadata": {
                    "processed_chars": len(text),
                    "output_length": len(encoded_text)
                }
            }]
        elif mode == 'decode':
            decoded_text = self._decode(text)
            confidence = self._calculate_confidence(decoded_text)
            results = [{
                "id": "result_1",
                "text_output": decoded_text,
                "confidence": confidence,
                "parameters": {"mode": "decode"},
                "metadata": {
                    "processed_chars": len(text),
                    "output_length": len(decoded_text)
                }
            }]
        elif mode == 'detect':
            is_match, score, details = self._detect(text)
            return {
                "status": "ok",
                "summary": f"Code Pokémon {'détecté' if is_match else 'non détecté'}",
                "results": [{
                    "id": "result_1",
                    "text_output": f"Probabilité code Pokémon: {score:.2%}",
                    "confidence": score,
                    "parameters": {"mode": "detect"},
                    "metadata": {
                        "is_match": is_match,
                        "detection_score": score,
                        "syllables_found": details.get('syllables_found', []),
                        "syllable_ratio": details.get('syllable_ratio', 0)
                    }
                }],
                "plugin_info": self._get_plugin_info(start_time)
            }
        else:
            return self._error_response(f"Mode inconnu: {mode}")
        
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
    
    def _encode(self, text: str) -> str:
        """
        Encode un texte en code Pokémon.
        
        Args:
            text (str): Texte clair à encoder
            
        Returns:
            str: Texte encodé en syllabes Pokémon
        """
        result = []
        for char in text.lower():
            if char in self.ENCODE_MAP:
                result.append(self.ENCODE_MAP[char])
            else:
                result.append(char)
        return ''.join(result).upper()
    
    def _decode(self, text: str) -> str:
        """
        Décode un texte en code Pokémon.
        
        Args:
            text (str): Texte encodé en syllabes Pokémon
            
        Returns:
            str: Texte décodé
        """
        text = text.lower()
        text = re.sub(r'\s+', ' ', text)
        
        words = text.split(' ')
        decoded_words = []
        
        for word in words:
            if word:
                decoded_word = self._decode_word(word)
                decoded_words.append(decoded_word)
        
        return ' '.join(decoded_words).upper()
    
    def _decode_word(self, word: str) -> str:
        """
        Décode un mot unique en code Pokémon.
        
        Args:
            word (str): Mot encodé
            
        Returns:
            str: Mot décodé
        """
        cypher = word.lower()
        result = ''
        max_iterations = len(cypher) + 1
        iteration = 0
        
        if len(cypher) == 1:
            return '?'
        
        while cypher and iteration < max_iterations:
            iteration += 1
            found = False
            
            for pattern, replacement in self.DECODE_PATTERNS:
                if cypher.startswith(pattern):
                    cypher = cypher[len(pattern):]
                    result += replacement
                    found = True
                    break
            
            if not found:
                if cypher:
                    result += '?'
                    cypher = cypher[1:]
        
        if not result or cypher:
            return '?'
        
        return result
    
    def _detect(self, text: str) -> Tuple[bool, float, Dict]:
        """
        Détecte si le texte pourrait être du code Pokémon.
        
        Args:
            text (str): Texte à analyser
            
        Returns:
            tuple: (is_match: bool, confidence: float, details: dict)
        """
        if not text:
            return False, 0.0, {}
        
        text_lower = text.lower()
        text_clean = re.sub(r'[^a-z]', '', text_lower)
        
        if not text_clean:
            return False, 0.0, {}
        
        syllables_found = []
        remaining = text_clean
        total_matched_length = 0
        
        sorted_syllables = sorted(self.POKEMON_SYLLABLES, key=len, reverse=True)
        
        temp_remaining = remaining
        while temp_remaining:
            found = False
            for syllable in sorted_syllables:
                if temp_remaining.startswith(syllable):
                    syllables_found.append(syllable)
                    total_matched_length += len(syllable)
                    temp_remaining = temp_remaining[len(syllable):]
                    found = True
                    break
            if not found:
                temp_remaining = temp_remaining[1:]
        
        syllable_ratio = total_matched_length / len(text_clean) if text_clean else 0
        
        is_match = syllable_ratio > 0.7 and len(syllables_found) >= 2
        confidence = min(syllable_ratio, 0.95)
        
        return is_match, confidence, {
            'syllables_found': syllables_found[:10],
            'syllable_ratio': round(syllable_ratio, 3),
            'total_syllables': len(syllables_found)
        }
    
    def _calculate_confidence(self, decoded_text: str) -> float:
        """
        Calcule la confiance du décodage.
        
        Args:
            decoded_text (str): Texte décodé
            
        Returns:
            float: Score de confiance
        """
        if not decoded_text:
            return 0.0
        
        unknown_count = decoded_text.count('?')
        total_chars = len(decoded_text.replace(' ', ''))
        
        if total_chars == 0:
            return 0.0
        
        valid_ratio = 1 - (unknown_count / total_chars)
        
        if _SCORING_AVAILABLE and score_text_fast:
            try:
                clean_text = decoded_text.replace('?', '')
                if clean_text:
                    lang_score = score_text_fast(clean_text)
                    return (valid_ratio * 0.5) + (lang_score * 0.5)
            except Exception:
                pass
        
        return valid_ratio * 0.8
    
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
