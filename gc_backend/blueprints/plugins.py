"""
Blueprint pour les endpoints API des plugins.

Ce module expose les routes REST pour :
- Lister les plugins disponibles
- RÃ©cupÃ©rer les informations d'un plugin
- GÃ©nÃ©rer l'interface HTML d'un plugin
- ExÃ©cuter un plugin (mode synchrone)
- ExÃ©cuter des plugins en mode batch
- RedÃ©clencher la dÃ©couverte de plugins
"""

import html
import json
import re
import threading
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template_string, current_app
from loguru import logger
from typing import Dict, Any, List, Optional

from ..plugins import PluginManager
from ..database import db
from ..geocaches.models import Geocache


# CrÃ©er le blueprint
bp = Blueprint('plugins', __name__, url_prefix='/api/plugins')

# Instance globale du PluginManager (sera initialisÃ©e dans create_app)
_plugin_manager: PluginManager = None


def init_plugin_manager(manager: PluginManager):
    """
    Initialise le PluginManager global pour ce blueprint.
    
    Cette fonction doit Ãªtre appelÃ©e depuis create_app() aprÃ¨s
    la crÃ©ation du PluginManager.
    
    Args:
        manager (PluginManager): Instance du gestionnaire de plugins
    """
    global _plugin_manager
    _plugin_manager = manager
    logger.info("PluginManager initialisÃ© dans le blueprint plugins")


# Stockage des tÃ¢ches batch en mÃ©moire (en production, utiliser Redis ou une base de donnÃ©es)
batch_tasks: Dict[str, 'BatchPluginTask'] = {}

CHEMICAL_SYMBOLS = frozenset({
    'H', 'HE', 'LI', 'BE', 'B', 'C', 'N', 'O', 'F', 'NE',
    'NA', 'MG', 'AL', 'SI', 'P', 'S', 'CL', 'AR', 'K', 'CA',
    'SC', 'TI', 'V', 'CR', 'MN', 'FE', 'CO', 'NI', 'CU', 'ZN',
    'GA', 'GE', 'AS', 'SE', 'BR', 'KR', 'RB', 'SR', 'Y', 'ZR',
    'NB', 'MO', 'TC', 'RU', 'RH', 'PD', 'AG', 'CD', 'IN', 'SN',
    'SB', 'TE', 'I', 'XE', 'CS', 'BA', 'LA', 'CE', 'PR', 'ND',
    'PM', 'SM', 'EU', 'GD', 'TB', 'DY', 'HO', 'ER', 'TM', 'YB',
    'LU', 'HF', 'TA', 'W', 'RE', 'OS', 'IR', 'PT', 'AU', 'HG',
    'TL', 'PB', 'BI', 'PO', 'AT', 'RN', 'FR', 'RA', 'AC', 'TH',
    'PA', 'U', 'NP', 'PU', 'AM', 'CM', 'BK', 'CF', 'ES', 'FM',
    'MD', 'NO', 'LR', 'RF', 'DB', 'SG', 'BH', 'HS', 'MT', 'DS',
    'RG', 'CN', 'NH', 'FL', 'MC', 'LV', 'TS', 'OG',
})

HOUDINI_WORDS = frozenset({
    'PRAY', 'ANSWER', 'SAY', 'NOW', 'TELL',
    'PLEASE', 'SPEAK', 'QUICKLY', 'LOOK', 'BE QUICK',
})

NAK_NAK_WORDS = frozenset({
    'NAK', 'NANAK', 'NANANAK', 'NANANANAK',
    'NAK?', 'NAKNAK', 'NAKNAKNAK', 'NAK.',
    'NAKNAK.', 'NAKNAKNAKNAK', 'NAK!',
})

SHADOK_SYLLABLE_PATTERN = re.compile(r'^(?:GA|BU|ZO|MEU|ME)+$', re.IGNORECASE)
TOM_TOM_TOKEN_PATTERN = re.compile(r'^[\\/]{1,5}$')
GOLD_BUG_SYMBOLS = frozenset('0123456789-*,.$();?:[]')


def _load_metasolver_presets(manager: PluginManager) -> Dict[str, Any]:
    presets_path = Path(manager.plugins_dir) / 'official' / 'metasolver' / 'presets.json'
    try:
        with presets_path.open('r', encoding='utf-8') as handle:
            return json.load(handle).get('presets') or {}
    except Exception:
        return {}


def _matches_metasolver_filter(metasolver_meta: Dict[str, Any], preset_filter: Optional[Dict[str, Any]]) -> bool:
    if not preset_filter:
        return True

    filter_tags = preset_filter.get('tags')
    if filter_tags:
        plugin_tags = set(metasolver_meta.get('tags') or [])
        if not plugin_tags.intersection(filter_tags):
            return False

    filter_charsets = preset_filter.get('input_charset')
    if filter_charsets:
        plugin_charset = metasolver_meta.get('input_charset', '')
        if plugin_charset not in filter_charsets:
            return False

    return True


def _collect_metasolver_candidates(
    *,
    preset_filter: Optional[Dict[str, Any]] = None,
    mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    from ..plugins.models import Plugin as PluginModel

    all_plugins = PluginModel.query.filter_by(enabled=True).all()
    candidates: List[Dict[str, Any]] = []

    for plugin in all_plugins:
        try:
            metadata = json.loads(plugin.metadata_json) if plugin.metadata_json else {}
        except Exception:
            continue

        metasolver_meta = metadata.get('metasolver') or {}
        if not metasolver_meta.get('eligible'):
            continue

        capabilities = metadata.get('capabilities') or {}
        if mode == 'detect' and not capabilities.get('analyze'):
            continue
        if mode == 'decode' and not capabilities.get('decode'):
            continue

        if not _matches_metasolver_filter(metasolver_meta, preset_filter):
            continue

        priority = metasolver_meta.get('priority', 50)
        try:
            priority = int(priority)
        except Exception:
            priority = 50

        candidates.append({
            'name': plugin.name,
            'description': plugin.description or '',
            'input_charset': metasolver_meta.get('input_charset') or '',
            'tags': list(metasolver_meta.get('tags') or []),
            'priority': priority,
            'capabilities': capabilities,
            'family': metasolver_meta.get('family'),
            'preferred_when': list(metasolver_meta.get('preferred_when') or []),
            'requires_key': bool(metasolver_meta.get('requires_key', False)),
            'supports_grouped_input': bool(metasolver_meta.get('supports_grouped_input', False)),
        })

    candidates.sort(key=lambda item: (-item['priority'], item['name']))
    return candidates


def _detect_dominant_input_kind(letter_count: int, digit_count: int, symbol_count: int, word_count: int) -> str:
    present = [name for name, value in (
        ('letters', letter_count),
        ('digits', digit_count),
        ('symbols', symbol_count),
    ) if value > 0]

    if not present:
        return 'empty'
    if len(present) == 1 and present[0] == 'letters' and word_count >= 2:
        return 'words'
    if len(present) == 1:
        return present[0]
    return 'mixed'


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value == 2:
        return True
    if value % 2 == 0:
        return False

    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2

    return True


def _normalize_postnet_candidate(text: str) -> Optional[str]:
    compact = ''.join(char for char in (text or '') if not char.isspace())
    if not compact:
        return None

    if set(compact) <= set('01'):
        return compact

    normalized: List[str] = []
    for char in compact:
        if char in '|I':
            normalized.append('1')
            continue
        if char in '.-_':
            normalized.append('0')
            continue
        return None

    return ''.join(normalized)


def _analyze_metasolver_signature(text: str) -> Dict[str, Any]:
    raw_text = text or ''
    trimmed = raw_text.strip()
    non_space = [char for char in trimmed if not char.isspace()]
    compact = ''.join(non_space)
    compact_upper = compact.upper()
    tokens = [token for token in re.split(r'\s+', trimmed) if token]

    letter_count = sum(1 for char in non_space if char.isalpha())
    digit_count = sum(1 for char in non_space if char.isdigit())
    symbol_count = sum(1 for char in non_space if not char.isalnum())
    whitespace_count = sum(1 for char in trimmed if char.isspace())
    total_non_space = len(non_space)

    charsets_present = [name for name, value in (
        ('letters', letter_count),
        ('digits', digit_count),
        ('symbols', symbol_count),
    ) if value > 0]

    word_count = len([token for token in tokens if any(char.isalpha() for char in token)])
    average_token_length = (
        round(sum(len(token) for token in tokens) / len(tokens), 2)
        if tokens else 0.0
    )
    separators = sorted({char for char in trimmed if not char.isalnum() and not char.isspace()})

    binary_candidate = re.sub(r'[\s|,;:_/-]+', '', trimmed)
    hex_candidate = re.sub(r'[\s|,;:_/-]+', '', compact_upper)
    digit_candidate = re.sub(r'\D', '', trimmed)
    bacon_candidate = re.sub(r'[\s|,;:_/-]+', '', compact_upper)
    numeric_tokens = [int(token) for token in tokens if re.fullmatch(r'\d+', token)]
    alpha_tokens_upper = [token.upper() for token in tokens if token.isalpha()]
    stripped_tokens = [re.sub(r'^[^\w?!.]+|[^\w?!.]+$', '', token) for token in tokens]
    normalized_word_tokens = [token.upper() for token in stripped_tokens if any(char.isalpha() for char in token)]
    tom_tom_tokens = [token for token in re.split(r'[\s.:;,_-]+', trimmed) if token]
    postnet_candidate = _normalize_postnet_candidate(trimmed)

    merged_houdini_tokens: List[str] = []
    index = 0
    while index < len(normalized_word_tokens):
        token = normalized_word_tokens[index]
        if token == 'BE' and index + 1 < len(normalized_word_tokens) and normalized_word_tokens[index + 1] == 'QUICK':
            merged_houdini_tokens.append('BE QUICK')
            index += 2
            continue
        merged_houdini_tokens.append(token)
        index += 1

    looks_like_morse = bool(compact) and set(compact) <= set('.-/|') and any(char in compact for char in '.-')
    looks_like_binary = bool(binary_candidate) and len(binary_candidate) >= 6 and set(binary_candidate) <= set('01')
    looks_like_hex = (
        bool(hex_candidate)
        and len(hex_candidate) >= 4
        and set(hex_candidate) <= set('0123456789ABCDEF')
        and any(char in 'ABCDEF' for char in hex_candidate)
    )
    looks_like_phone_keypad = bool(digit_candidate) and len(digit_candidate) >= 4 and set(digit_candidate) <= set('23456789')
    looks_like_roman = (
        bool(compact_upper)
        and letter_count > 0
        and digit_count == 0
        and symbol_count == 0
        and set(compact_upper) <= set('IVXLCDM')
        and len(compact_upper) >= 2
    )
    looks_like_decimal_sequence = digit_count > 0 and letter_count == 0 and len(tokens) >= 2
    looks_like_a1z26 = (
        len(numeric_tokens) >= 2
        and len(numeric_tokens) == len(tokens)
        and all(1 <= token <= 26 for token in numeric_tokens)
    )
    looks_like_tap_code = (
        len(tokens) >= 4
        and len(tokens) % 2 == 0
        and all(re.fullmatch(r'[1-5]', token) for token in tokens)
    ) or bool(re.search(r'(?:X+|\.+)\s+(?:X+|\.+)', trimmed))
    looks_like_polybius = len(tokens) >= 2 and all(re.fullmatch(r'[1-6]{2}', token) for token in tokens)
    looks_like_multitap = (
        len(tokens) >= 2
        and all(re.fullmatch(r'([2-9])\1{0,3}', token) for token in tokens)
        and any(len(token) > 1 for token in tokens)
    )
    looks_like_chemical_symbols = (
        len(alpha_tokens_upper) >= 2
        and len(alpha_tokens_upper) == len(tokens)
        and all(token in CHEMICAL_SYMBOLS for token in alpha_tokens_upper)
    )
    looks_like_houdini_words = (
        len(merged_houdini_tokens) >= 2
        and len(merged_houdini_tokens) == len(normalized_word_tokens)
        and all(token in HOUDINI_WORDS for token in merged_houdini_tokens)
    )
    looks_like_nak_nak = (
        len(normalized_word_tokens) >= 2
        and len(normalized_word_tokens) == len(tokens)
        and all(token in NAK_NAK_WORDS for token in normalized_word_tokens)
    )
    looks_like_shadok = (
        len(normalized_word_tokens) >= 2
        and len(normalized_word_tokens) == len(tokens)
        and all(SHADOK_SYLLABLE_PATTERN.fullmatch(token) for token in normalized_word_tokens)
    )
    looks_like_tom_tom = (
        len(tom_tom_tokens) >= 2
        and all(TOM_TOM_TOKEN_PATTERN.fullmatch(token) for token in tom_tom_tokens)
        and any('\\' in token for token in tom_tom_tokens)
    )
    looks_like_gold_bug = (
        len(non_space) >= 5
        and letter_count == 0
        and all(char in GOLD_BUG_SYMBOLS for char in non_space)
        and len({char for char in non_space if not char.isdigit()}) >= 2
    )
    looks_like_postnet = False
    if postnet_candidate and len(postnet_candidate) >= 12:
        data_portion = (
            postnet_candidate[1:-1]
            if len(postnet_candidate) >= 2 and postnet_candidate.startswith('1') and postnet_candidate.endswith('1')
            else postnet_candidate
        )
        if len(data_portion) >= 10 and len(data_portion) % 5 == 0:
            chunks = [data_portion[index:index + 5] for index in range(0, len(data_portion), 5)]
            looks_like_postnet = all(chunk.count('1') == 2 for chunk in chunks)
    looks_like_prime_sequence = (
        len(numeric_tokens) >= 3
        and len(numeric_tokens) == len(tokens)
        and all(_is_prime(token) for token in numeric_tokens)
    )
    looks_like_bacon = (
        bool(bacon_candidate)
        and len(bacon_candidate) >= 10
        and len(bacon_candidate) % 5 == 0
        and set(bacon_candidate) <= set('AB')
    )
    looks_like_coordinate_fragment = bool(re.search(r'[NSEW]\s*\d|[0-9]+\s*[Â°Âº]|[0-9]+[.,][0-9]+', trimmed, re.IGNORECASE))

    dominant_input_kind = _detect_dominant_input_kind(letter_count, digit_count, symbol_count, word_count)

    if looks_like_postnet or looks_like_gold_bug:
        suggested_preset = 'all'
    elif looks_like_morse:
        suggested_preset = 'symbols_only'
    elif looks_like_tom_tom:
        suggested_preset = 'symbols_only'
    elif looks_like_houdini_words or looks_like_nak_nak or looks_like_shadok or looks_like_chemical_symbols:
        suggested_preset = 'words_only'
    elif dominant_input_kind == 'digits':
        suggested_preset = 'digits_only'
    elif dominant_input_kind == 'symbols':
        suggested_preset = 'symbols_only'
    elif dominant_input_kind == 'words':
        suggested_preset = 'words_only'
    elif dominant_input_kind == 'letters':
        suggested_preset = 'letters_only'
    else:
        suggested_preset = 'frequent'

    return {
        'raw_length': len(raw_text),
        'trimmed_length': len(trimmed),
        'non_space_length': total_non_space,
        'letter_count': letter_count,
        'digit_count': digit_count,
        'symbol_count': symbol_count,
        'whitespace_count': whitespace_count,
        'word_count': word_count,
        'group_count': len(tokens),
        'average_group_length': average_token_length,
        'charsets_present': charsets_present,
        'dominant_input_kind': dominant_input_kind,
        'separators': separators,
        'looks_like_morse': looks_like_morse,
        'looks_like_binary': looks_like_binary,
        'looks_like_hex': looks_like_hex,
        'looks_like_phone_keypad': looks_like_phone_keypad,
        'looks_like_roman_numerals': looks_like_roman,
        'looks_like_decimal_sequence': looks_like_decimal_sequence,
        'looks_like_a1z26': looks_like_a1z26,
        'looks_like_tap_code': looks_like_tap_code,
        'looks_like_polybius': looks_like_polybius,
        'looks_like_multitap': looks_like_multitap,
        'looks_like_chemical_symbols': looks_like_chemical_symbols,
        'looks_like_houdini_words': looks_like_houdini_words,
        'looks_like_nak_nak': looks_like_nak_nak,
        'looks_like_shadok': looks_like_shadok,
        'looks_like_tom_tom': looks_like_tom_tom,
        'looks_like_gold_bug': looks_like_gold_bug,
        'looks_like_postnet': looks_like_postnet,
        'looks_like_prime_sequence': looks_like_prime_sequence,
        'looks_like_bacon': looks_like_bacon,
        'looks_like_coordinate_fragment': looks_like_coordinate_fragment,
        'suggested_preset': suggested_preset,
    }


def _candidate_name_matches(candidate: Dict[str, Any], *fragments: str) -> bool:
    name = (candidate.get('name') or '').lower()
    description = (candidate.get('description') or '').lower()
    return any(fragment in name or fragment in description for fragment in fragments)


def _score_metasolver_candidate(candidate: Dict[str, Any], signature: Dict[str, Any]) -> Dict[str, Any]:
    score = float(candidate.get('priority', 50))
    reasons: List[str] = []

    candidate_charset = candidate.get('input_charset') or ''
    dominant_kind = signature.get('dominant_input_kind')
    charsets_present = set(signature.get('charsets_present') or [])
    tags = set(candidate.get('tags') or [])
    preferred_when = list(candidate.get('preferred_when') or [])
    requires_key = bool(candidate.get('requires_key', False))
    supports_grouped_input = bool(candidate.get('supports_grouped_input', False))

    if candidate_charset == dominant_kind:
        score += 40
        reasons.append(f"Correspondance directe avec l'entrÃ©e {dominant_kind}")
    elif dominant_kind == 'mixed' and candidate_charset in charsets_present:
        score += 18
        reasons.append(f"Compatible avec une entrÃ©e mixte contenant {candidate_charset}")
    elif dominant_kind == 'words' and candidate_charset == 'letters':
        score += 15
        reasons.append("Compatible avec un texte composÃ© de mots")
    elif candidate_charset and dominant_kind not in ('mixed', 'empty'):
        score -= 10

    if dominant_kind in ('letters', 'words') and 'substitution' in tags:
        score += 12
        reasons.append("Tag substitution cohÃ©rent avec une entrÃ©e textuelle")

    if dominant_kind == 'digits' and 'numeral' in tags:
        score += 20
        reasons.append("Tag numeral cohÃ©rent avec une entrÃ©e numÃ©rique")

    if signature.get('looks_like_morse'):
        if _candidate_name_matches(candidate, 'morse'):
            score += 150
            reasons.append("Le texte ressemble fortement Ã  du Morse")
        elif candidate_charset == 'symbols':
            score += 12

    if signature.get('looks_like_binary'):
        if _candidate_name_matches(candidate, 'base', 'binary'):
            score += 100
            reasons.append("Le texte ressemble Ã  une sÃ©quence binaire")
        elif 'numeral' in tags:
            score += 20

    if signature.get('looks_like_hex'):
        if _candidate_name_matches(candidate, 'base', 'hex'):
            score += 90
            reasons.append("Le texte ressemble Ã  une sÃ©quence hexadÃ©cimale")
        elif 'numeral' in tags:
            score += 20

    if signature.get('looks_like_phone_keypad') and _candidate_name_matches(candidate, 't9', 'phone', 'keypad'):
        score += 120
        reasons.append("Le texte ressemble Ã  une saisie type T9")

    if signature.get('looks_like_multitap') and _candidate_name_matches(candidate, 'multitap', 'multi tap'):
        score += 140
        reasons.append("Le texte ressemble Ã  un code Multitap")

    if signature.get('looks_like_chemical_symbols') and _candidate_name_matches(candidate, 'chemical', 'element'):
        score += 140
        reasons.append("Le texte ressemble Ã  des symboles chimiques")

    if signature.get('looks_like_houdini_words') and _candidate_name_matches(candidate, 'houdini'):
        score += 150
        reasons.append("Le texte ressemble Ã  du code Houdini")

    if signature.get('looks_like_nak_nak') and _candidate_name_matches(candidate, 'nak'):
        score += 160
        reasons.append("Le texte ressemble Ã  du code Nak Nak")

    if signature.get('looks_like_shadok') and _candidate_name_matches(candidate, 'shadok'):
        score += 150
        reasons.append("Le texte ressemble Ã  de la numÃ©ration Shadok")

    if signature.get('looks_like_tom_tom') and _candidate_name_matches(candidate, 'tom'):
        score += 180
        reasons.append("Le texte ressemble Ã  du code Tom Tom")

    if signature.get('looks_like_gold_bug') and _candidate_name_matches(candidate, 'gold', 'scarab'):
        score += 180
        reasons.append("Le texte ressemble Ã  du Gold-Bug")

    if signature.get('looks_like_postnet') and _candidate_name_matches(candidate, 'postnet', 'barcode'):
        score += 260
        reasons.append("Le texte ressemble Ã  un code POSTNET")

    if signature.get('looks_like_prime_sequence') and _candidate_name_matches(candidate, 'prime'):
        score += 130
        reasons.append("Le texte ressemble Ã  une sÃ©quence de nombres premiers")

    if signature.get('looks_like_roman_numerals') and _candidate_name_matches(candidate, 'roman'):
        score += 120
        reasons.append("Le texte ressemble Ã  des chiffres romains")

    if signature.get('looks_like_polybius') and _candidate_name_matches(candidate, 'polybius', 'polybe'):
        score += 140
        reasons.append("Le texte ressemble Ã  des coordonnÃ©es Polybe / Polybius")

    if signature.get('looks_like_tap_code') and _candidate_name_matches(candidate, 'tap'):
        score += 120
        reasons.append("Le texte ressemble Ã  du Tap Code")

    if signature.get('looks_like_decimal_sequence') and candidate_charset == 'digits':
        score += 15
        reasons.append("EntrÃ©e dÃ©coupÃ©e en groupes numÃ©riques")

    if signature.get('looks_like_coordinate_fragment') and _candidate_name_matches(candidate, 'coord', 'gps'):
        score += 60
        reasons.append("Le texte ressemble Ã  un fragment de coordonnÃ©es")

    preferred_condition_map = {
        'letters_only': (dominant_kind == 'letters', "OptimisÃƒÂ© pour une entrÃƒÂ©e uniquement en lettres"),
        'digits_only': (dominant_kind == 'digits', "OptimisÃƒÂ© pour une entrÃƒÂ©e uniquement en chiffres"),
        'symbols_only': (dominant_kind == 'symbols', "OptimisÃƒÂ© pour une entrÃƒÂ©e symbolique"),
        'words_only': (dominant_kind == 'words', "OptimisÃƒÂ© pour une entrÃƒÂ©e composÃƒÂ©e de mots"),
        'mixed_input': (dominant_kind == 'mixed', "Compatible avec une entrÃƒÂ©e mixte"),
        'grouped_input': (int(signature.get('group_count', 0)) > 1, "Compatible avec une entrÃƒÂ©e dÃƒÂ©coupÃƒÂ©e en groupes"),
        'short_input': (int(signature.get('non_space_length', 0)) <= 12, "Pertinent sur des entrÃƒÂ©es courtes"),
        'long_input': (int(signature.get('non_space_length', 0)) >= 24, "Pertinent sur des entrÃƒÂ©es longues"),
        'morse_like': (bool(signature.get('looks_like_morse')), "Le motif dÃƒÂ©tectÃƒÂ© correspond au Morse"),
        'binary_like': (bool(signature.get('looks_like_binary')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du binaire"),
        'hex_like': (bool(signature.get('looks_like_hex')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  de l'hexadÃƒÂ©cimal"),
        't9_like': (bool(signature.get('looks_like_phone_keypad')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  une saisie T9"),
        'chemical_like': (bool(signature.get('looks_like_chemical_symbols')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  des symboles chimiques"),
        'houdini_like': (bool(signature.get('looks_like_houdini_words')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du code Houdini"),
        'nak_nak_like': (bool(signature.get('looks_like_nak_nak')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du code Nak Nak"),
        'shadok_like': (bool(signature.get('looks_like_shadok')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  de la numÃƒÂ©ration Shadok"),
        'tom_tom_like': (bool(signature.get('looks_like_tom_tom')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du code Tom Tom"),
        'gold_bug_like': (bool(signature.get('looks_like_gold_bug')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du Gold-Bug"),
        'postnet_like': (bool(signature.get('looks_like_postnet')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du POSTNET"),
        'prime_like': (bool(signature.get('looks_like_prime_sequence')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  une sÃƒÂ©quence de nombres premiers"),
        'roman_like': (bool(signature.get('looks_like_roman_numerals')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  des chiffres romains"),
        'a1z26_like': (bool(signature.get('looks_like_a1z26')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  un code A1Z26"),
        'tap_code_like': (bool(signature.get('looks_like_tap_code')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du Tap Code"),
        'polybius_like': (bool(signature.get('looks_like_polybius')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du Polybius"),
        'multitap_like': (bool(signature.get('looks_like_multitap')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du Multitap"),
        'bacon_like': (bool(signature.get('looks_like_bacon')), "Le motif dÃƒÂ©tectÃƒÂ© correspond ÃƒÂ  du Bacon"),
        'digit_groups': (bool(signature.get('looks_like_decimal_sequence')), "L'entrÃƒÂ©e est segmentÃƒÂ©e en groupes numÃƒÂ©riques"),
        'coordinate_fragment': (bool(signature.get('looks_like_coordinate_fragment')), "L'entrÃƒÂ©e ressemble ÃƒÂ  un fragment de coordonnÃƒÂ©es"),
    }

    matched_preferences = [
        preferred_condition_map[condition][1]
        for condition in preferred_when
        if condition in preferred_condition_map and preferred_condition_map[condition][0]
    ]
    if matched_preferences:
        score += 24 * len(matched_preferences)
        reasons.extend(matched_preferences)

    if supports_grouped_input and int(signature.get('group_count', 0)) > 1:
        score += 6
        reasons.append("Supporte explicitement les entrÃƒÂ©es groupÃƒÂ©es")

    if requires_key:
        score -= 18
        reasons.append("NÃƒÂ©cessite souvent une clÃƒÂ© ou un indice supplÃƒÂ©mentaire")

    if 'frequent' in tags:
        score += 8
        reasons.append("Code frÃ©quent en gÃ©ocaching")

    if 'no_key' in tags:
        score += 5
        reasons.append("Ne nÃ©cessite pas de clÃ© explicite")

    return {
        **candidate,
        'score': round(score, 2),
        'reasons': list(dict.fromkeys(reasons)),
    }


def _normalize_max_plugins(value: Any, default: int = 8) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


LISTING_CLASSIFICATION_ACTIONS: Dict[str, str] = {
    'secret_code': "Extract the most structured fragment, then call recommend_metasolver_plugins before metasolver.",
    'hidden_content': "Inspect HTML comments, hidden styles and page source before trying decoders.",
    'formula': "List variables and coordinate placeholders, then use the formula solver workflow.",
    'word_game': "Identify the exact game type first (sudoku, crossword, anagram, etc.) before decoding.",
    'image_puzzle': "Inspect listing images and run OCR / QR / barcode tools if relevant.",
    'coord_transform': "Compare posted coordinates, waypoint notes and projection clues before estimating finals.",
    'checker_available': "Validate textual answers or final coordinates with run_checker before concluding.",
}


def _clean_listing_text(value: Any, *, preserve_lines: bool = False) -> str:
    if value is None:
        return ''

    text = html.unescape(str(value))
    text = re.sub(r'<script\b[^>]*>.*?</script>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<style\b[^>]*>.*?</style>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    if preserve_lines:
        text = re.sub(r'</?(?:p|div|li|ul|ol|br|tr|td|table|section|article|h[1-6])[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<!--.*?-->', ' ', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('\xa0', ' ')

    if preserve_lines:
        lines = []
        for line in text.splitlines():
            normalized = re.sub(r'\s+', ' ', line).strip()
            if normalized:
                lines.append(normalized)
        return '\n'.join(lines)

    return re.sub(r'\s+', ' ', text).strip()


def _collect_waypoint_listing_text(waypoints: Any) -> str:
    if not isinstance(waypoints, list):
        return ''

    parts: List[str] = []
    for waypoint in waypoints:
        if not isinstance(waypoint, dict):
            continue
        for key in ('prefix', 'lookup', 'name', 'type', 'gc_coords', 'note', 'note_override'):
            value = waypoint.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return '\n'.join(parts)


def _extract_hidden_content_signals(description_html: str) -> Dict[str, Any]:
    raw_html = description_html or ''
    signals: List[str] = []

    comments = [
        _clean_listing_text(match, preserve_lines=False)
        for match in re.findall(r'<!--(.*?)-->', raw_html, flags=re.DOTALL)
    ]
    comments = [comment[:160] for comment in comments if comment]
    if comments:
        signals.append("HTML comments present")

    style_patterns = (
        (r'display\s*:\s*none', "display:none detected"),
        (r'visibility\s*:\s*hidden', "visibility:hidden detected"),
        (r'opacity\s*:\s*0(?:[^\d]|$)', "opacity:0 detected"),
        (r'font-size\s*:\s*0(?:px|em|rem|pt|%)?', "font-size:0 detected"),
        (r'<[^>]+\bhidden\b', "hidden attribute detected"),
    )
    for pattern, label in style_patterns:
        if re.search(pattern, raw_html, flags=re.IGNORECASE):
            signals.append(label)

    return {
        'signals': signals,
        'comments': comments[:4],
    }


def _build_secret_fragment_evidence(signature: Dict[str, Any], source_name: str) -> List[str]:
    evidence: List[str] = []
    if source_name == 'html_comment':
        evidence.append("Fragment extracted from an HTML comment")
    if signature.get('looks_like_morse'):
        evidence.append("Morse-like pattern detected")
    if signature.get('looks_like_binary'):
        evidence.append("Binary-like pattern detected")
    if signature.get('looks_like_hex'):
        evidence.append("Hex-like pattern detected")
    if signature.get('looks_like_phone_keypad'):
        evidence.append("T9-like pattern detected")
    if signature.get('looks_like_roman_numerals'):
        evidence.append("Roman numeral pattern detected")
    if signature.get('looks_like_a1z26'):
        evidence.append("Grouped values in the 1-26 range detected")
    if signature.get('looks_like_tap_code'):
        evidence.append("Tap code groups detected")
    if signature.get('looks_like_bacon'):
        evidence.append("Bacon pattern detected")
    if signature.get('dominant_input_kind') in ('digits', 'symbols', 'mixed'):
        evidence.append(f"Dominant input kind: {signature.get('dominant_input_kind')}")
    if int(signature.get('group_count', 0)) > 1:
        evidence.append("The fragment is split into multiple groups")
    return list(dict.fromkeys(evidence))


def _score_secret_fragment(signature: Dict[str, Any], source_name: str) -> float:
    score = 0.0
    if signature.get('looks_like_morse'):
        score += 60
    if signature.get('looks_like_binary'):
        score += 48
    if signature.get('looks_like_hex'):
        score += 42
    if signature.get('looks_like_phone_keypad'):
        score += 45
    if signature.get('looks_like_roman_numerals'):
        score += 32
    if signature.get('looks_like_a1z26'):
        score += 50
    if signature.get('looks_like_tap_code'):
        score += 50
    if signature.get('looks_like_bacon'):
        score += 50

    dominant_kind = signature.get('dominant_input_kind')
    if dominant_kind in ('digits', 'symbols', 'mixed'):
        score += 16
    if int(signature.get('group_count', 0)) > 1:
        score += 10

    fragment_length = int(signature.get('non_space_length', 0))
    if 4 <= fragment_length <= 64:
        score += 8
    if source_name == 'html_comment':
        score += 10
    if signature.get('looks_like_coordinate_fragment'):
        score -= 12
    if dominant_kind == 'words' and int(signature.get('word_count', 0)) >= 3:
        score -= 20

    return score


def _register_secret_fragment(
    *,
    fragments: List[Dict[str, Any]],
    seen: set,
    text: str,
    source_name: str,
    source_kind: str,
) -> None:
    normalized_text = re.sub(r'\s+', ' ', (text or '')).strip()
    if len(normalized_text) < 4:
        return

    dedupe_key = normalized_text.lower()
    if dedupe_key in seen:
        return

    signature = _analyze_metasolver_signature(normalized_text)
    score = _score_secret_fragment(signature, source_name)
    if score < 25:
        return

    fragments.append({
        'source': source_name,
        'source_kind': source_kind,
        'text': normalized_text[:160],
        'score': round(score, 2),
        'confidence': round(min(0.99, max(0.05, score / 100.0)), 3),
        'signature': signature,
        'evidence': _build_secret_fragment_evidence(signature, source_name),
    })
    seen.add(dedupe_key)


def _extract_secret_fragments(
    *,
    title: str,
    description: str,
    hint: str,
    waypoint_text: str,
    hidden_comments: List[str],
    max_fragments: int,
) -> List[Dict[str, Any]]:
    fragments: List[Dict[str, Any]] = []
    seen: set = set()

    source_values = [
        ('title', title),
        ('hint', hint),
        ('description', description),
        ('waypoints', waypoint_text),
    ]

    patterns = (
        ('morse_like', re.compile(r'(?<!\S)(?=[.\-/| ]{5,}[.\-])[.\-/| ]{5,}(?!\S)')),
        ('digit_groups', re.compile(r'(?<!\w)(?:\d{1,3}(?:[\s,;:/_-]+\d{1,3}){2,})(?!\w)')),
        ('tap_code', re.compile(r'(?<!\w)(?:[1-5]{2}(?:\s+[1-5]{2}){1,})(?!\w)')),
        ('bacon_like', re.compile(r'(?<!\w)(?:[AB]{5}(?:[\s,;:/_-]*[AB]{5})+)(?!\w)', flags=re.IGNORECASE)),
        ('t9_like', re.compile(r'(?<!\w)[2-9]{4,}(?!\w)')),
        ('hex_like', re.compile(r'(?<!\w)(?:0x)?[A-F0-9]{6,32}(?!\w)', flags=re.IGNORECASE)),
        ('mixed_code', re.compile(r'(?<!\w)[A-Z0-9]{5,24}(?!\w)')),
    )

    for source_name, source_text in source_values:
        cleaned_source = (source_text or '').strip()
        if not cleaned_source:
            continue

        source_kind = 'listing_field'
        if source_name in ('title', 'hint') and len(cleaned_source) <= 96:
            _register_secret_fragment(
                fragments=fragments,
                seen=seen,
                text=cleaned_source,
                source_name=source_name,
                source_kind=source_kind,
            )

        for _, pattern in patterns:
            for match in pattern.findall(cleaned_source):
                _register_secret_fragment(
                    fragments=fragments,
                    seen=seen,
                    text=match,
                    source_name=source_name,
                    source_kind=source_kind,
                )

    for comment in hidden_comments:
        _register_secret_fragment(
            fragments=fragments,
            seen=seen,
            text=comment,
            source_name='html_comment',
            source_kind='hidden_html',
        )

    fragments.sort(key=lambda item: (-item['score'], -item['confidence'], item['source'], item['text']))
    return fragments[:max_fragments]


def _label_confidence(raw_score: float, *, max_score: float = 100.0) -> float:
    bounded = min(max(raw_score, 0.0), max_score)
    return round(min(0.99, max(0.05, bounded / max_score)), 3)


def _build_listing_classification(
    *,
    title: str,
    description: str,
    description_html: str,
    hint: str,
    waypoint_text: str,
    image_count: int,
    checker_count: int,
    waypoint_count: int,
    max_secret_fragments: int,
) -> Dict[str, Any]:
    hidden_info = _extract_hidden_content_signals(description_html)
    hidden_signals = hidden_info.get('signals') or []
    hidden_comments = hidden_info.get('comments') or []

    combined_text = '\n'.join(part for part in (title, description, hint, waypoint_text) if part).strip()
    combined_lower = combined_text.lower()

    secret_fragments = _extract_secret_fragments(
        title=title,
        description=description,
        hint=hint,
        waypoint_text=waypoint_text,
        hidden_comments=hidden_comments,
        max_fragments=max_secret_fragments,
    )

    labels: List[Dict[str, Any]] = []
    formula_signals: List[str] = []

    formula_keywords = (
        r'\b(formula|formule|equation|projection|project|coord(?:onnee|onnee|onn|inate)s?|variable|variables|solve|solver|calcul|calcule|calculate)\b'
    )
    word_game_keywords = (
        r'\b(sudoku|crossword|mot croise|mots croises|anagram|word search|cryptogram|hangman|mastermind|nonogram|wordle|scrabble)\b'
    )
    image_keywords = (
        r'\b(image|photo|picture|visual|visuel|qr|barcode|ocr|stegano|steganography|jigsaw|puzzle)\b'
    )
    code_keywords = (
        r'\b(code|cipher|decode|decrypt|crypt|enigme|secret|morse|alphabet|substitution|transposition)\b'
    )
    coord_keywords = (
        r'\b(coord(?:onnee|onnee|onn|inate)s?|projection|bearing|distance|waypoint|final|offset|azimuth)\b'
    )

    variable_assignments = re.findall(r'\b[A-Z]{1,3}\s*=\s*[-+*/()0-9A-Z ]{1,40}', combined_text)
    if variable_assignments:
        formula_signals.append(f"{len(variable_assignments)} variable assignment(s) detected")
    if re.search(formula_keywords, combined_lower, flags=re.IGNORECASE):
        formula_signals.append("Formula or coordinate keywords detected")
    if re.search(r'\b[NS]\s*\d', combined_text, flags=re.IGNORECASE) and re.search(r'[A-Z]\s*[+\-*/=]', combined_text):
        formula_signals.append("Coordinate pattern mixed with variables detected")

    formula_score = 0.0
    formula_score += 30.0 if variable_assignments else 0.0
    formula_score += 24.0 if re.search(formula_keywords, combined_lower, flags=re.IGNORECASE) else 0.0
    formula_score += 30.0 if re.search(r'\b[NS]\s*\d', combined_text, flags=re.IGNORECASE) and re.search(r'[A-Z]\s*[+\-*/=]', combined_text) else 0.0
    if formula_score >= 28.0:
        labels.append({
            'name': 'formula',
            'confidence': _label_confidence(formula_score),
            'evidence': formula_signals[:4],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['formula'],
        })

    hidden_score = 24.0 * len(hidden_signals) + (12.0 if hidden_comments else 0.0)
    if hidden_score >= 24.0:
        labels.append({
            'name': 'hidden_content',
            'confidence': _label_confidence(hidden_score),
            'evidence': hidden_signals[:4] or ["Suspicious hidden HTML markers detected"],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['hidden_content'],
        })

    secret_score = 0.0
    secret_evidence: List[str] = []
    if secret_fragments:
        strongest_fragment = secret_fragments[0]
        secret_score += strongest_fragment.get('score', 0.0)
        secret_evidence.append(
            f"Structured fragment detected in {strongest_fragment.get('source')}: {strongest_fragment.get('text')[:60]}"
        )
    if re.search(code_keywords, combined_lower, flags=re.IGNORECASE):
        secret_score += 18.0
        secret_evidence.append("Code / cipher vocabulary detected")
    if hint and len(hint.strip()) <= 96 and secret_fragments:
        secret_score += 8.0
        secret_evidence.append("The hint contains a compact candidate fragment")
    if secret_score >= 32.0:
        labels.append({
            'name': 'secret_code',
            'confidence': _label_confidence(secret_score),
            'evidence': secret_evidence[:4],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['secret_code'],
        })

    word_game_score = 35.0 if re.search(word_game_keywords, combined_lower, flags=re.IGNORECASE) else 0.0
    if word_game_score:
        labels.append({
            'name': 'word_game',
            'confidence': _label_confidence(word_game_score),
            'evidence': ["Word-game keywords detected in the listing"],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['word_game'],
        })

    image_score = 0.0
    image_evidence: List[str] = []
    if image_count > 0:
        image_score += min(40.0, 12.0 + 6.0 * image_count)
        image_evidence.append(f"{image_count} image(s) attached to the listing")
    if '<img' in (description_html or '').lower():
        image_score += 12.0
        image_evidence.append("Inline image tags detected in listing HTML")
    if re.search(image_keywords, combined_lower, flags=re.IGNORECASE):
        image_score += 18.0
        image_evidence.append("Image / OCR / QR vocabulary detected")
    if image_score >= 24.0:
        labels.append({
            'name': 'image_puzzle',
            'confidence': _label_confidence(image_score),
            'evidence': image_evidence[:4],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['image_puzzle'],
        })

    coord_score = 0.0
    coord_evidence: List[str] = []
    if re.search(coord_keywords, combined_lower, flags=re.IGNORECASE):
        coord_score += 26.0
        coord_evidence.append("Coordinate / projection vocabulary detected")
    if waypoint_count > 0:
        coord_score += min(18.0, 6.0 + waypoint_count * 3.0)
        coord_evidence.append(f"{waypoint_count} waypoint(s) available")
    if re.search(r'\b[NS]\s*\d', combined_text, flags=re.IGNORECASE):
        coord_score += 16.0
        coord_evidence.append("Coordinate-like fragments detected")
    if formula_score >= 28.0:
        coord_score += 12.0
        coord_evidence.append("Formula clues are tied to coordinates")
    if coord_score >= 28.0:
        labels.append({
            'name': 'coord_transform',
            'confidence': _label_confidence(coord_score),
            'evidence': coord_evidence[:4],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['coord_transform'],
        })

    if checker_count > 0:
        labels.append({
            'name': 'checker_available',
            'confidence': _label_confidence(min(100.0, 36.0 + checker_count * 10.0)),
            'evidence': [f"{checker_count} checker(s) linked to the geocache"],
            'suggested_next_step': LISTING_CLASSIFICATION_ACTIONS['checker_available'],
        })

    labels.sort(key=lambda item: (-item['confidence'], item['name']))

    recommended_actions: List[str] = []
    for item in labels:
        action = item.get('suggested_next_step')
        if action and action not in recommended_actions:
            recommended_actions.append(action)

    return {
        'labels': labels,
        'recommended_actions': recommended_actions[:5],
        'candidate_secret_fragments': secret_fragments,
        'hidden_signals': hidden_signals[:6],
        'formula_signals': formula_signals[:6],
        'signal_summary': {
            'has_title': bool(title),
            'has_hint': bool(hint),
            'has_description_html': bool(description_html),
            'image_count': image_count,
            'checker_count': checker_count,
            'waypoint_count': waypoint_count,
        },
    }


def _serialize_geocache_listing(geocache: Geocache) -> Dict[str, Any]:
    decoded_hint = geocache.hints_decoded_override or geocache.hints_decoded
    if decoded_hint is None and geocache.hints:
        decoded_hint = Geocache.decode_hint_rot13(geocache.hints)

    description_raw = geocache.description_override_raw or geocache.description_raw or ''
    description_html = geocache.description_override_html or geocache.description_html or ''
    images = geocache.images or []
    waypoints = [waypoint.to_dict() for waypoint in (geocache.waypoints or [])]
    checkers = [checker.to_dict() for checker in (geocache.checkers or [])]

    return {
        'title': geocache.name or '',
        'description': description_raw or _clean_listing_text(description_html, preserve_lines=True),
        'description_html': description_html,
        'hint': decoded_hint or '',
        'waypoints': waypoints,
        'checkers': checkers,
        'images': images,
        'metadata': {
            'id': geocache.id,
            'gc_code': geocache.gc_code,
            'name': geocache.name,
        },
    }

class BatchPluginTask:
    """
    Classe pour gÃ©rer l'exÃ©cution batch d'un plugin sur plusieurs gÃ©ocaches.
    """
    
    def __init__(
        self,
        task_id: str,
        plugin_name: str,
        geocaches: List[Dict],
        inputs: Dict[str, Any],
        execution_mode: str = 'sequential',
        max_concurrency: int = 3,
        detect_coordinates: bool = True,
        app=None,
        include_images: bool = False,
    ):
        self.task_id = task_id
        self.plugin_name = plugin_name
        self.geocaches = geocaches
        self.inputs = inputs
        self.execution_mode = execution_mode
        self.max_concurrency = max_concurrency
        self.detect_coordinates = detect_coordinates
        self.app = app
        self.include_images = include_images
        
        # Ã‰tat de la tÃ¢che
        self.status = 'pending'  # pending, running, completed, failed, cancelled
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.cancelled = False
        
        # RÃ©sultats par gÃ©ocache
        self.results: List[Dict] = []
        for geocache in geocaches:
            self.results.append({
                'geocache_id': geocache['id'],
                'gc_code': geocache['gc_code'],
                'name': geocache['name'],
                'status': 'pending',
                'result': None,
                'error': None,
                'execution_time': None,
                'coordinates': None,
                'started_at': None,
                'completed_at': None
            })
    
    def execute(self):
        """
        ExÃ©cute la tÃ¢che batch selon le mode configurÃ©.
        """
        try:
            self.status = 'running'
            self.started_at = datetime.utcnow()
            
            logger.info(f"Starting batch task {self.task_id}: {self.plugin_name} on {len(self.geocaches)} geocaches")

            if self.app is not None:
                with self.app.app_context():
                    if self.execution_mode == 'sequential':
                        self._execute_sequential()
                    else:
                        self._execute_parallel()
            else:
                if self.execution_mode == 'sequential':
                    self._execute_sequential()
                else:
                    self._execute_parallel()
            
            if not self.cancelled:
                self.status = 'completed'
                logger.info(f"Batch task {self.task_id} completed successfully")
            
        except Exception as e:
            self.status = 'failed'
            logger.error(f"Batch task {self.task_id} failed: {str(e)}")
        finally:
            self.completed_at = datetime.utcnow()
    
    def _execute_sequential(self):
        """
        ExÃ©cution sÃ©quentielle des gÃ©ocaches.
        """
        for i, geocache in enumerate(self.geocaches):
            if self.cancelled:
                break
            
            result = self.results[i]
            result['status'] = 'executing'
            result['started_at'] = datetime.utcnow()
            
            try:
                start_time = time.time()
                
                # PrÃ©parer les inputs pour cette gÃ©ocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # ExÃ©cuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les rÃ©sultats
                processed_result = self._process_plugin_result(plugin_result, geocache)
                
                result.update({
                    'status': 'completed',
                    'result': plugin_result,
                    'coordinates': processed_result.get('coordinates'),
                    'execution_time': execution_time,
                    'completed_at': datetime.utcnow()
                })
                
            except Exception as e:
                result.update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': datetime.utcnow()
                })
                logger.error(f"Error executing plugin on {geocache['gc_code']}: {str(e)}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass
    
    def _execute_parallel(self):
        """
        ExÃ©cution parallÃ¨le des gÃ©ocaches avec ThreadPoolExecutor.
        """
        def execute_single_geocache(geocache_data):
            geocache, result_index = geocache_data
            result = self.results[result_index]
            
            if self.cancelled:
                return result
            
            result['status'] = 'executing'
            result['started_at'] = datetime.utcnow()
            
            try:
                if self.app is not None:
                    ctx = self.app.app_context()
                    ctx.push()
                else:
                    ctx = None

                start_time = time.time()
                
                # PrÃ©parer les inputs pour cette gÃ©ocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # ExÃ©cuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les rÃ©sultats
                processed_result = self._process_plugin_result(plugin_result, geocache)
                
                result.update({
                    'status': 'completed',
                    'result': plugin_result,
                    'coordinates': processed_result.get('coordinates'),
                    'execution_time': execution_time,
                    'completed_at': datetime.utcnow()
                })
                
            except Exception as e:
                result.update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': datetime.utcnow()
                })
                logger.error(f"Error executing plugin on {geocache['gc_code']}: {str(e)}")
            finally:
                try:
                    db.session.remove()
                except Exception:
                    pass
                if ctx is not None:
                    try:
                        ctx.pop()
                    except Exception:
                        pass
            
            return result
        
        # ExÃ©cuter en parallÃ¨le avec ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            # Soumettre toutes les tÃ¢ches
            future_to_index = {
                executor.submit(execute_single_geocache, (geocache, i)): i 
                for i, geocache in enumerate(self.geocaches)
            }
            
            # Traiter les rÃ©sultats au fur et Ã  mesure
            for future in as_completed(future_to_index):
                if self.cancelled:
                    break
                try:
                    future.result()
                except Exception as e:
                    index = future_to_index[future]
                    self.results[index].update({
                        'status': 'error',
                        'error': str(e),
                        'completed_at': datetime.utcnow()
                    })
    
    def _prepare_inputs_for_geocache(self, geocache: Dict) -> Dict[str, Any]:
        """
        PrÃ©pare les inputs du plugin pour une gÃ©ocache spÃ©cifique.
        """
        inputs = self.inputs.copy()
        
        # Injecter les donnÃ©es spÃ©cifiques Ã  la gÃ©ocache
        plugin_manager = get_plugin_manager()
        plugin_info = plugin_manager.get_plugin_info(self.plugin_name)
        if plugin_info and 'metadata' in plugin_info and 'input_types' in plugin_info['metadata']:
            input_types = plugin_info['metadata']['input_types']
            
            for key, input_type in input_types.items():
                default_value_source = input_type.get('default_value_source')
                
                if default_value_source == 'geocache_id':
                    inputs[key] = geocache['gc_code']
                elif default_value_source == 'geocache_description' and geocache.get('description'):
                    inputs[key] = geocache['description']
                elif default_value_source == 'geocache_coordinates' and geocache.get('coordinates'):
                    coords = geocache['coordinates']
                    inputs[key] = coords.get('coordinates_raw') or f"{coords['latitude']}, {coords['longitude']}"

            if self.include_images and 'images' in input_types and geocache.get('images') and not inputs.get('images'):
                inputs['images'] = geocache['images']

            if 'waypoints' in input_types and geocache.get('waypoints') and not isinstance(inputs.get('waypoints'), list):
                inputs['waypoints'] = geocache.get('waypoints') or []
        
        return inputs
    
    def _process_plugin_result(self, plugin_result: Dict, geocache: Dict) -> Dict:
        """
        Traite les rÃ©sultats du plugin (dÃ©tection de coordonnÃ©es, etc.).
        """
        processed = {}

        primary = plugin_result.get('primary_coordinates')
        if isinstance(primary, dict):
            lat = primary.get('latitude')
            lon = primary.get('longitude')
            if lat is not None and lon is not None:
                formatted = None
                for item in plugin_result.get('results') or []:
                    coords = item.get('coordinates')
                    if isinstance(coords, dict) and coords.get('formatted'):
                        formatted = coords.get('formatted')
                        break
                if not formatted:
                    formatted = f"{lat}, {lon}"
                try:
                    processed['coordinates'] = {
                        'latitude': float(lat),
                        'longitude': float(lon),
                        'formatted': str(formatted)
                    }
                    return processed
                except Exception:
                    pass

        if plugin_result.get('results'):
            for item in plugin_result.get('results'):
                lat = item.get('decimal_latitude')
                lon = item.get('decimal_longitude')
                if lat is not None and lon is not None:
                    formatted = None
                    coords = item.get('coordinates')
                    if isinstance(coords, dict):
                        formatted = coords.get('formatted')
                    if not formatted:
                        formatted = f"{lat}, {lon}"
                    try:
                        processed['coordinates'] = {
                            'latitude': float(lat),
                            'longitude': float(lon),
                            'formatted': str(formatted)
                        }
                        return processed
                    except Exception:
                        continue
        
        if self.detect_coordinates and plugin_result.get('results'):
            for item in plugin_result['results']:
                text_output = item.get('text_output')
                if text_output:
                    try:
                        # Utiliser directement la fonction de dÃ©tection (pas l'API)
                        from gc_backend.blueprints.coordinates import detect_gps_coordinates
                        
                        logger.info(f"[Batch] DÃ©tection de coordonnÃ©es dans: {text_output[:100]}...")
                        
                        coords = detect_gps_coordinates(text_output, include_numeric_only=False)
                        
                        if coords.get('exist'):
                            logger.info(f"[Batch] CoordonnÃ©es trouvÃ©es: {coords.get('ddm')}")
                            processed['coordinates'] = {
                                'latitude': coords.get('decimal_latitude', 0),
                                'longitude': coords.get('decimal_longitude', 0),
                                'formatted': coords.get('ddm', '')
                            }
                            break
                        else:
                            logger.info(f"[Batch] Aucune coordonnÃ©e dÃ©tectÃ©e dans ce rÃ©sultat")
                    except Exception as e:
                        logger.warning(f"Error detecting coordinates: {str(e)}")
                        import traceback
                        traceback.print_exc()
        
        return processed
    
    def cancel(self):
        """
        Annule la tÃ¢che.
        """
        self.cancelled = True
        if self.status == 'running':
            self.status = 'cancelled'
        logger.info(f"Batch task {self.task_id} cancellation requested")
    
    def get_status(self) -> Dict:
        """
        Retourne le statut actuel de la tÃ¢che.
        """
        completed_count = len([r for r in self.results if r['status'] == 'completed'])
        error_count = len([r for r in self.results if r['status'] == 'error'])
        total_count = len(self.results)
        
        progress_percentage = (completed_count / total_count * 100) if total_count > 0 else 0
        
        return {
            'task_id': self.task_id,
            'plugin_name': self.plugin_name,
            'status': self.status,
            'progress': {
                'completed': completed_count,
                'errors': error_count,
                'total': total_count,
                'percentage': round(progress_percentage, 1)
            },
            'results': self.results,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'execution_mode': self.execution_mode,
            'cancelled': self.cancelled
        }


def get_plugin_manager() -> PluginManager:
    """
    RÃ©cupÃ¨re l'instance du PluginManager.
    
    Returns:
        PluginManager: Instance du gestionnaire
        
    Raises:
        RuntimeError: Si le manager n'est pas initialisÃ©
    """
    if _plugin_manager is None:
        raise RuntimeError(
            "PluginManager non initialisÃ©. "
            "Appelez init_plugin_manager() depuis create_app()"
        )
    return _plugin_manager


@bp.route('/score', methods=['POST'])
def score_text_endpoint():
    try:
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            return jsonify({
                "error": "JSON invalide",
                "message": f"Le body de la requÃªte doit Ãªtre un JSON valide: {str(json_error)}"
            }), 400

        if not data or not isinstance(data, dict):
            return jsonify({
                "error": "RequÃªte invalide",
                "message": "Le body doit Ãªtre un objet JSON"
            }), 400

        context = data.get('context')
        if context is not None and not isinstance(context, dict):
            return jsonify({
                "error": "RequÃªte invalide",
                "message": "Le champ 'context' doit Ãªtre un objet"
            }), 400

        from gc_backend.plugins.scoring import score_text

        if 'texts' in data:
            texts = data.get('texts')
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                return jsonify({
                    "error": "RequÃªte invalide",
                    "message": "Le champ 'texts' doit Ãªtre une liste de strings"
                }), 400

            results: List[Dict[str, Any]] = []
            for t in texts:
                results.append(score_text(t, context=context or {}))
            return jsonify({"results": results}), 200

        text = data.get('text')
        if not isinstance(text, str):
            return jsonify({
                "error": "RequÃªte invalide",
                "message": "Le champ 'text' (string) est requis"
            }), 400

        return jsonify(score_text(text, context=context or {})), 200

    except Exception as e:
        logger.error(f"Erreur scoring: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur scoring",
            "message": str(e)
        }), 500


# =============================================================================
# Routes de listage et informations
# =============================================================================

@bp.route('', methods=['GET'])
def list_plugins():
    """
    Liste tous les plugins disponibles avec filtres optionnels.
    
    Query Parameters:
        source (str, optional): Filtrer par source ('official', 'custom')
        category (str, optional): Filtrer par catÃ©gorie
        enabled (bool, optional): Filtrer par statut (true/false)
        
    Returns:
        JSON: {
            "plugins": [liste des plugins],
            "total": nombre total,
            "filters": filtres appliquÃ©s
        }
        
    Example:
        GET /api/plugins
        GET /api/plugins?source=official
        GET /api/plugins?category=Substitution
        GET /api/plugins?enabled=true
    """
    try:
        manager = get_plugin_manager()
        
        # RÃ©cupÃ©rer les paramÃ¨tres de filtre
        source = request.args.get('source')
        category = request.args.get('category')
        enabled_param = request.args.get('enabled')
        
        # Convertir enabled en boolÃ©en
        enabled_only = True  # Par dÃ©faut
        if enabled_param is not None:
            enabled_only = enabled_param.lower() in ['true', '1', 'yes']
        
        # Lister les plugins avec filtres
        plugins = manager.list_plugins(
            source=source,
            category=category,
            enabled_only=enabled_only
        )
        
        logger.info(
            f"Liste plugins : {len(plugins)} rÃ©sultats "
            f"(source={source}, category={category}, enabled={enabled_only})"
        )
        
        return jsonify({
            "plugins": plugins,
            "total": len(plugins),
            "filters": {
                "source": source,
                "category": category,
                "enabled": enabled_only
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors du listage des plugins: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors du listage des plugins",
            "message": str(e)
        }), 500


@bp.route('/metasolver/eligible', methods=['GET'])
def metasolver_eligible_plugins():
    """
    Liste les plugins Ã©ligibles au metasolver, optionnellement filtrÃ©s par preset.

    Query Parameters:
        preset (str, optional): Nom du preset Ã  appliquer (dÃ©faut: 'all')

    Returns:
        JSON: {
            "preset": nom du preset,
            "preset_filter": filtre appliquÃ©,
            "plugins": [ {name, description, input_charset, tags, priority} ],
            "total": nombre total
        }

    Example:
        GET /api/plugins/metasolver/eligible
        GET /api/plugins/metasolver/eligible?preset=frequent
    """
    try:
        manager = get_plugin_manager()
        preset_name = (request.args.get('preset') or 'all').lower()
        presets = _load_metasolver_presets(manager)
        preset_info = presets.get(preset_name, {})
        preset_filter = preset_info.get('filter', {})
        eligible = _collect_metasolver_candidates(preset_filter=preset_filter)

        return jsonify({
            'preset': preset_name,
            'preset_label': preset_info.get('label', preset_name),
            'preset_filter': preset_filter or None,
            'plugins': eligible,
            'total': len(eligible),
            'available_presets': {
                name: {'label': p.get('label', name), 'description': p.get('description', '')}
                for name, p in presets.items()
            }
        }), 200

    except Exception as e:
        logger.error(f"Erreur listing metasolver eligible: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur listing metasolver eligible",
            "message": str(e)
        }), 500


@bp.route('/metasolver/recommend', methods=['POST'])
def metasolver_recommend_plugins():
    """
    Analyse la signature d'entrÃ©e d'un texte et recommande une sous-liste de plugins metasolver.

    Request body:
        {
            "text": ".... . .-.. .-.. ---",
            "preset": "all",
            "mode": "decode",
            "max_plugins": 8
        }
    """
    try:
        data = request.get_json(force=True)
    except Exception as json_error:
        return jsonify({
            "error": "JSON invalide",
            "message": f"Le body doit Ãªtre un JSON valide: {str(json_error)}"
        }), 400

    if not data or not isinstance(data, dict):
        return jsonify({
            "error": "RequÃªte invalide",
            "message": "Le body doit Ãªtre un objet JSON"
        }), 400

    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({
            "error": "RequÃªte invalide",
            "message": "Le champ 'text' (string non vide) est requis"
        }), 400

    requested_preset = (data.get('preset') or '').strip().lower()
    mode = (data.get('mode') or 'decode').strip().lower()
    max_plugins = _normalize_max_plugins(data.get('max_plugins'), default=8)

    try:
        manager = get_plugin_manager()
        presets = _load_metasolver_presets(manager)
        signature = _analyze_metasolver_signature(text)

        effective_preset = requested_preset or signature.get('suggested_preset') or 'frequent'
        if effective_preset not in presets:
            effective_preset = 'all'

        preset_info = presets.get(effective_preset, {})
        preset_filter = preset_info.get('filter') or {}
        candidates = _collect_metasolver_candidates(preset_filter=preset_filter, mode=mode)
        scored = [_score_metasolver_candidate(candidate, signature) for candidate in candidates]
        scored.sort(key=lambda item: (-item['score'], -item['priority'], item['name']))

        selected = scored[:max_plugins]
        if selected:
            top_score = selected[0]['score'] or 1.0
        else:
            top_score = 1.0

        recommendations = []
        for item in selected:
            confidence = round(min(1.0, max(0.0, item['score'] / top_score)), 3)
            recommendations.append({
                **item,
                'confidence': confidence,
            })

        selected_plugins = [item['name'] for item in recommendations]
        explanation = [
            f"Signature dominante: {signature.get('dominant_input_kind')}",
            f"Preset effectif: {effective_preset}",
            f"Plugins recommandÃ©s: {len(selected_plugins)} / {len(scored)} Ã©ligibles",
        ]

        return jsonify({
            'requested_preset': requested_preset or None,
            'effective_preset': effective_preset,
            'effective_preset_label': preset_info.get('label', effective_preset),
            'preset_filter': preset_filter or None,
            'mode': mode,
            'max_plugins': max_plugins,
            'signature': signature,
            'recommendations': recommendations,
            'selected_plugins': selected_plugins,
            'plugin_list': ', '.join(selected_plugins),
            'eligible_total': len(scored),
            'available_presets': {
                name: {'label': value.get('label', name), 'description': value.get('description', '')}
                for name, value in presets.items()
            },
            'explanation': explanation,
        }), 200
    except Exception as e:
        logger.error(f"Erreur recommandation metasolver: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur recommandation metasolver",
            "message": str(e)
        }), 500


@bp.route('/listing/classify', methods=['POST'])
def classify_listing():
    """
    Classifie un listing de geocache en plusieurs familles d'enigmes.

    Request body:
        {
            "geocache_id": 123,
            "title": "Puzzle name",
            "description": "...",
            "description_html": "<!-- hidden -->",
            "hint": "...",
            "max_secret_fragments": 6
        }
    """
    try:
        data = request.get_json(force=True)
    except Exception as json_error:
        return jsonify({
            "error": "JSON invalide",
            "message": f"Le body doit etre un JSON valide: {str(json_error)}"
        }), 400

    if not data or not isinstance(data, dict):
        return jsonify({
            "error": "Requete invalide",
            "message": "Le body doit etre un objet JSON"
        }), 400

    max_secret_fragments = _normalize_max_plugins(data.get('max_secret_fragments'), default=6)

    try:
        geocache_id = data.get('geocache_id')
        source = 'direct_input'
        metadata: Dict[str, Any] | None = None

        if geocache_id is not None:
            try:
                geocache_id = int(geocache_id)
            except (TypeError, ValueError):
                return jsonify({
                    "error": "Requete invalide",
                    "message": "Le champ 'geocache_id' doit etre un entier"
                }), 400

            geocache = Geocache.query.get(geocache_id)
            if not geocache:
                return jsonify({
                    "error": "Geocache introuvable",
                    "message": f"Aucune geocache avec l'id {geocache_id}"
                }), 404

            payload = _serialize_geocache_listing(geocache)
            source = 'geocache'
            metadata = payload.pop('metadata', None)

            for field in ('title', 'description', 'description_html', 'hint'):
                override_value = data.get(field)
                if isinstance(override_value, str) and override_value.strip():
                    payload[field] = override_value
        else:
            payload = {
                'title': data.get('title') or '',
                'description': data.get('description') or '',
                'description_html': data.get('description_html') or '',
                'hint': data.get('hint') or '',
                'waypoints': data.get('waypoints') if isinstance(data.get('waypoints'), list) else [],
                'checkers': data.get('checkers') if isinstance(data.get('checkers'), list) else [],
                'images': data.get('images') if isinstance(data.get('images'), list) else [],
            }

        title = _clean_listing_text(payload.get('title'), preserve_lines=False)
        description = _clean_listing_text(payload.get('description'), preserve_lines=True)
        description_html = str(payload.get('description_html') or '')
        if not description and description_html:
            description = _clean_listing_text(description_html, preserve_lines=True)
        hint = _clean_listing_text(payload.get('hint'), preserve_lines=False)
        waypoints = payload.get('waypoints') or []
        checkers = payload.get('checkers') or []
        images = payload.get('images') or []
        waypoint_text = _clean_listing_text(_collect_waypoint_listing_text(waypoints), preserve_lines=True)

        if not any((title, description, description_html, hint, waypoint_text)):
            return jsonify({
                "error": "Requete invalide",
                "message": "Fournissez au moins un contenu de listing ou un geocache_id"
            }), 400

        classification = _build_listing_classification(
            title=title,
            description=description,
            description_html=description_html,
            hint=hint,
            waypoint_text=waypoint_text,
            image_count=len(images) if isinstance(images, list) else 0,
            checker_count=len(checkers) if isinstance(checkers, list) else 0,
            waypoint_count=len(waypoints) if isinstance(waypoints, list) else 0,
            max_secret_fragments=max_secret_fragments,
        )

        return jsonify({
            'source': source,
            'geocache': metadata,
            'title': title or None,
            'max_secret_fragments': max_secret_fragments,
            **classification,
        }), 200
    except Exception as e:
        logger.error(f"Erreur classification listing: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur classification listing",
            "message": str(e)
        }), 500


@bp.route('/metasolver/execute-stream', methods=['POST'])
def metasolver_execute_stream():
    """
    ExÃ©cute le metasolver en mode streaming SSE.

    Chaque sous-plugin exÃ©cutÃ© Ã©met des Ã©vÃ©nements en temps rÃ©el :
    - init         : liste des candidats
    - plugin_start : un sous-plugin dÃ©marre
    - plugin_done  : un sous-plugin a terminÃ© (avec rÃ©sultats)
    - plugin_error : un sous-plugin a Ã©chouÃ©
    - progress     : avancement global (pourcentage)
    - result       : rÃ©sultat final complet

    Request Body (JSON):
        inputs (dict): ParamÃ¨tres d'entrÃ©e identiques Ã  /metasolver/execute

    Returns:
        text/event-stream (SSE)

    Example:
        POST /api/plugins/metasolver/execute-stream
        {"inputs": {"text": "URYYB", "mode": "decode", "preset": "all"}}
    """
    import json as _json
    from flask import Response, stream_with_context

    try:
        data = request.get_json(force=True)
    except Exception as json_error:
        return jsonify({
            "error": "JSON invalide",
            "message": f"Le body doit Ãªtre un JSON valide: {str(json_error)}"
        }), 400

    if not data or 'inputs' not in data:
        return jsonify({
            "error": "RequÃªte invalide",
            "message": "Le champ 'inputs' est requis"
        }), 400

    inputs = data['inputs']

    manager = get_plugin_manager()

    # Charger le plugin metasolver via le plugin manager
    wrapper = manager.get_plugin(('metasolver'))
    if not wrapper:
        return jsonify({
            "error": "Plugin metasolver non disponible",
            "message": "Impossible de charger le plugin metasolver"
        }), 500

    # AccÃ©der Ã  l'instance brute du plugin pour appeler execute_streaming
    raw_instance = getattr(wrapper, '_instance', None)
    if not raw_instance or not hasattr(raw_instance, 'execute_streaming'):
        return jsonify({
            "error": "Streaming non supportÃ©",
            "message": "Le plugin metasolver ne supporte pas le mode streaming"
        }), 500

    logger.info(f"DÃ©marrage exÃ©cution streaming metasolver avec inputs: {list(inputs.keys())}")

    def generate():
        try:
            for event in raw_instance.execute_streaming(inputs):
                event_type = event.get('event', 'message')
                try:
                    event_data = _json.dumps(event.get('data', {}), ensure_ascii=False)
                except Exception as serial_exc:
                    logger.error(f"[streaming] JSON serialization error on event '{event_type}': {serial_exc}", exc_info=True)
                    event_data = _json.dumps({"error": f"Serialization error: {serial_exc}"}, ensure_ascii=False)
                logger.debug(f"[streaming] Yielding event: {event_type}")
                yield f"event: {event_type}\ndata: {event_data}\n\n"
            logger.info("[streaming] execute_streaming generator exhausted â€” all events sent")
        except Exception as exc:
            logger.error(f"[streaming] Unhandled exception in generate(): {exc}", exc_info=True)
            error_data = _json.dumps({
                "error": str(exc),
                "type": type(exc).__name__
            }, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@bp.route('/<plugin_name>', methods=['GET'])
def get_plugin_info(plugin_name: str):
    """
    RÃ©cupÃ¨re les informations dÃ©taillÃ©es d'un plugin.
    
    Args:
        plugin_name (str): Nom du plugin
        
    Returns:
        JSON: Informations complÃ¨tes du plugin incluant metadata
        
    Example:
        GET /api/plugins/caesar
    """
    try:
        manager = get_plugin_manager()
        
        plugin_info = manager.get_plugin_info(plugin_name)
        
        if not plugin_info:
            logger.warning(f"Plugin non trouvÃ©: {plugin_name}")
            return jsonify({
                "error": "Plugin non trouvÃ©",
                "plugin_name": plugin_name
            }), 404
        
        logger.info(f"Informations rÃ©cupÃ©rÃ©es pour plugin: {plugin_name}")
        
        return jsonify(plugin_info), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la rÃ©cupÃ©ration du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la rÃ©cupÃ©ration des informations",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/interface', methods=['GET'])
def get_plugin_interface(plugin_name: str):
    """
    GÃ©nÃ¨re l'interface HTML du formulaire pour un plugin.
    
    L'interface est gÃ©nÃ©rÃ©e dynamiquement Ã  partir des input_types
    dÃ©finis dans le plugin.json.
    
    Args:
        plugin_name (str): Nom du plugin
        
    Returns:
        HTML: Formulaire d'interface du plugin
        
    Example:
        GET /api/plugins/caesar/interface
    """
    try:
        manager = get_plugin_manager()
        
        plugin_info = manager.get_plugin_info(plugin_name)
        
        if not plugin_info:
            return jsonify({
                "error": "Plugin non trouvÃ©",
                "plugin_name": plugin_name
            }), 404
        
        # GÃ©nÃ©rer l'interface HTML
        html = _generate_plugin_interface_html(plugin_info)
        
        logger.info(f"Interface gÃ©nÃ©rÃ©e pour plugin: {plugin_name}")
        
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la gÃ©nÃ©ration de l'interface pour {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la gÃ©nÃ©ration de l'interface",
            "message": str(e)
        }), 500


# =============================================================================
# Routes d'exÃ©cution
# =============================================================================

@bp.route('/<plugin_name>/execute', methods=['POST'])
def execute_plugin(plugin_name: str):
    """
    ExÃ©cute un plugin de maniÃ¨re synchrone.
    
    Cette route est adaptÃ©e pour les plugins rapides (< 1s).
    Pour les plugins longs, utiliser /api/tasks (Phase 2.2).
    
    Args:
        plugin_name (str): Nom du plugin Ã  exÃ©cuter
        
    Request Body (JSON):
        inputs (dict): ParamÃ¨tres d'entrÃ©e du plugin
        
    Returns:
        JSON: RÃ©sultat de l'exÃ©cution au format standardisÃ©
        
    Example:
        POST /api/plugins/caesar/execute
        {
            "inputs": {
                "text": "HELLO",
                "mode": "encode",
                "shift": 13
            }
        }
    """
    try:
        manager = get_plugin_manager()
        
        # RÃ©cupÃ©rer les inputs depuis le body JSON (gestion explicite des erreurs JSON)
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            return jsonify({
                "error": "JSON invalide",
                "message": f"Le body de la requÃªte doit Ãªtre un JSON valide: {str(json_error)}"
            }), 400
        
        if not data or 'inputs' not in data:
            return jsonify({
                "error": "RequÃªte invalide",
                "message": "Le champ 'inputs' est requis dans le body JSON"
            }), 400
        
        inputs = data['inputs']
        
        logger.info(
            f"ExÃ©cution synchrone du plugin {plugin_name} "
            f"avec inputs: {list(inputs.keys())}"
        )
        
        # ExÃ©cuter le plugin
        result = manager.execute_plugin(plugin_name, inputs)
        
        if not result:
            return jsonify({
                "error": "Erreur d'exÃ©cution",
                "message": f"Le plugin {plugin_name} n'a pas pu Ãªtre exÃ©cutÃ©"
            }), 500
        
        logger.info(
            f"Plugin {plugin_name} exÃ©cutÃ© avec succÃ¨s "
            f"(status: {result.get('status')})"
        )

        try:
            plugin_info = manager.get_plugin_info(plugin_name) or {}
            metadata = plugin_info.get('metadata') if isinstance(plugin_info, dict) else {}
            plugin_enable_scoring = False
            if isinstance(metadata, dict) and 'enable_scoring' in metadata:
                plugin_enable_scoring = bool(metadata.get('enable_scoring'))
            enable_scoring = bool(inputs.get('enable_scoring', plugin_enable_scoring))

            mode = inputs.get('mode')
            mode_str = str(mode).lower() if isinstance(mode, str) else None

            if enable_scoring and isinstance(result, dict) and isinstance(result.get('results'), list):
                items = [item for item in (result.get('results') or []) if isinstance(item, dict)]

                # Preserve original plugin confidence before overwriting
                for item in items:
                    item_metadata = item.get('metadata')
                    if not isinstance(item_metadata, dict):
                        item_metadata = {}
                        item['metadata'] = item_metadata
                    plugin_confidence = item.get('confidence')
                    if plugin_confidence is not None and 'plugin_confidence' not in item_metadata:
                        item_metadata['plugin_confidence'] = plugin_confidence

                if mode_str == 'detect':
                    for item in items:
                        item['confidence'] = 0.0
                elif mode_str == 'encode':
                    pass  # Encode results have deterministic confidence, skip scoring
                else:
                    # Use tiered scoring: fast pre-filter then full score on survivors
                    from gc_backend.plugins.scoring import score_and_rank_results

                    max_results = int(inputs.get('max_results', 25) or 25)
                    ranked = score_and_rank_results(
                        items,
                        top_k=max(max_results, 25),
                        min_score=0.03,
                        fast_reject_threshold=0.01,
                        context={},
                    )
                    result['results'] = ranked
        except Exception as e:
            logger.warning(f"Scoring integration error for {plugin_name}: {e}")
         
        # Tracking : si le plugin s'exÃ©cute avec succÃ¨s sur une gÃ©ocache, enregistrer dans l'archive
        try:
            geocache_id_raw = inputs.get('geocache_id')
            has_results = bool(result.get('results')) or result.get('status') == 'success'
            if geocache_id_raw and has_results:
                geocache_for_tracking = Geocache.query.get(int(geocache_id_raw))
                if geocache_for_tracking:
                    from ..geocaches.archive_service import ArchiveService
                    ArchiveService.add_resolution_plugin(geocache_for_tracking.gc_code, plugin_name)
        except Exception:
            pass  # Le tracking ne doit jamais bloquer l'exÃ©cution du plugin

        return jsonify(result), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de l'exÃ©cution du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur d'exÃ©cution",
            "message": str(e)
        }), 500


# =============================================================================
# Routes de gestion
# =============================================================================

@bp.route('/discover', methods=['POST'])
def discover_plugins():
    """
    RedÃ©clenche la dÃ©couverte des plugins.
    
    Scanne les rÃ©pertoires plugins/official/ et plugins/custom/
    pour dÃ©couvrir les nouveaux plugins ou dÃ©tecter les modifications.
    
    Returns:
        JSON: {
            "discovered": nombre de plugins dÃ©couverts,
            "plugins": liste des plugins,
            "errors": erreurs Ã©ventuelles
        }
        
    Example:
        POST /api/plugins/discover
    """
    try:
        manager = get_plugin_manager()
        
        logger.info("DÃ©clenchement de la dÃ©couverte de plugins")
        
        # Lancer la dÃ©couverte
        discovered = manager.discover_plugins()
        
        # RÃ©cupÃ©rer les erreurs
        errors = manager.get_discovery_errors()
        
        logger.info(
            f"DÃ©couverte terminÃ©e: {len(discovered)} plugins, "
            f"{len(errors)} erreurs"
        )
        
        return jsonify({
            "discovered": len(discovered),
            "plugins": discovered,
            "errors": errors,
            "message": f"{len(discovered)} plugin(s) dÃ©couvert(s)"
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la dÃ©couverte: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la dÃ©couverte",
            "message": str(e)
        }), 500


@bp.route('/status', methods=['GET'])
def get_plugins_status():
    """
    RÃ©cupÃ¨re le statut de tous les plugins (enabled, loaded, errors).
    
    Returns:
        JSON: {
            "plugins": {
                "plugin_name": {
                    "enabled": bool,
                    "loaded": bool,
                    "error": str or null,
                    ...
                }
            }
        }
        
    Example:
        GET /api/plugins/status
    """
    try:
        manager = get_plugin_manager()
        
        status = manager.get_plugin_status()
        
        logger.info(f"Statut rÃ©cupÃ©rÃ© pour {len(status)} plugins")
        
        return jsonify({
            "plugins": status,
            "total": len(status),
            "loaded": sum(1 for p in status.values() if p['loaded']),
            "enabled": sum(1 for p in status.values() if p['enabled'])
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la rÃ©cupÃ©ration du statut: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la rÃ©cupÃ©ration du statut",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/reload', methods=['POST'])
def reload_plugin(plugin_name: str):
    """
    Recharge un plugin (dÃ©charge puis recharge).
    
    Utile aprÃ¨s modification du code du plugin.
    
    Args:
        plugin_name (str): Nom du plugin Ã  recharger
        
    Returns:
        JSON: {
            "success": bool,
            "message": str
        }
        
    Example:
        POST /api/plugins/caesar/reload
    """
    try:
        manager = get_plugin_manager()
        
        logger.info(f"Rechargement du plugin: {plugin_name}")
        
        success = manager.reload_plugin(plugin_name)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Plugin {plugin_name} rechargÃ© avec succÃ¨s"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Ã‰chec du rechargement du plugin {plugin_name}"
            }), 500
            
    except Exception as e:
        logger.error(
            f"Erreur lors du rechargement du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors du rechargement",
            "message": str(e)
        }), 500


# =============================================================================
# Utilitaires de gÃ©nÃ©ration HTML
# =============================================================================

def _generate_plugin_interface_html(plugin_info: Dict[str, Any]) -> str:
    """
    GÃ©nÃ¨re l'interface HTML d'un plugin Ã  partir de ses mÃ©tadonnÃ©es.
    
    Args:
        plugin_info (dict): Informations du plugin incluant metadata
        
    Returns:
        str: Code HTML du formulaire
    """
    metadata = plugin_info.get('metadata', {})
    input_types = metadata.get('input_types', {})
    
    # Template HTML de base
    template = '''
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ plugin_name }} - Interface</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
            margin: 0;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        .header {
            background-color: #252526;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
        h1 {
            margin: 0 0 10px 0;
            color: #569cd6;
        }
        .description {
            color: #858585;
            margin: 10px 0;
        }
        .categories {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }
        .category-badge {
            background-color: #3e3e42;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            color: #cccccc;
        }
        .form-container {
            background-color: #252526;
            padding: 20px;
            border-radius: 8px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #cccccc;
            font-weight: 500;
        }
        input[type="text"],
        input[type="number"],
        textarea,
        select {
            width: 100%;
            padding: 10px;
            background-color: #3c3c3c;
            border: 1px solid #555555;
            border-radius: 4px;
            color: #d4d4d4;
            font-size: 14px;
            box-sizing: border-box;
        }
        input[type="text"]:focus,
        input[type="number"]:focus,
        textarea:focus,
        select:focus {
            outline: none;
            border-color: #007acc;
        }
        input[type="checkbox"] {
            width: 18px;
            height: 18px;
            margin-right: 8px;
        }
        .checkbox-group {
            display: flex;
            align-items: center;
        }
        .button-group {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        button {
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            font-weight: 500;
        }
        .btn-primary {
            background-color: #007acc;
            color: white;
        }
        .btn-primary:hover {
            background-color: #005a9e;
        }
        .btn-secondary {
            background-color: #3e3e42;
            color: #cccccc;
        }
        .btn-secondary:hover {
            background-color: #505050;
        }
        .placeholder {
            color: #858585;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ plugin_name }}</h1>
            <div class="description">{{ description }}</div>
            <div class="description" style="font-size: 12px;">Version {{ version }} - {{ author }}</div>
            <div class="categories">
                {% for category in categories %}
                <span class="category-badge">{{ category }}</span>
                {% endfor %}
            </div>
        </div>
        
        <div class="form-container">
            <form id="plugin-form">
                {% for input_name, input_config in input_types.items() %}
                <div class="form-group">
                    <label for="{{ input_name }}">{{ input_config.label }}</label>
                    
                    {% if input_config.type == "string" or input_config.type == "textarea" %}
                        {% if input_config.type == "textarea" %}
                        <textarea 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            placeholder="{{ input_config.placeholder or '' }}"
                            rows="4">{{ input_config.default or '' }}</textarea>
                        {% else %}
                        <input 
                            type="text" 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            placeholder="{{ input_config.placeholder or '' }}"
                            value="{{ input_config.default or '' }}">
                        {% endif %}
                    
                    {% elif input_config.type == "number" or input_config.type == "float" %}
                        <input 
                            type="number" 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            value="{{ input_config.default or 0 }}"
                            min="{{ input_config.min or '' }}"
                            max="{{ input_config.max or '' }}"
                            step="{{ input_config.step or 'any' }}">
                    
                    {% elif input_config.type == "select" %}
                        <select id="{{ input_name }}" name="{{ input_name }}">
                            {% for option in input_config.options %}
                                {% if option is mapping %}
                                <option value="{{ option.value }}" {% if option.value == input_config.default %}selected{% endif %}>
                                    {{ option.label }}
                                </option>
                                {% else %}
                                <option value="{{ option }}" {% if option == input_config.default %}selected{% endif %}>
                                    {{ option }}
                                </option>
                                {% endif %}
                            {% endfor %}
                        </select>
                    
                    {% elif input_config.type == "checkbox" or input_config.type == "boolean" %}
                        <div class="checkbox-group">
                            <input 
                                type="checkbox" 
                                id="{{ input_name }}" 
                                name="{{ input_name }}"
                                {% if input_config.default %}checked{% endif %}>
                            <label for="{{ input_name }}" style="margin-bottom: 0;">
                                {{ input_config.description or input_config.label }}
                            </label>
                        </div>
                    {% endif %}
                </div>
                {% endfor %}
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">ExÃ©cuter</button>
                    <button type="reset" class="btn-secondary">RÃ©initialiser</button>
                </div>
            </form>
        </div>
    </div>
    <script>
        // PrÃ©-remplissage automatique des champs Ã  partir des paramÃ¨tres d'URL (ex: ?text=...)
        document.addEventListener('DOMContentLoaded', function() {
            const urlParams = new URLSearchParams(window.location.search);
            
            // Pour chaque champ du formulaire
            const form = document.getElementById('plugin-form');
            if (form) {
                Array.from(form.elements).forEach(element => {
                    if (element.name && urlParams.has(element.name)) {
                        const paramValue = urlParams.get(element.name);
                        
                        // Gestion spÃ©cifique selon le type
                        if (element.type === 'checkbox') {
                            element.checked = paramValue === 'true' || paramValue === '1' || paramValue === 'on';
                        } else {
                            element.value = paramValue;
                        }
                    }
                });
            }
        });
    </script>
</body>
</html>
    '''
    
    return render_template_string(
        template,
        plugin_name=plugin_info.get('name', 'Unknown'),
        version=plugin_info.get('version', '0.0.0'),
        description=plugin_info.get('description', ''),
        author=plugin_info.get('author', 'Unknown'),
        categories=plugin_info.get('categories', []),
        input_types=input_types
    )

@bp.route('/batch-execute', methods=['POST'])
def batch_execute_plugins():
    """
    ExÃ©cute un plugin sur plusieurs gÃ©ocaches en mode batch.
    
    Request body:
    {
        "plugin_name": "caesar",
        "geocache_ids": [123, 456, 789],
        "inputs": {"mode": "decode", "shift": 3},
        "options": {
            "execution_mode": "sequential",  # ou "parallel"
            "max_concurrency": 3,
            "detect_coordinates": true
        }
    }
    
    Response:
    {
        "task_id": "uuid-string",
        "status": "started",
        "total_geocaches": 3,
        "message": "Batch execution started"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validation des paramÃ¨tres requis
        plugin_name = data.get('plugin_name')
        geocache_ids = data.get('geocache_ids', [])
        inputs = data.get('inputs', {})
        options = data.get('options', {})
        
        if not plugin_name:
            return jsonify({"error": "plugin_name is required"}), 400
        
        if not geocache_ids or not isinstance(geocache_ids, list):
            return jsonify({"error": "geocache_ids must be a non-empty list"}), 400
        
        # Validation du plugin
        plugin_info = _plugin_manager.get_plugin_info(plugin_name)
        if not plugin_info:
            return jsonify({"error": f"Plugin '{plugin_name}' not found"}), 404
        
        # Options par dÃ©faut
        execution_mode = options.get('execution_mode', 'sequential')
        max_concurrency = options.get('max_concurrency', 3)
        detect_coordinates = options.get('detect_coordinates', True)
        include_images = options.get('include_images', False)
        
        # Validation du mode d'exÃ©cution
        if execution_mode not in ['sequential', 'parallel']:
            return jsonify({"error": "execution_mode must be 'sequential' or 'parallel'"}), 400
        
        # CrÃ©er une tÃ¢che batch
        task_id = str(uuid.uuid4())
        
        # RÃ©cupÃ©rer les informations des gÃ©ocaches
        geocaches = []
        for gc_id in geocache_ids:
            geocache = Geocache.query.get(gc_id)
            if geocache:
                decoded_hint = geocache.hints_decoded
                if decoded_hint is None and geocache.hints:
                    decoded_hint = Geocache.decode_hint_rot13(geocache.hints)
                geocaches.append({
                    'id': geocache.id,
                    'gc_code': geocache.gc_code,
                    'name': geocache.name,
                    'description': geocache.description_raw,
                    'hint': decoded_hint,
                    'difficulty': geocache.difficulty,
                    'terrain': geocache.terrain,
                    'images': geocache.images or [],
                    'coordinates': {
                        'latitude': geocache.latitude,
                        'longitude': geocache.longitude,
                        'coordinates_raw': geocache.coordinates_raw
                    } if geocache.latitude and geocache.longitude else None,
                    'waypoints': geocache.waypoints or []
                })
        
        if len(geocaches) != len(geocache_ids):
            found_ids = [g['id'] for g in geocaches]
            missing_ids = [gid for gid in geocache_ids if gid not in found_ids]
            return jsonify({
                "error": f"Some geocaches not found: {missing_ids}",
                "found_count": len(geocaches),
                "requested_count": len(geocache_ids)
            }), 404
        
        # DÃ©marrer la tÃ¢che en arriÃ¨re-plan
        batch_task = BatchPluginTask(
            task_id=task_id,
            plugin_name=plugin_name,
            geocaches=geocaches,
            inputs=inputs,
            execution_mode=execution_mode,
            max_concurrency=max_concurrency,
            detect_coordinates=detect_coordinates,
            app=current_app._get_current_object(),
            include_images=include_images,
        )
        
        # Stocker la tÃ¢che
        batch_tasks[task_id] = batch_task
        
        # DÃ©marrer l'exÃ©cution en arriÃ¨re-plan
        thread = threading.Thread(target=batch_task.execute)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "task_id": task_id,
            "status": "started",
            "total_geocaches": len(geocaches),
            "execution_mode": execution_mode,
            "message": f"Batch execution started for {len(geocaches)} geocaches"
        })
        
    except Exception as e:
        current_app.logger.error(f"Error in batch_execute_plugins: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@bp.route('/batch-status/<task_id>', methods=['GET'])
def get_batch_status(task_id):
    """
    RÃ©cupÃ¨re le statut d'une tÃ¢che batch.
    
    Response:
    {
        "task_id": "uuid-string",
        "status": "running|completed|failed",
        "progress": {
            "completed": 2,
            "total": 3,
            "percentage": 66.7
        },
        "results": [
            {
                "geocache_id": 123,
                "gc_code": "GC123",
                "status": "completed|error",
                "result": {...},
                "error": "Error message if any",
                "execution_time": 1500,
                "coordinates": {
                    "latitude": 48.123,
                    "longitude": 2.456,
                    "formatted": "N 48Â° 07.380 E 002Â° 27.360"
                }
            }
        ],
        "started_at": "2023-...",
        "completed_at": "2023-..."  # si terminÃ©
    }
    """
    if task_id not in batch_tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = batch_tasks[task_id]
    return jsonify(task.get_status())

@bp.route('/batch-cancel/<task_id>', methods=['POST'])
def cancel_batch_task(task_id):
    """
    Annule une tÃ¢che batch en cours.
    
    Response:
    {
        "message": "Task cancelled successfully"
    }
    """
    if task_id not in batch_tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = batch_tasks[task_id]
    task.cancel()
    
    return jsonify({"message": "Task cancellation requested"})

@bp.route('/batch-list', methods=['GET'])
def list_batch_tasks():
    """
    Liste toutes les tÃ¢ches batch (actives et terminÃ©es).
    
    Response:
    {
        "tasks": [
            {
                "task_id": "uuid",
                "plugin_name": "caesar",
                "status": "completed",
                "total_geocaches": 5,
                "completed_geocaches": 5,
                "started_at": "2023-...",
                "completed_at": "2023-..."
            }
        ]
    }
    """
    tasks_info = []
    for task_id, task in batch_tasks.items():
        tasks_info.append({
            "task_id": task_id,
            "plugin_name": task.plugin_name,
            "status": task.status,
            "total_geocaches": len(task.geocaches),
            "completed_geocaches": len([r for r in task.results if r['status'] == 'completed']),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None
        })
    
    return jsonify({"tasks": tasks_info})
