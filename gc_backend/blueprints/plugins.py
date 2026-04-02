"""
Blueprint pour les endpoints API des plugins.

Ce module expose les routes REST pour :
- Lister les plugins disponibles
- Récupérer les informations d'un plugin
- Générer l'interface HTML d'un plugin
- Exécuter un plugin (mode synchrone)
- Exécuter des plugins en mode batch
- Redéclencher la découverte de plugins
"""

import html
import json
import math
import re
import threading
import time
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, unquote
from flask import Blueprint, jsonify, request, render_template_string, current_app
from loguru import logger
from typing import Dict, Any, List, Optional, Tuple

from ..plugins import PluginManager
from ..database import db
from ..geocaches.models import Geocache


# Créer le blueprint
bp = Blueprint('plugins', __name__, url_prefix='/api/plugins')

# Instance globale du PluginManager (sera initialisée dans create_app)
_plugin_manager: PluginManager = None


def init_plugin_manager(manager: PluginManager):
    """
    Initialise le PluginManager global pour ce blueprint.
    
    Cette fonction doit être appelée depuis create_app() après
    la création du PluginManager.
    
    Args:
        manager (PluginManager): Instance du gestionnaire de plugins
    """
    global _plugin_manager
    _plugin_manager = manager
    logger.info("PluginManager initialisé dans le blueprint plugins")


# Stockage des tâches batch en mémoire (en production, utiliser Redis ou une base de données)
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

GENERIC_IMAGE_FILENAME_TOKENS = frozenset({
    'image', 'img', 'photo', 'picture', 'pict', 'pic', 'scan', 'file',
    'attachment', 'download', 'thumb', 'thumbnail', 'small', 'medium', 'large',
    'original', 'cache', 'geocache', 'waypoint', 'wp', 'gc', 'final', 'listing',
})

NAK_NAK_WORDS = frozenset({
    'NAK', 'NANAK', 'NANANAK', 'NANANANAK',
    'NAK?', 'NAKNAK', 'NAKNAKNAK', 'NAK.',
    'NAKNAK.', 'NAKNAKNAKNAK', 'NAK!',
})

SHADOK_SYLLABLE_PATTERN = re.compile(r'^(?:GA|BU|ZO|MEU|ME)+$', re.IGNORECASE)
TOM_TOM_TOKEN_PATTERN = re.compile(r'^[\\/]{1,5}$')
GOLD_BUG_SYMBOLS = frozenset('0123456789-*,.$();?:[]')
DECIMAL_COORDINATE_PAIR_PATTERN = re.compile(
    r'(-?\d{1,2}(?:[.,]\d+)?)\s*[,;/]\s*(-?\d{1,3}(?:[.,]\d+)?)'
)
DDM_COORDINATE_PAIR_PATTERN = re.compile(r'([NS][^EW]+?)\s+([EW].+)$', re.IGNORECASE)
PI_THEME_PATTERN = re.compile(r'(?<![A-Z0-9])(?:PI(?:\s*-\s*DAY|\s+DAY)?|Π)(?![A-Z0-9])', re.IGNORECASE)


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
    grouped_numeric_tokens = [int(token) for token in re.findall(r'\d+', trimmed)]
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
    looks_like_pi_index_positions = (
        len(grouped_numeric_tokens) >= 6
        and max(grouped_numeric_tokens or [0]) > 26
        and max(grouped_numeric_tokens or [0]) <= 10_000
        and len({token for token in grouped_numeric_tokens}) >= 4
        and bool(re.search(r'[,;:/_-]', trimmed))
    )
    looks_like_bacon = (
        bool(bacon_candidate)
        and len(bacon_candidate) >= 10
        and len(bacon_candidate) % 5 == 0
        and set(bacon_candidate) <= set('AB')
    )
    looks_like_coordinate_fragment = bool(re.search(r'[NSEW]\s*\d|[0-9]+\s*[°º]|[0-9]+[.,][0-9]+', trimmed, re.IGNORECASE))

    dominant_input_kind = _detect_dominant_input_kind(letter_count, digit_count, symbol_count, word_count)

    if looks_like_postnet or looks_like_gold_bug:
        suggested_preset = 'all'
    elif looks_like_morse:
        suggested_preset = 'symbols_only'
    elif looks_like_tom_tom:
        suggested_preset = 'symbols_only'
    elif looks_like_houdini_words or looks_like_nak_nak or looks_like_shadok or looks_like_chemical_symbols:
        suggested_preset = 'words_only'
    elif looks_like_pi_index_positions:
        suggested_preset = 'numeral'
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
        'looks_like_pi_index_positions': looks_like_pi_index_positions,
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
        reasons.append(f"Correspondance directe avec l'entrée {dominant_kind}")
    elif dominant_kind == 'mixed' and candidate_charset in charsets_present:
        score += 18
        reasons.append(f"Compatible avec une entrée mixte contenant {candidate_charset}")
    elif dominant_kind == 'words' and candidate_charset == 'letters':
        score += 15
        reasons.append("Compatible avec un texte composé de mots")
    elif candidate_charset and dominant_kind not in ('mixed', 'empty'):
        score -= 10

    if dominant_kind in ('letters', 'words') and 'substitution' in tags:
        score += 12
        reasons.append("Tag substitution cohérent avec une entrée textuelle")

    if dominant_kind == 'digits' and 'numeral' in tags:
        score += 20
        reasons.append("Tag numeral cohérent avec une entrée numérique")

    if signature.get('looks_like_morse'):
        if _candidate_name_matches(candidate, 'morse'):
            score += 150
            reasons.append("Le texte ressemble fortement à du Morse")
        elif candidate_charset == 'symbols':
            score += 12

    if signature.get('looks_like_binary'):
        if _candidate_name_matches(candidate, 'base', 'binary'):
            score += 100
            reasons.append("Le texte ressemble à une séquence binaire")
        elif 'numeral' in tags:
            score += 20

    if signature.get('looks_like_hex'):
        if _candidate_name_matches(candidate, 'base', 'hex'):
            score += 90
            reasons.append("Le texte ressemble à une séquence hexadécimale")
        elif 'numeral' in tags:
            score += 20

    if signature.get('looks_like_phone_keypad') and _candidate_name_matches(candidate, 't9', 'phone', 'keypad'):
        score += 120
        reasons.append("Le texte ressemble à une saisie type T9")

    if signature.get('looks_like_multitap') and _candidate_name_matches(candidate, 'multitap', 'multi tap'):
        score += 140
        reasons.append("Le texte ressemble à un code Multitap")

    if signature.get('looks_like_chemical_symbols') and _candidate_name_matches(candidate, 'chemical', 'element'):
        score += 140
        reasons.append("Le texte ressemble à des symboles chimiques")

    if signature.get('looks_like_houdini_words') and _candidate_name_matches(candidate, 'houdini'):
        score += 150
        reasons.append("Le texte ressemble à du code Houdini")

    if signature.get('looks_like_nak_nak') and _candidate_name_matches(candidate, 'nak'):
        score += 160
        reasons.append("Le texte ressemble à du code Nak Nak")

    if signature.get('looks_like_shadok') and _candidate_name_matches(candidate, 'shadok'):
        score += 150
        reasons.append("Le texte ressemble à de la numération Shadok")

    if signature.get('looks_like_tom_tom') and _candidate_name_matches(candidate, 'tom'):
        score += 180
        reasons.append("Le texte ressemble à du code Tom Tom")

    if signature.get('looks_like_gold_bug') and _candidate_name_matches(candidate, 'gold', 'scarab'):
        score += 180
        reasons.append("Le texte ressemble à du Gold-Bug")

    if signature.get('looks_like_postnet') and _candidate_name_matches(candidate, 'postnet', 'barcode'):
        score += 260
        reasons.append("Le texte ressemble à un code POSTNET")

    if signature.get('looks_like_prime_sequence') and _candidate_name_matches(candidate, 'prime'):
        score += 130
        reasons.append("Le texte ressemble à une séquence de nombres premiers")

    if signature.get('looks_like_pi_index_positions') and _candidate_name_matches(candidate, 'pi'):
        score += 180
        reasons.append("Le texte ressemble a des positions indexees dans les decimales de Pi")
    elif signature.get('looks_like_pi_index_positions') and 'numeral' in tags:
        score += 18

    if signature.get('looks_like_roman_numerals') and _candidate_name_matches(candidate, 'roman'):
        score += 120
        reasons.append("Le texte ressemble à des chiffres romains")

    if signature.get('looks_like_polybius') and _candidate_name_matches(candidate, 'polybius', 'polybe'):
        score += 140
        reasons.append("Le texte ressemble à des coordonnées Polybe / Polybius")

    if signature.get('looks_like_tap_code') and _candidate_name_matches(candidate, 'tap'):
        score += 120
        reasons.append("Le texte ressemble à du Tap Code")

    if signature.get('looks_like_decimal_sequence') and candidate_charset == 'digits':
        score += 15
        reasons.append("Entrée découpée en groupes numériques")

    if signature.get('looks_like_coordinate_fragment') and _candidate_name_matches(candidate, 'coord', 'gps'):
        score += 60
        reasons.append("Le texte ressemble à un fragment de coordonnées")

    preferred_condition_map = {
        'letters_only': (dominant_kind == 'letters', "Optimisé pour une entrée uniquement en lettres"),
        'digits_only': (dominant_kind == 'digits', "Optimisé pour une entrée uniquement en chiffres"),
        'symbols_only': (dominant_kind == 'symbols', "Optimisé pour une entrée symbolique"),
        'words_only': (dominant_kind == 'words', "Optimisé pour une entrée composée de mots"),
        'mixed_input': (dominant_kind == 'mixed', "Compatible avec une entrée mixte"),
        'grouped_input': (int(signature.get('group_count', 0)) > 1, "Compatible avec une entrée découpée en groupes"),
        'short_input': (int(signature.get('non_space_length', 0)) <= 12, "Pertinent sur des entrées courtes"),
        'long_input': (int(signature.get('non_space_length', 0)) >= 24, "Pertinent sur des entrées longues"),
        'morse_like': (bool(signature.get('looks_like_morse')), "Le motif détecté correspond au Morse"),
        'binary_like': (bool(signature.get('looks_like_binary')), "Le motif détecté correspond à du binaire"),
        'hex_like': (bool(signature.get('looks_like_hex')), "Le motif détecté correspond à de l'hexadécimal"),
        't9_like': (bool(signature.get('looks_like_phone_keypad')), "Le motif détecté correspond à une saisie T9"),
        'chemical_like': (bool(signature.get('looks_like_chemical_symbols')), "Le motif détecté correspond à des symboles chimiques"),
        'houdini_like': (bool(signature.get('looks_like_houdini_words')), "Le motif détecté correspond à du code Houdini"),
        'nak_nak_like': (bool(signature.get('looks_like_nak_nak')), "Le motif détecté correspond à du code Nak Nak"),
        'shadok_like': (bool(signature.get('looks_like_shadok')), "Le motif détecté correspond à de la numération Shadok"),
        'tom_tom_like': (bool(signature.get('looks_like_tom_tom')), "Le motif détecté correspond à du code Tom Tom"),
        'gold_bug_like': (bool(signature.get('looks_like_gold_bug')), "Le motif détecté correspond à du Gold-Bug"),
        'postnet_like': (bool(signature.get('looks_like_postnet')), "Le motif détecté correspond à du POSTNET"),
        'prime_like': (bool(signature.get('looks_like_prime_sequence')), "Le motif détecté correspond à une séquence de nombres premiers"),
        'pi_index_positions_like': (bool(signature.get('looks_like_pi_index_positions')), "Le motif detecte correspond a des positions indexees pour Pi"),
        'roman_like': (bool(signature.get('looks_like_roman_numerals')), "Le motif détecté correspond à des chiffres romains"),
        'a1z26_like': (bool(signature.get('looks_like_a1z26')), "Le motif détecté correspond à un code A1Z26"),
        'tap_code_like': (bool(signature.get('looks_like_tap_code')), "Le motif détecté correspond à du Tap Code"),
        'polybius_like': (bool(signature.get('looks_like_polybius')), "Le motif détecté correspond à du Polybius"),
        'multitap_like': (bool(signature.get('looks_like_multitap')), "Le motif détecté correspond à du Multitap"),
        'bacon_like': (bool(signature.get('looks_like_bacon')), "Le motif détecté correspond à du Bacon"),
        'digit_groups': (bool(signature.get('looks_like_decimal_sequence')), "L'entrée est segmentée en groupes numériques"),
        'coordinate_fragment': (bool(signature.get('looks_like_coordinate_fragment')), "L'entrée ressemble à un fragment de coordonnées"),
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
        reasons.append("Supporte explicitement les entrées groupées")

    if requires_key:
        score -= 18
        reasons.append("Nécessite souvent une clé ou un indice supplémentaire")

    if 'frequent' in tags:
        score += 8
        reasons.append("Code fréquent en géocaching")

    if 'no_key' in tags:
        score += 5
        reasons.append("Ne nécessite pas de clé explicite")

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


SUPPORTED_AUTOMATED_WORKFLOW_STEPS = frozenset({
    'inspect-hidden-html',
    'inspect-images',
    'execute-direct-plugin',
    'execute-metasolver',
    'search-answers',
    'calculate-final-coordinates',
    'validate-with-checker',
})

WORKFLOW_BUDGET_DEFAULTS: Dict[str, Dict[str, Any]] = {
    'general': {
        'max_automated_steps': 1,
        'max_metasolver_runs': 0,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
    'secret_code': {
        'max_automated_steps': 2,
        'max_metasolver_runs': 1,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
    'formula': {
        'max_automated_steps': 3,
        'max_metasolver_runs': 0,
        'max_search_questions': 12,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 1,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
    'checker': {
        'max_automated_steps': 1,
        'max_metasolver_runs': 0,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
    'hidden_content': {
        'max_automated_steps': 1,
        'max_metasolver_runs': 0,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
    'image_puzzle': {
        'max_automated_steps': 1,
        'max_metasolver_runs': 0,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 3,
        'stop_on_checker_success': True,
    },
    'coord_transform': {
        'max_automated_steps': 1,
        'max_metasolver_runs': 0,
        'max_search_questions': 0,
        'max_checker_runs': 1,
        'max_coordinate_calculations': 0,
        'max_vision_ocr_runs': 0,
        'stop_on_checker_success': True,
    },
}


def _normalize_positive_int(value: Any, default: int, *, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return maximum
    return parsed


def _normalize_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'oui', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'non', 'off'}:
            return False
    return default


LISTING_CLASSIFICATION_ACTIONS: Dict[str, str] = {
    'secret_code': "Extract the most structured fragment, run a direct plugin if the family is obvious, otherwise call recommend_metasolver_plugins before metasolver.",
    'hidden_content': "Inspect HTML comments, hidden styles, CSS-hidden selectors and page source before trying decoders.",
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


def _contains_pi_theme(*values: Any) -> bool:
    combined = '\n'.join(str(value or '') for value in values if value)
    return bool(combined and PI_THEME_PATTERN.search(combined))


def _extract_pi_coordinate_position_sequences(*values: Any) -> Optional[Dict[str, Any]]:
    ordered_axes = ('N', 'S', 'E', 'W')
    axis_lines: Dict[str, str] = {}
    axis_positions: Dict[str, List[int]] = {}

    for value in values:
        if not value:
            continue
        text = str(value)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if len(line) < 3 or line[0].upper() not in ordered_axes:
                continue
            if any(marker in line for marker in ('°', 'º')):
                continue
            axis = line[0].upper()
            body = line[1:].strip()
            if not body or re.search(r'[A-DF-Z]{2,}', body, flags=re.IGNORECASE):
                continue
            positions = [int(token) for token in re.findall(r'\d{1,4}', body)]
            if len(positions) < 6 or any(position <= 0 for position in positions):
                continue
            axis_positions[axis] = positions
            axis_lines[axis] = f"{axis} " + ','.join(str(position) for position in positions)

    if not axis_positions:
        return None

    source_lines = [axis_lines[axis] for axis in ordered_axes if axis in axis_lines]
    return {
        'axes': [axis for axis in ordered_axes if axis in axis_positions],
        'axis_positions': axis_positions,
        'source_text': '\n'.join(source_lines),
        'total_positions': sum(len(values) for values in axis_positions.values()),
    }


HIDDEN_STYLE_MARKER_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'display\s*:\s*none', flags=re.IGNORECASE), 'display:none'),
    (re.compile(r'visibility\s*:\s*hidden', flags=re.IGNORECASE), 'visibility:hidden'),
    (re.compile(r'opacity\s*:\s*0(?:[^\d]|$)', flags=re.IGNORECASE), 'opacity:0'),
    (re.compile(r'font-size\s*:\s*0(?:px|em|rem|pt|%)?', flags=re.IGNORECASE), 'font-size:0'),
    (re.compile(r'color\s*:\s*transparent', flags=re.IGNORECASE), 'color:transparent'),
)


def _extract_hidden_style_markers(style_text: str) -> List[str]:
    normalized = str(style_text or '')
    markers = [
        marker
        for pattern, marker in HIDDEN_STYLE_MARKER_PATTERNS
        if pattern.search(normalized)
    ]
    if (
        re.search(r'position\s*:\s*(?:absolute|fixed)', normalized, flags=re.IGNORECASE)
        and re.search(
            r'(?:left|right|top|bottom|text-indent)\s*:\s*-\d{2,}(?:px|em|rem|pt|%)?',
            normalized,
            flags=re.IGNORECASE,
        )
    ):
        markers.append('offscreen positioning')
    return list(dict.fromkeys(markers))


def _format_hidden_style_signal(marker: str) -> str:
    return f"{marker} detected"


def _normalize_remote_resource_url(url: str) -> str:
    normalized = str(url or '').strip()
    if normalized.startswith('//'):
        return f'https:{normalized}'
    if normalized.startswith('/'):
        return f'https://www.geocaching.com{normalized}'
    return normalized


def _fetch_remote_text(
    url: str,
    *,
    timeout_sec: int = 5,
    max_bytes: int = 200_000,
) -> str:
    normalized_url = _normalize_remote_resource_url(url)
    if not normalized_url:
        return ''

    try:
        import requests  # type: ignore
    except Exception:
        return ''

    try:
        response = requests.get(normalized_url, timeout=timeout_sec)
    except Exception:
        return ''
    if response.status_code != 200:
        return ''

    raw_bytes = response.content or b''
    if max_bytes > 0 and len(raw_bytes) > max_bytes:
        raw_bytes = raw_bytes[:max_bytes]

    encoding = getattr(response, 'encoding', None) or getattr(response, 'apparent_encoding', None) or 'utf-8'
    try:
        return raw_bytes.decode(encoding, errors='ignore')
    except Exception:
        try:
            return raw_bytes.decode('utf-8', errors='ignore')
        except Exception:
            return ''


def _extract_external_stylesheet_blocks(description_html: str) -> List[Dict[str, str]]:
    raw_html = description_html or ''
    stylesheet_blocks: List[Dict[str, str]] = []
    seen_urls: set = set()

    for link_html in re.findall(r'<link\b[^>]*>', raw_html, flags=re.IGNORECASE):
        rel_value = _extract_html_tag_attribute(link_html, 'rel').lower()
        href_value = _extract_html_tag_attribute(link_html, 'href')
        if 'stylesheet' not in rel_value or not href_value:
            continue
        normalized_url = _normalize_remote_resource_url(href_value)
        if not normalized_url or normalized_url in seen_urls:
            continue
        css_text = _fetch_remote_text(normalized_url)
        if not css_text.strip():
            continue
        stylesheet_blocks.append({
            'source': 'external_stylesheet',
            'url': normalized_url,
            'css_text': css_text,
        })
        seen_urls.add(normalized_url)
        if len(stylesheet_blocks) >= 3:
            break

    return stylesheet_blocks


def _parse_hidden_css_selector_component(selector: str) -> Optional[Dict[str, Any]]:
    normalized = str(selector or '').strip()
    if not normalized or normalized == '*':
        return None
    match = re.fullmatch(
        r'(?:(?P<tag>[a-z][a-z0-9_-]*))?(?P<parts>(?:[#.][A-Za-z_][\w-]*)*)',
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    tag_name = str(match.group('tag') or '').strip().lower()
    parts = re.findall(r'([#.])([A-Za-z_][\w-]*)', normalized)
    class_names = sorted({
        name.strip().lower()
        for prefix, name in parts
        if prefix == '.'
    })
    ids = sorted({
        name.strip().lower()
        for prefix, name in parts
        if prefix == '#'
    })
    if len(ids) > 1:
        return None
    if not tag_name and not class_names and not ids:
        return None

    return {
        'selector': normalized,
        'tag': tag_name,
        'classes': class_names,
        'element_id': ids[0] if ids else '',
    }


def _parse_hidden_css_selector(selector: str) -> Optional[Dict[str, Any]]:
    normalized = re.sub(r'\s*>\s*', ' > ', str(selector or '').strip())
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    if not normalized:
        return None
    if any(token in normalized for token in ('+', '~', ':', '[')):
        return None

    tokens = normalized.split(' ')
    steps: List[Dict[str, Any]] = []
    combinators: List[str] = []
    expect_selector = True
    pending_combinator = 'descendant'

    for token in tokens:
        if token == '>':
            if expect_selector:
                return None
            pending_combinator = 'child'
            expect_selector = True
            continue

        component = _parse_hidden_css_selector_component(token)
        if not component:
            return None
        if steps:
            combinators.append(pending_combinator if expect_selector else 'descendant')
        steps.append(component)
        pending_combinator = 'descendant'
        expect_selector = False

    if not steps or expect_selector:
        return None

    last_step = steps[-1]
    return {
        'selector': normalized,
        'steps': steps,
        'combinators': combinators,
        'tag': last_step.get('tag') or '',
        'classes': list(last_step.get('classes') or []),
        'element_id': last_step.get('element_id') or '',
    }


def _extract_hidden_css_rules(description_html: str) -> List[Dict[str, Any]]:
    raw_html = description_html or ''
    rules: List[Dict[str, Any]] = []
    seen_rules: set = set()
    stylesheet_blocks = [
        {
            'source': 'inline_style',
            'url': '',
            'css_text': style_block,
        }
        for style_block in re.findall(r'<style\b[^>]*>(.*?)</style>', raw_html, flags=re.IGNORECASE | re.DOTALL)
    ]
    stylesheet_blocks.extend(_extract_external_stylesheet_blocks(raw_html))

    for stylesheet_block in stylesheet_blocks:
        css_text = re.sub(r'/\*.*?\*/', ' ', str(stylesheet_block.get('css_text') or ''), flags=re.DOTALL)
        stylesheet_source = str(stylesheet_block.get('source') or 'inline_style')
        stylesheet_url = str(stylesheet_block.get('url') or '').strip()
        for selector_block, declarations in re.findall(r'([^{}]+)\{([^{}]+)\}', css_text):
            markers = _extract_hidden_style_markers(declarations)
            if not markers:
                continue
            for raw_selector in selector_block.split(','):
                parsed_selector = _parse_hidden_css_selector(raw_selector)
                if not parsed_selector:
                    continue
                dedupe_key = (
                    parsed_selector['selector'],
                    tuple(
                        (
                            str(step.get('tag') or ''),
                            tuple(step.get('classes') or []),
                            str(step.get('element_id') or ''),
                        )
                        for step in (parsed_selector.get('steps') or [])
                    ),
                    tuple(parsed_selector.get('combinators') or []),
                    stylesheet_source,
                    stylesheet_url,
                    tuple(markers),
                )
                if dedupe_key in seen_rules:
                    continue
                rules.append({
                    **parsed_selector,
                    'stylesheet_source': stylesheet_source,
                    'stylesheet_url': stylesheet_url,
                    'markers': markers,
                })
                seen_rules.add(dedupe_key)
    return rules


def _descriptor_matches_hidden_selector_step(
    descriptor: Dict[str, Any],
    step: Dict[str, Any],
) -> bool:
    descriptor_tag = str(descriptor.get('tag') or '').strip().lower()
    step_tag = str(step.get('tag') or '').strip().lower()
    if step_tag and descriptor_tag != step_tag:
        return False

    descriptor_id = str(descriptor.get('element_id') or '').strip().lower()
    step_id = str(step.get('element_id') or '').strip().lower()
    if step_id and descriptor_id != step_id:
        return False

    descriptor_classes = {
        str(token or '').strip().lower()
        for token in (descriptor.get('classes') or [])
        if str(token or '').strip()
    }
    step_classes = {
        str(token or '').strip().lower()
        for token in (step.get('classes') or [])
        if str(token or '').strip()
    }
    return not step_classes or step_classes.issubset(descriptor_classes)


def _match_hidden_css_rules(
    tag_name: str,
    attrs: Dict[str, str],
    ancestry: List[Dict[str, Any]],
    hidden_css_rules: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    current_descriptor = {
        'tag': str(tag_name or '').strip().lower(),
        'classes': sorted({
            token.strip().lower()
            for token in re.split(r'\s+', str(attrs.get('class') or '').strip())
            if token.strip()
        }),
        'element_id': str(attrs.get('id') or '').strip().lower(),
    }

    matched_rules: List[Dict[str, Any]] = []
    for rule in hidden_css_rules:
        steps = [
            step
            for step in (rule.get('steps') or [])
            if isinstance(step, dict)
        ]
        combinators = [
            str(item or '').strip().lower()
            for item in (rule.get('combinators') or [])
        ]
        if not steps:
            continue
        if not _descriptor_matches_hidden_selector_step(current_descriptor, steps[-1]):
            continue

        ancestor_index = len(ancestry) - 1
        matched = True
        for step_index in range(len(steps) - 2, -1, -1):
            combinator = combinators[step_index] if step_index < len(combinators) else 'descendant'
            if combinator == 'child':
                if ancestor_index < 0 or not _descriptor_matches_hidden_selector_step(ancestry[ancestor_index], steps[step_index]):
                    matched = False
                    break
                ancestor_index -= 1
                continue

            found_index = -1
            for candidate_index in range(ancestor_index, -1, -1):
                if _descriptor_matches_hidden_selector_step(ancestry[candidate_index], steps[step_index]):
                    found_index = candidate_index
                    break
            if found_index < 0:
                matched = False
                break
            ancestor_index = found_index - 1

        if matched:
            matched_rules.append(rule)

    return matched_rules


class _HiddenContentHtmlParser(HTMLParser):
    def __init__(self, *, hidden_css_rules: List[Dict[str, Any]], register_hidden_item):
        super().__init__(convert_charrefs=True)
        self.hidden_css_rules = hidden_css_rules
        self.register_hidden_item = register_hidden_item
        self.stack: List[Dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._push_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self._push_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = str(tag or '').strip().lower()
        while self.stack:
            entry = self.stack.pop()
            self._flush_entry(entry)
            if str(entry.get('tag') or '').strip().lower() == normalized_tag:
                break

    def handle_data(self, data: str) -> None:
        if not data:
            return
        for entry in reversed(self.stack):
            if entry.get('capture'):
                entry.setdefault('parts', []).append(data)
                break

    def close(self) -> None:
        super().close()
        while self.stack:
            self._flush_entry(self.stack.pop())

    def _push_tag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
        *,
        self_closing: bool = False,
    ) -> None:
        normalized_tag = str(tag or '').strip().lower()
        parent_hidden = bool(self.stack[-1].get('context_hidden')) if self.stack else False
        attr_map = {
            str(name or '').strip().lower(): str(value or '').strip()
            for name, value in attrs
            if str(name or '').strip()
        }
        class_tokens = sorted({
            token.strip().lower()
            for token in re.split(r'\s+', str(attr_map.get('class') or '').strip())
            if token.strip()
        })
        current_descriptor = {
            'tag': normalized_tag,
            'classes': class_tokens,
            'element_id': str(attr_map.get('id') or '').strip().lower(),
        }

        own_source = ''
        own_reason = ''

        style_markers = _extract_hidden_style_markers(attr_map.get('style') or '')
        if style_markers:
            own_source = 'hidden_html_text'
            own_reason = 'Inline hidden style element text extracted'
        elif any(str(name or '').strip().lower() == 'hidden' for name, _ in attrs):
            own_source = 'hidden_html_text'
            own_reason = 'Hidden attribute element text extracted'
        elif str(attr_map.get('aria-hidden') or '').strip().lower() in {'true', '1', 'yes'}:
            own_source = 'hidden_html_text'
            own_reason = 'ARIA-hidden element text extracted'
        else:
            ancestry = [
                dict(entry.get('descriptor') or {})
                for entry in self.stack
                if isinstance(entry.get('descriptor'), dict)
            ]
            matched_css_rules = _match_hidden_css_rules(normalized_tag, attr_map, ancestry, self.hidden_css_rules)
            if matched_css_rules:
                own_source = 'hidden_css_text'
                selector_preview = ', '.join(
                    str(rule.get('selector') or '').strip()
                    for rule in matched_css_rules[:2]
                    if str(rule.get('selector') or '').strip()
                )
                markers = list(dict.fromkeys(
                    marker
                    for rule in matched_css_rules
                    for marker in (rule.get('markers') or [])
                    if str(marker or '').strip()
                ))
                marker_preview = ', '.join(markers[:2])
                uses_external_stylesheet = any(
                    str(rule.get('stylesheet_source') or '').strip() == 'external_stylesheet'
                    for rule in matched_css_rules
                )
                own_reason = "CSS-hidden element text extracted"
                if selector_preview:
                    own_reason += f" via {selector_preview}"
                if marker_preview:
                    own_reason += f" ({marker_preview})"
                if uses_external_stylesheet:
                    own_reason += " from external stylesheet"

        entry = {
            'tag': normalized_tag,
            'context_hidden': parent_hidden or bool(own_source),
            'capture': bool(own_source) and not parent_hidden and normalized_tag not in {'script', 'style'},
            'source': own_source,
            'reason': own_reason,
            'descriptor': current_descriptor,
            'parts': [],
        }
        self.stack.append(entry)

        if self_closing:
            self._flush_entry(self.stack.pop())

    def _flush_entry(self, entry: Dict[str, Any]) -> None:
        if not entry.get('capture'):
            return
        text = ''.join(entry.get('parts') or [])
        self.register_hidden_item(
            text,
            str(entry.get('source') or 'hidden_html_text'),
            str(entry.get('reason') or 'Hidden element text extracted'),
        )


def _extract_hidden_content_signals(description_html: str) -> Dict[str, Any]:
    raw_html = description_html or ''
    signals: List[str] = []
    hidden_items: List[Dict[str, str]] = []
    seen_hidden_items: set = set()

    def register_hidden_item(text: str, source: str, reason: str) -> None:
        normalized = _clean_listing_text(text, preserve_lines=False)
        if len(normalized) < 2:
            return
        dedupe_key = f"{source}:{normalized.lower()}"
        if dedupe_key in seen_hidden_items:
            return
        hidden_items.append({
            'source': source,
            'reason': reason,
            'text': normalized[:160],
        })
        seen_hidden_items.add(dedupe_key)

    comments = [
        _clean_listing_text(match, preserve_lines=False)
        for match in re.findall(r'<!--(.*?)-->', raw_html, flags=re.DOTALL)
    ]
    comments = [comment[:160] for comment in comments if comment]
    if comments:
        signals.append("HTML comments present")
        for comment in comments:
            register_hidden_item(comment, 'html_comment', 'HTML comment extracted')

    for marker in _extract_hidden_style_markers(raw_html):
        signals.append(_format_hidden_style_signal(marker))
    if re.search(r'<[^>]+\bhidden\b', raw_html, flags=re.IGNORECASE):
        signals.append("hidden attribute detected")
    if re.search(r'\baria-hidden\s*=\s*["\']?(?:true|1|yes)\b', raw_html, flags=re.IGNORECASE):
        signals.append("aria-hidden detected")

    hidden_css_rules = _extract_hidden_css_rules(raw_html)
    if hidden_css_rules:
        signals.append(f"{len(hidden_css_rules)} hidden CSS selector(s) detected")
        external_stylesheet_urls = list(dict.fromkeys(
            str(rule.get('stylesheet_url') or '').strip()
            for rule in hidden_css_rules
            if str(rule.get('stylesheet_source') or '').strip() == 'external_stylesheet'
            and str(rule.get('stylesheet_url') or '').strip()
        ))
        if external_stylesheet_urls:
            signals.append(f"{len(external_stylesheet_urls)} external stylesheet(s) inspected")
        css_markers = list(dict.fromkeys(
            marker
            for rule in hidden_css_rules
            for marker in (rule.get('markers') or [])
            if str(marker or '').strip()
        ))
        for marker in css_markers[:2]:
            signals.append(f"Hidden CSS selector uses {marker}")

    hidden_text_patterns = (
        (
            re.compile(
                r'<(?P<tag>[a-z0-9]+)\b[^>]*style\s*=\s*["\'][^"\']*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:[^\d]|$)|font-size\s*:\s*0(?:px|em|rem|pt|%)?)[^"\']*["\'][^>]*>(?P<content>.*?)</(?P=tag)>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            'hidden_html_text',
            'Hidden styled element text extracted',
        ),
        (
            re.compile(
                r'<(?P<tag>[a-z0-9]+)\b[^>]*\bhidden\b[^>]*>(?P<content>.*?)</(?P=tag)>',
                flags=re.IGNORECASE | re.DOTALL,
            ),
            'hidden_html_text',
            'Hidden attribute element text extracted',
        ),
    )
    for pattern, source_name, reason in hidden_text_patterns:
        for match in pattern.finditer(raw_html):
            register_hidden_item(match.group('content') or '', source_name, reason)

    parser = _HiddenContentHtmlParser(
        hidden_css_rules=hidden_css_rules,
        register_hidden_item=register_hidden_item,
    )
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        pass

    hidden_texts = [
        item['text']
        for item in hidden_items
        if item.get('source') in {'hidden_html_text', 'hidden_css_text'}
    ]
    hidden_text_items = [
        {
            'source': str(item.get('source') or 'hidden_html_text'),
            'text': str(item.get('text') or ''),
        }
        for item in hidden_items
        if item.get('source') in {'hidden_html_text', 'hidden_css_text'}
    ]

    return {
        'signals': list(dict.fromkeys(signals))[:6],
        'comments': comments[:4],
        'hidden_texts': hidden_texts[:6],
        'hidden_text_items': hidden_text_items[:8],
        'items': hidden_items[:8],
    }


def _extract_html_tag_attribute(tag_html: str, attribute_name: str) -> str:
    match = re.search(
        rf'\b{re.escape(attribute_name)}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))',
        tag_html or '',
        flags=re.IGNORECASE,
    )
    if not match:
        return ''
    return html.unescape(next((group for group in match.groups() if group is not None), '') or '').strip()


def _extract_image_url_hint_candidates(image_url: str) -> List[Dict[str, Any]]:
    normalized_url = _normalize_remote_image_url(image_url)
    if not normalized_url:
        return []

    parsed = urlparse(normalized_url)
    image_host = (parsed.netloc or '').strip().lower()
    raw_stem = unquote(Path(parsed.path).stem or '').strip()
    if not raw_stem:
        return []

    def is_generic_filename(value: str) -> bool:
        compact = value.strip().lower()
        if not compact:
            return True
        compact_hex = re.sub(r'[^0-9a-f]+', '', compact)
        if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', compact):
            return True
        if (
            ('geocaching.com' in image_host or 'gcimg.net' in image_host)
            and re.fullmatch(r'[0-9a-f]{24,64}', compact_hex)
        ):
            return True
        if re.fullmatch(r'(?:img|image|photo|picture|pic|scan|file|dsc|wp|gc)[\s._-]*\d{1,8}', compact):
            return True
        tokens = [token for token in re.split(r'[^a-z0-9]+', compact) if token]
        if not tokens:
            return True
        if all(token in GENERIC_IMAGE_FILENAME_TOKENS or token.isdigit() for token in tokens):
            grouped_digits = re.fullmatch(r'\d{1,3}(?:[\s._-]\d{1,3}){2,}', compact)
            morse_like = re.fullmatch(r'[\-._]{4,}', compact)
            return not bool(grouped_digits or morse_like)
        return False

    candidates: List[Dict[str, Any]] = []
    seen_texts: set = set()

    def register_candidate(text: str, reason: str, confidence: float) -> None:
        cleaned = _clean_listing_text(text, preserve_lines=False)
        if len(cleaned) < 3:
            return
        dedupe_key = cleaned.lower()
        if dedupe_key in seen_texts:
            return
        candidates.append({
            'source': 'image_filename_text',
            'reason': reason,
            'text': cleaned[:160],
            'image_url': normalized_url,
            'confidence': confidence,
        })
        seen_texts.add(dedupe_key)

    if not is_generic_filename(raw_stem):
        register_candidate(raw_stem, 'Nom de fichier d image extrait', 0.62)

    normalized_stem = re.sub(r'[_+=]+', ' ', raw_stem)
    normalized_stem = re.sub(r'(?<=\d)[.-](?=\d)', ' ', normalized_stem)
    normalized_stem = re.sub(r'(?<=[A-Za-z])[.-](?=[A-Za-z])', ' ', normalized_stem)
    normalized_stem = re.sub(r'\s+', ' ', normalized_stem).strip()
    if normalized_stem and normalized_stem != raw_stem and not is_generic_filename(normalized_stem):
        register_candidate(normalized_stem, 'Nom de fichier d image normalise', 0.68)

    return candidates[:2]


def _extract_image_listing_items(description_html: str, images: Any) -> Dict[str, Any]:
    raw_html = description_html or ''
    items: List[Dict[str, Any]] = []
    image_urls: List[str] = []
    seen_items: set = set()
    seen_urls: set = set()

    def register_image_url(raw_value: Any) -> str:
        normalized = str(raw_value or '').strip()
        if normalized and normalized not in seen_urls:
            seen_urls.add(normalized)
            image_urls.append(normalized)
            for hint_candidate in _extract_image_url_hint_candidates(normalized):
                register_item(
                    source=str(hint_candidate.get('source') or 'image_filename_text'),
                    reason=str(hint_candidate.get('reason') or 'Nom de fichier d image'),
                    text=str(hint_candidate.get('text') or ''),
                    image_url=str(hint_candidate.get('image_url') or normalized),
                    confidence=hint_candidate.get('confidence') if isinstance(hint_candidate.get('confidence'), (int, float)) else None,
                )
        return normalized

    def register_item(
        *,
        source: str,
        reason: str,
        text: str,
        image_url: str = '',
        confidence: Optional[float] = None,
    ) -> None:
        normalized = _clean_listing_text(text, preserve_lines=False)
        if len(normalized) < 2:
            return
        dedupe_key = f"{source}:{image_url}:{normalized.lower()}"
        if dedupe_key in seen_items:
            return
        item: Dict[str, Any] = {
            'source': source,
            'reason': reason,
            'text': normalized[:160],
        }
        if image_url:
            item['image_url'] = image_url
        if confidence is not None:
            item['confidence'] = round(float(confidence), 3)
        items.append(item)
        seen_items.add(dedupe_key)

    for match in re.finditer(r'<img\b[^>]*>', raw_html, flags=re.IGNORECASE):
        tag_html = match.group(0) or ''
        image_url = register_image_url(_extract_html_tag_attribute(tag_html, 'src'))
        alt_text = _extract_html_tag_attribute(tag_html, 'alt')
        title_text = _extract_html_tag_attribute(tag_html, 'title')
        if alt_text:
            register_item(
                source='image_alt_text',
                reason='Texte alt d image extrait',
                text=alt_text,
                image_url=image_url,
                confidence=1.0,
            )
        if title_text:
            register_item(
                source='image_title_text',
                reason='Titre d image extrait',
                text=title_text,
                image_url=image_url,
                confidence=1.0,
            )

    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, dict):
                image_url = register_image_url(
                    entry.get('url') or entry.get('src') or entry.get('href') or entry.get('image_url')
                )
                alt_text = _clean_listing_text(entry.get('alt') or entry.get('alt_text') or '', preserve_lines=False)
                title_text = _clean_listing_text(entry.get('title') or entry.get('title_text') or '', preserve_lines=False)
                if alt_text:
                    register_item(
                        source='image_alt_text',
                        reason='Texte alt d image fourni',
                        text=alt_text,
                        image_url=image_url,
                        confidence=1.0,
                    )
                if title_text:
                    register_item(
                        source='image_title_text',
                        reason='Titre d image fourni',
                        text=title_text,
                        image_url=image_url,
                        confidence=1.0,
                    )
            else:
                register_image_url(entry)

    return {
        'image_count': max(len(image_urls), len(re.findall(r'<img\b[^>]*>', raw_html, flags=re.IGNORECASE))),
        'image_urls': image_urls[:12],
        'items': items[:12],
    }


def _normalize_remote_image_url(url: str) -> str:
    return _normalize_remote_resource_url(url)


def _normalize_exif_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        for encoding in ('utf-8', 'utf-16-le', 'latin-1'):
            try:
                decoded = value.decode(encoding, errors='ignore').replace('\x00', ' ').strip()
                if decoded:
                    return _clean_listing_text(decoded, preserve_lines=False)
            except Exception:
                continue
        return ''
    if isinstance(value, (list, tuple)):
        return _clean_listing_text(' '.join(str(item) for item in value if item is not None), preserve_lines=False)
    return _clean_listing_text(str(value), preserve_lines=False)


def _gps_ratio_to_float(value: Any) -> Optional[float]:
    try:
        if hasattr(value, 'numerator') and hasattr(value, 'denominator'):
            denominator = float(value.denominator or 0)
            if denominator == 0:
                return None
            return float(value.numerator) / denominator
        if isinstance(value, (tuple, list)) and len(value) == 2:
            denominator = float(value[1] or 0)
            if denominator == 0:
                return None
            return float(value[0]) / denominator
        return float(value)
    except Exception:
        return None


def _extract_exif_gps_coordinates(gps_info: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(gps_info, dict):
        return None
    try:
        from PIL.ExifTags import GPSTAGS  # type: ignore
    except Exception:
        return None

    named_gps: Dict[str, Any] = {}
    for key, value in gps_info.items():
        named_gps[GPSTAGS.get(key, key)] = value

    latitude_values = named_gps.get('GPSLatitude')
    latitude_ref = str(named_gps.get('GPSLatitudeRef') or '').strip().upper()
    longitude_values = named_gps.get('GPSLongitude')
    longitude_ref = str(named_gps.get('GPSLongitudeRef') or '').strip().upper()
    if not latitude_values or not longitude_values or not latitude_ref or not longitude_ref:
        return None

    def convert_triplet(values: Any, ref: str) -> Optional[float]:
        if not isinstance(values, (list, tuple)) or len(values) != 3:
            return None
        degrees = _gps_ratio_to_float(values[0])
        minutes = _gps_ratio_to_float(values[1])
        seconds = _gps_ratio_to_float(values[2])
        if degrees is None or minutes is None or seconds is None:
            return None
        decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
        if ref in {'S', 'W'}:
            decimal *= -1
        return round(decimal, 6)

    latitude = convert_triplet(latitude_values, latitude_ref)
    longitude = convert_triplet(longitude_values, longitude_ref)
    if latitude is None or longitude is None:
        return None

    return {
        'latitude': latitude,
        'longitude': longitude,
        'decimal': f'{latitude}, {longitude}',
    }


def _estimate_vision_ocr_cost_units(image_detail: Optional[Dict[str, Any]]) -> int:
    if not isinstance(image_detail, dict):
        return 1

    width = _normalize_positive_int(image_detail.get('width'), 0, minimum=0, maximum=50000)
    height = _normalize_positive_int(image_detail.get('height'), 0, minimum=0, maximum=50000)
    byte_size = _normalize_positive_int(image_detail.get('byte_size'), 0, minimum=0, maximum=200_000_000)
    megapixels = (width * height) / 1_000_000 if width and height else 0.0

    if megapixels >= 8.0 or byte_size >= 4_000_000:
        return 3
    if megapixels >= 2.5 or byte_size >= 1_500_000:
        return 2
    return 1


def _extract_image_metadata_items(image_urls: List[str]) -> Dict[str, Any]:
    if not image_urls:
        return {'items': [], 'coordinate_candidates': [], 'summaries': [], 'image_details': []}

    try:
        import requests  # type: ignore
        from io import BytesIO
        from PIL import Image  # type: ignore
        from PIL.ExifTags import TAGS  # type: ignore
    except Exception as exc:
        return {
            'items': [],
            'coordinate_candidates': [],
            'summaries': [f'EXIF indisponible: {exc}'],
            'image_details': [],
        }

    items: List[Dict[str, Any]] = []
    coordinate_candidates: List[Dict[str, Any]] = []
    summaries: List[str] = []
    image_details: List[Dict[str, Any]] = []
    interesting_tags = (
        'ImageDescription',
        'XPTitle',
        'XPComment',
        'Artist',
        'Copyright',
        'UserComment',
        'Make',
        'Model',
        'Software',
        'DateTimeOriginal',
    )

    for raw_url in image_urls[:6]:
        image_url = _normalize_remote_image_url(raw_url)
        if not image_url:
            continue
        try:
            response = requests.get(image_url, timeout=10)
            if response.status_code != 200:
                continue
            image_bytes = response.content or b''
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
                exif = image.getexif()
        except Exception:
            continue

        image_detail = {
            'image_url': image_url,
            'width': int(width) if isinstance(width, int) else 0,
            'height': int(height) if isinstance(height, int) else 0,
            'byte_size': len(image_bytes),
        }
        image_detail['vision_ocr_cost_units'] = _estimate_vision_ocr_cost_units(image_detail)
        image_details.append(image_detail)

        if not exif:
            continue

        named_exif: Dict[str, Any] = {
            str(TAGS.get(tag_id, tag_id)): value
            for tag_id, value in exif.items()
        }
        found_for_image = 0
        for tag_name in interesting_tags:
            normalized_value = _normalize_exif_value(named_exif.get(tag_name))
            if not normalized_value:
                continue
            items.append({
                'source': 'image_exif_text',
                'reason': f'EXIF {tag_name}',
                'text': normalized_value[:160],
                'image_url': image_url,
                'confidence': 0.9,
            })
            found_for_image += 1

        gps_coordinates = _extract_exif_gps_coordinates(named_exif.get('GPSInfo'))
        if gps_coordinates:
            coordinate_candidates.append({
                'source': 'image_exif_gps',
                'image_url': image_url,
                'confidence': 0.93,
                'coordinates': gps_coordinates,
            })
            found_for_image += 1

        if found_for_image:
            summaries.append(f'EXIF: {found_for_image} indice(s) extrait(s) sur {image_url}')

    return {
        'items': items[:12],
        'coordinate_candidates': coordinate_candidates[:6],
        'summaries': summaries[:6],
        'image_details': image_details[:12],
    }


def _build_secret_fragment_evidence(signature: Dict[str, Any], source_name: str) -> List[str]:
    evidence: List[str] = []
    if source_name == 'html_comment':
        evidence.append("Fragment extracted from an HTML comment")
    if source_name == 'hidden_html_text':
        evidence.append("Fragment extracted from hidden HTML text")
    if source_name == 'hidden_css_text':
        evidence.append("Fragment extracted from CSS-hidden HTML text")
    if source_name == 'image_alt_text':
        evidence.append("Fragment extracted from image alt text")
    if source_name == 'image_title_text':
        evidence.append("Fragment extracted from an image title")
    if source_name == 'image_filename_text':
        evidence.append("Fragment extracted from an image filename")
    if source_name == 'image_ocr_text':
        evidence.append("Fragment extracted from OCR on an image")
    if source_name == 'image_vision_text':
        evidence.append("Fragment extracted from vision OCR on an image")
    if source_name == 'image_barcode_text':
        evidence.append("Fragment extracted from a barcode")
    if source_name == 'image_exif_text':
        evidence.append("Fragment extracted from EXIF metadata")
    if source_name == 'image_qr_text':
        evidence.append("Fragment extracted from a QR code")
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
    if signature.get('looks_like_pi_index_positions'):
        evidence.append("Indexed numeric positions suitable for Pi digits detected")
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
    if signature.get('looks_like_pi_index_positions'):
        score += 54
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
    if source_name == 'hidden_html_text':
        score += 12
    if source_name == 'hidden_css_text':
        score += 13
    if source_name in {'image_alt_text', 'image_title_text'}:
        score += 11
    if source_name == 'image_filename_text':
        score += 8
    if source_name == 'image_ocr_text':
        score += 14
    if source_name == 'image_vision_text':
        score += 13
    if source_name == 'image_barcode_text':
        score += 16
    if source_name == 'image_exif_text':
        score += 9
    if source_name == 'image_qr_text':
        score += 18
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
    hidden_texts: List[str],
    hidden_text_items: Optional[List[Dict[str, str]]] = None,
    supplemental_text_sources: Optional[List[Dict[str, str]]] = None,
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

    if hidden_text_items:
        for hidden_item in hidden_text_items:
            if not isinstance(hidden_item, dict):
                continue
            _register_secret_fragment(
                fragments=fragments,
                seen=seen,
                text=str(hidden_item.get('text') or ''),
                source_name=str(hidden_item.get('source') or 'hidden_html_text'),
                source_kind='hidden_html',
            )
    else:
        for hidden_text in hidden_texts:
            _register_secret_fragment(
                fragments=fragments,
                seen=seen,
                text=hidden_text,
                source_name='hidden_html_text',
                source_kind='hidden_html',
            )

    for supplemental in supplemental_text_sources or []:
        if not isinstance(supplemental, dict):
            continue
        _register_secret_fragment(
            fragments=fragments,
            seen=seen,
            text=str(supplemental.get('text') or ''),
            source_name=str(supplemental.get('source_name') or 'supplemental_text'),
            source_kind=str(supplemental.get('source_kind') or 'supplemental'),
        )

    fragments.sort(key=lambda item: (-item['score'], -item['confidence'], item['source'], item['text']))
    return fragments[:max_fragments]


def _build_hidden_content_execution(
    *,
    listing_inputs: Dict[str, Any],
    data: Dict[str, Any],
    max_secret_fragments: int,
    max_plugins: int,
) -> Dict[str, Any]:
    hidden_info = _extract_hidden_content_signals(listing_inputs.get('description_html') or '')
    candidate_secret_fragments = _extract_secret_fragments(
        title='',
        description='',
        hint='',
        waypoint_text='',
        hidden_comments=hidden_info.get('comments') or [],
        hidden_texts=hidden_info.get('hidden_texts') or [],
        hidden_text_items=hidden_info.get('hidden_text_items') or [],
        max_fragments=max_secret_fragments,
    )
    selected_fragment = candidate_secret_fragments[0] if candidate_secret_fragments else None
    recommendation = None
    if selected_fragment and isinstance(selected_fragment, dict):
        fragment_text = str(selected_fragment.get('text') or '').strip()
        if fragment_text:
            recommendation = _recommend_metasolver_plugins_response(
                text=fragment_text,
                requested_preset=(str(data.get('metasolver_preset') or '')).strip().lower(),
                mode=(str(data.get('metasolver_mode') or 'decode')).strip().lower(),
                max_plugins=max_plugins,
            )

    summary_parts: List[str] = []
    signal_count = len(hidden_info.get('signals') or [])
    item_count = len(hidden_info.get('items') or [])
    if signal_count:
        summary_parts.append(f"{signal_count} signal(s) HTML cache detecte(s)")
    if item_count:
        summary_parts.append(f"{item_count} extrait(s) cache(s)")
    if selected_fragment:
        summary_parts.append(f"Fragment principal: {str(selected_fragment.get('text') or '')[:60]}")
    summary = ' | '.join(summary_parts) or 'Aucun contenu cache exploitable extrait.'

    return {
        'inspected': True,
        'hidden_signals': hidden_info.get('signals') or [],
        'comments': hidden_info.get('comments') or [],
        'hidden_texts': hidden_info.get('hidden_texts') or [],
        'items': hidden_info.get('items') or [],
        'candidate_secret_fragments': candidate_secret_fragments,
        'selected_fragment': selected_fragment,
        'recommendation': recommendation,
        'summary': summary,
    }


def _build_image_puzzle_execution(
    *,
    listing_inputs: Dict[str, Any],
    data: Dict[str, Any],
    max_secret_fragments: int,
    max_plugins: int,
    include_plugin_runs: bool = True,
    inspected: bool = True,
    max_vision_ocr_cost_units: int = 0,
) -> Dict[str, Any]:
    image_info = _extract_image_listing_items(
        listing_inputs.get('description_html') or '',
        listing_inputs.get('images') or [],
    )
    image_items: List[Dict[str, Any]] = [dict(item) for item in (image_info.get('items') or [])]
    plugin_summaries: List[str] = []
    coordinate_candidates: List[Dict[str, Any]] = []
    vision_ocr_images_analyzed = 0
    vision_ocr_budget_cost = 0
    seen_item_keys = {
        f"{str(item.get('source') or '')}:{str(item.get('image_url') or '')}:{str(item.get('text') or '').lower()}"
        for item in image_items
    }
    seen_coordinate_keys: set = set()

    def register_image_item(
        *,
        source: str,
        reason: str,
        text: str,
        image_url: str = '',
        confidence: Optional[float] = None,
    ) -> None:
        normalized = _clean_listing_text(text, preserve_lines=False)
        if len(normalized) < 2:
            return
        dedupe_key = f"{source}:{image_url}:{normalized.lower()}"
        if dedupe_key in seen_item_keys:
            return
        item: Dict[str, Any] = {
            'source': source,
            'reason': reason,
            'text': normalized[:160],
        }
        if image_url:
            item['image_url'] = image_url
        if confidence is not None:
            item['confidence'] = round(float(confidence), 3)
        image_items.append(item)
        seen_item_keys.add(dedupe_key)

    def register_coordinate_candidate(
        candidate: Any,
        *,
        source: str,
        image_url: str = '',
        confidence: Optional[float] = None,
    ) -> None:
        parsed = _extract_decimal_coordinates(candidate)
        if not parsed:
            return
        dedupe_key = f"{round(parsed['latitude'], 6)}:{round(parsed['longitude'], 6)}"
        if dedupe_key in seen_coordinate_keys:
            return
        coordinate_candidates.append({
            'source': source,
            'image_url': image_url or None,
            'confidence': round(float(confidence), 3) if confidence is not None else None,
            'coordinates': candidate if isinstance(candidate, (dict, str)) else {
                'latitude': parsed['latitude'],
                'longitude': parsed['longitude'],
                'decimal': f"{parsed['latitude']}, {parsed['longitude']}",
            },
        })
        seen_coordinate_keys.add(dedupe_key)

    exif_info = _extract_image_metadata_items(image_info.get('image_urls') or [])
    image_details_by_url = {
        str(detail.get('image_url') or ''): detail
        for detail in (exif_info.get('image_details') or [])
        if isinstance(detail, dict) and str(detail.get('image_url') or '')
    }
    for item in exif_info.get('items') or []:
        if not isinstance(item, dict):
            continue
        register_image_item(
            source=str(item.get('source') or 'image_exif_text'),
            reason=str(item.get('reason') or 'EXIF'),
            text=str(item.get('text') or ''),
            image_url=str(item.get('image_url') or ''),
            confidence=item.get('confidence') if isinstance(item.get('confidence'), (int, float)) else None,
        )
    for coordinate in exif_info.get('coordinate_candidates') or []:
        if not isinstance(coordinate, dict):
            continue
        register_coordinate_candidate(
            coordinate.get('coordinates'),
            source=str(coordinate.get('source') or 'image_exif_gps'),
            image_url=str(coordinate.get('image_url') or ''),
            confidence=coordinate.get('confidence') if isinstance(coordinate.get('confidence'), (int, float)) else None,
        )
    plugin_summaries.extend(
        str(summary).strip()
        for summary in (exif_info.get('summaries') or [])
        if str(summary).strip()
    )

    geocache_id = listing_inputs.get('geocache_id')
    explicit_images = listing_inputs.get('images') or []
    if include_plugin_runs and (geocache_id is not None or explicit_images):
        manager = get_plugin_manager()
        base_inputs = {
            'geocache_id': geocache_id,
            'images': explicit_images,
        }

        qr_result = manager.execute_plugin('qr_code_detector', base_inputs)
        qr_summary = str((qr_result or {}).get('summary') or '').strip()
        if qr_summary:
            plugin_summaries.append(f"qr_code_detector: {qr_summary}")
        for item in (qr_result or {}).get('results') or []:
            if not isinstance(item, dict):
                continue
            text_output = str(item.get('text_output') or '').strip()
            image_url = str(item.get('image_url') or '').strip()
            confidence = item.get('confidence')
            barcode_type = str(item.get('barcode_type') or '').strip().upper()
            is_barcode = bool(barcode_type and barcode_type not in {'QRCODE', 'QR'})
            if text_output:
                register_image_item(
                    source='image_barcode_text' if is_barcode else 'image_qr_text',
                    reason=f'Texte decode depuis un code-barres {barcode_type}' if is_barcode else 'Texte decode depuis un QR code',
                    text=text_output,
                    image_url=image_url,
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                )
                register_coordinate_candidate(
                    text_output,
                    source='image_barcode_text' if is_barcode else 'image_qr_text',
                    image_url=image_url,
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                )
            register_coordinate_candidate(
                item.get('coordinates'),
                source='image_barcode_text' if is_barcode else 'image_qr_text',
                image_url=image_url,
                confidence=confidence if isinstance(confidence, (int, float)) else None,
            )
        register_coordinate_candidate(
            (qr_result or {}).get('coordinates') or (qr_result or {}).get('primary_coordinates'),
            source='image_qr_text',
        )

        ocr_result = manager.execute_plugin('easyocr_ocr', base_inputs)
        ocr_summary = str((ocr_result or {}).get('summary') or '').strip()
        if ocr_summary:
            plugin_summaries.append(f"easyocr_ocr: {ocr_summary}")
        for item in (ocr_result or {}).get('results') or []:
            if not isinstance(item, dict):
                continue
            text_output = str(item.get('text_output') or '').strip()
            if not text_output:
                continue
            image_url = str(item.get('image_url') or '').strip()
            confidence = item.get('confidence')
            register_image_item(
                source='image_ocr_text',
                reason='Texte OCR extrait d une image',
                text=text_output,
                image_url=image_url,
                confidence=confidence if isinstance(confidence, (int, float)) else None,
            )
            register_coordinate_candidate(
                text_output,
                source='image_ocr_text',
                image_url=image_url,
                confidence=confidence if isinstance(confidence, (int, float)) else None,
            )

        has_machine_readable_hits = any(
            str(item.get('source') or '') in {'image_qr_text', 'image_barcode_text', 'image_ocr_text'}
            for item in image_items
        )
        if not has_machine_readable_hits:
            if max_vision_ocr_cost_units > 0:
                limited_image_urls: List[str] = []
                total_image_urls = image_info.get('image_urls') or []
                for raw_url in total_image_urls:
                    normalized_url = _normalize_remote_image_url(raw_url)
                    detail = image_details_by_url.get(normalized_url) or image_details_by_url.get(raw_url) or {}
                    cost_units = _estimate_vision_ocr_cost_units(detail)
                    if vision_ocr_budget_cost + cost_units > max_vision_ocr_cost_units:
                        continue
                    limited_image_urls.append(raw_url)
                    vision_ocr_budget_cost += cost_units
                if not limited_image_urls and total_image_urls:
                    plugin_summaries.append('vision_ocr skipped: aucune image ne rentre dans le budget OCR vision restant.')
                if limited_image_urls:
                    vision_inputs = {
                        'images': [{'url': url} for url in limited_image_urls],
                    }
                    if geocache_id is not None:
                        vision_inputs['geocache_id'] = geocache_id
                    if len(limited_image_urls) < len(total_image_urls):
                        plugin_summaries.append(
                            f"vision_ocr limited: {len(limited_image_urls)} image(s) analysee(s) sur {len(total_image_urls)} a cause du budget ({vision_ocr_budget_cost}/{max_vision_ocr_cost_units})."
                        )
                    vision_result = manager.execute_plugin('vision_ocr', vision_inputs)
                    vision_summary = str((vision_result or {}).get('summary') or '').strip()
                    if vision_summary:
                        plugin_summaries.append(f"vision_ocr: {vision_summary}")
                    try:
                        vision_ocr_images_analyzed = max(
                            vision_ocr_images_analyzed,
                            int((vision_result or {}).get('images_analyzed') or 0),
                        )
                    except (TypeError, ValueError):
                        vision_ocr_images_analyzed = max(vision_ocr_images_analyzed, 0)
                    for item in (vision_result or {}).get('results') or []:
                        if not isinstance(item, dict):
                            continue
                        text_output = str(item.get('text_output') or '').strip()
                        if not text_output:
                            continue
                        image_url = str(item.get('image_url') or '').strip()
                        confidence = item.get('confidence')
                        register_image_item(
                            source='image_vision_text',
                            reason='Texte OCR vision extrait d une image',
                            text=text_output,
                            image_url=image_url,
                            confidence=confidence if isinstance(confidence, (int, float)) else None,
                        )
                        register_coordinate_candidate(
                            text_output,
                            source='image_vision_text',
                            image_url=image_url,
                            confidence=confidence if isinstance(confidence, (int, float)) else None,
                        )
            else:
                plugin_summaries.append('vision_ocr skipped: budget OCR vision epuise.')

    candidate_secret_fragments = _extract_secret_fragments(
        title='',
        description='',
        hint='',
        waypoint_text='',
        hidden_comments=[],
        hidden_texts=[],
        supplemental_text_sources=[
            {
                'text': str(item.get('text') or ''),
                'source_name': str(item.get('source') or 'image_text'),
                'source_kind': 'image_analysis',
            }
            for item in image_items
            if str(item.get('text') or '').strip()
        ],
        max_fragments=max_secret_fragments,
    )
    selected_fragment = candidate_secret_fragments[0] if candidate_secret_fragments else None

    recommendation = None
    if selected_fragment and isinstance(selected_fragment, dict):
        fragment_text = str(selected_fragment.get('text') or '').strip()
        if fragment_text:
            recommendation = _recommend_metasolver_plugins_response(
                text=fragment_text,
                requested_preset=(str(data.get('metasolver_preset') or '')).strip().lower(),
                mode=(str(data.get('metasolver_mode') or 'decode')).strip().lower(),
                max_plugins=max_plugins,
            )

    best_coordinate_candidate: Optional[Dict[str, Any]] = None
    best_plausibility: Optional[Dict[str, Any]] = None
    best_coordinate_score = -1.0
    for candidate in coordinate_candidates:
        plausibility = _build_geographic_plausibility(candidate.get('coordinates'), listing_inputs)
        score = float((plausibility or {}).get('score') or 0.0)
        if score > best_coordinate_score:
            best_coordinate_score = score
            best_coordinate_candidate = candidate
            best_plausibility = plausibility

    summary_parts: List[str] = []
    image_count = int(image_info.get('image_count') or 0)
    if image_count:
        summary_parts.append(f"{image_count} image(s) reperee(s)")
    if image_items:
        summary_parts.append(f"{len(image_items)} indice(s) image extrait(s)")
    if selected_fragment:
        summary_parts.append(f"Fragment principal: {str(selected_fragment.get('text') or '')[:60]}")
    if best_plausibility:
        summary_parts.append(
            f"Plausibilite geo: {str(best_plausibility.get('status') or 'unknown')} {(float(best_plausibility.get('score') or 0.0) * 100):.0f}%"
        )
    if plugin_summaries and not selected_fragment:
        summary_parts.append(plugin_summaries[0])
    summary = ' | '.join(summary_parts) or 'Aucun indice image exploitable extrait.'

    return {
        'inspected': bool(inspected),
        'image_count': image_count,
        'image_urls': image_info.get('image_urls') or [],
        'items': image_items[:12],
        'candidate_secret_fragments': candidate_secret_fragments,
        'selected_fragment': selected_fragment,
        'recommendation': recommendation,
        'plugin_summaries': plugin_summaries[:6],
        'vision_ocr_images_analyzed': vision_ocr_images_analyzed,
        'vision_ocr_budget_cost': vision_ocr_budget_cost,
        'coordinates_candidate': (best_coordinate_candidate or {}).get('coordinates') if best_coordinate_candidate else None,
        'geographic_plausibility': best_plausibility,
        'summary': summary,
    }


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
    images: Any,
    checker_count: int,
    waypoint_count: int,
    max_secret_fragments: int,
) -> Dict[str, Any]:
    hidden_info = _extract_hidden_content_signals(description_html)
    hidden_signals = hidden_info.get('signals') or []
    hidden_comments = hidden_info.get('comments') or []
    hidden_texts = hidden_info.get('hidden_texts') or []
    image_info = _extract_image_listing_items(description_html, images)
    image_count = int(image_info.get('image_count') or 0)
    image_items = image_info.get('items') or []
    image_hint_count = len(image_items)
    image_hint_sources = list(dict.fromkeys(
        str(item.get('source') or '')
        for item in image_items
        if str(item.get('source') or '')
    ))[:6]

    combined_text = '\n'.join(part for part in (title, description, hint, waypoint_text) if part).strip()
    combined_lower = combined_text.lower()
    has_pi_theme = _contains_pi_theme(title, description, hint, waypoint_text)
    pi_coordinate_sequences = _extract_pi_coordinate_position_sequences(description, hint, waypoint_text, title)
    pi_position_token_count = int((pi_coordinate_sequences or {}).get('total_positions') or 0)
    pi_coordinate_axes = list((pi_coordinate_sequences or {}).get('axes') or [])

    secret_fragments = _extract_secret_fragments(
        title=title,
        description=description,
        hint=hint,
        waypoint_text=waypoint_text,
        hidden_comments=hidden_comments,
        hidden_texts=hidden_texts,
        hidden_text_items=hidden_info.get('hidden_text_items') or [],
        supplemental_text_sources=[
            {
                'text': str(item.get('text') or ''),
                'source_name': str(item.get('source') or 'image_text'),
                'source_kind': 'image_listing',
            }
            for item in image_items
            if str(item.get('text') or '').strip()
        ],
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
    visual_image_action_keywords = (
        r'\b(inspect|observe|spot|compare|count|zoom|rotate|mirror|flip|colour|color|shape|symbol|pattern|pixel)\b'
    )
    code_keywords = (
        r'\b(code|cipher|decode|decrypt|crypt|enigme|secret|morse|alphabet|substitution|transposition)\b'
    )
    coord_keywords = (
        r'\b(coord(?:onnee|onnee|onn|inate)s?|projection|bearing|distance|waypoint|final|offset|azimuth)\b'
    )
    projection_keywords = (
        r'\b(projection|bearing|distance|waypoint|final|offset|azimuth)\b'
    )

    variable_assignments = re.findall(r'\b[A-Z]{1,3}\s*=\s*[-+*/()0-9A-Z ]{1,40}', combined_text)
    projection_keyword_matches = re.findall(projection_keywords, combined_lower, flags=re.IGNORECASE)
    visual_image_action_matches = re.findall(visual_image_action_keywords, combined_lower, flags=re.IGNORECASE)
    strongest_fragment = secret_fragments[0] if secret_fragments else {}
    strongest_fragment_confidence = float((strongest_fragment or {}).get('confidence') or 0.0)
    strongest_fragment_source = str((strongest_fragment or {}).get('source') or '').strip()
    direct_fragments = [
        fragment for fragment in secret_fragments
        if str((fragment or {}).get('source') or '').strip() in DIRECT_SECRET_FRAGMENT_SOURCES
    ]
    hidden_fragments = [
        fragment for fragment in secret_fragments
        if str((fragment or {}).get('source') or '').strip() in HIDDEN_SECRET_FRAGMENT_SOURCES
    ]
    image_fragments = [
        fragment for fragment in secret_fragments
        if str((fragment or {}).get('source') or '').strip() in IMAGE_SECRET_FRAGMENT_SOURCES
    ]
    best_direct_fragment = direct_fragments[0] if direct_fragments else {}
    best_hidden_fragment = hidden_fragments[0] if hidden_fragments else {}
    best_image_fragment = image_fragments[0] if image_fragments else {}
    image_structured_fragment_count = sum(
        1
        for fragment in secret_fragments
        if str((fragment or {}).get('source') or '').strip() in IMAGE_SECRET_FRAGMENT_SOURCES
    )
    hidden_structured_fragment_count = len(hidden_fragments)
    direct_structured_fragment_count = len(direct_fragments)
    has_visual_only_image_clue = bool(
        image_count > 0
        and visual_image_action_matches
        and image_structured_fragment_count == 0
        and strongest_fragment_confidence < 0.72
    )
    if variable_assignments:
        formula_signals.append(f"{len(variable_assignments)} variable assignment(s) detected")
    if re.search(formula_keywords, combined_lower, flags=re.IGNORECASE):
        formula_signals.append("Formula or coordinate keywords detected")
    if re.search(r'\b[NS]\s*\d', combined_text, flags=re.IGNORECASE) and re.search(r'[A-Z]\s*[+\-*/=]', combined_text):
        formula_signals.append("Coordinate pattern mixed with variables detected")
    has_formula_coordinate_placeholders = (
        bool(re.search(r'\b[NS]\s*\d{1,2}[^.\n]{0,24}\.[A-Z0-9()]{2,}', combined_text, flags=re.IGNORECASE))
        and bool(re.search(r'\b[EW]\s*\d{1,3}[^.\n]{0,24}\.[A-Z0-9()]{2,}', combined_text, flags=re.IGNORECASE))
    )
    if has_formula_coordinate_placeholders:
        formula_signals.append("Coordinate formula placeholders detected")

    formula_score = 0.0
    formula_score += 30.0 if variable_assignments else 0.0
    formula_score += 24.0 if re.search(formula_keywords, combined_lower, flags=re.IGNORECASE) else 0.0
    formula_score += 30.0 if re.search(r'\b[NS]\s*\d', combined_text, flags=re.IGNORECASE) and re.search(r'[A-Z]\s*[+\-*/=]', combined_text) else 0.0
    formula_score += 24.0 if has_formula_coordinate_placeholders else 0.0
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
        secret_score += strongest_fragment.get('score', 0.0)
        secret_evidence.append(
            f"Structured fragment detected in {strongest_fragment.get('source')}: {strongest_fragment.get('text')[:60]}"
        )
    if has_pi_theme and pi_position_token_count:
        secret_score += 30.0
        secret_evidence.append(
            f"Theme Pi detecte avec {pi_position_token_count} positions indexees sur les axes {'/'.join(pi_coordinate_axes or ['listing'])}"
        )
    if re.search(code_keywords, combined_lower, flags=re.IGNORECASE):
        secret_score += 18.0
        secret_evidence.append("Code / cipher vocabulary detected")
    if hint and len(hint.strip()) <= 96 and secret_fragments:
        secret_score += 8.0
        secret_evidence.append("The hint contains a compact candidate fragment")
    if (
        has_visual_only_image_clue
        and strongest_fragment_source in DIRECT_SECRET_FRAGMENT_SOURCES
        and strongest_fragment_confidence < 0.72
    ):
        secret_score -= 14.0
        secret_evidence.append("Visual image inspection cues dominate over the weak visible fragment")
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
    if image_hint_count > 0:
        image_score += min(24.0, 8.0 + 4.0 * image_hint_count)
        image_evidence.append(f"{image_hint_count} indice(s) image textuel(s) extrait(s)")
    if '<img' in (description_html or '').lower():
        image_score += 12.0
        image_evidence.append("Inline image tags detected in listing HTML")
    if re.search(image_keywords, combined_lower, flags=re.IGNORECASE):
        image_score += 18.0
        image_evidence.append("Image / OCR / QR vocabulary detected")
    if has_visual_only_image_clue:
        image_score += 18.0
        image_evidence.append("Visual inspection cues detected even without extracted image text")
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

    direct_domain_score = 0.0
    if best_direct_fragment:
        direct_domain_score += float(best_direct_fragment.get('score') or 0.0)
    if has_pi_theme and pi_position_token_count:
        direct_domain_score += 30.0
    if re.search(code_keywords, combined_lower, flags=re.IGNORECASE):
        direct_domain_score += 18.0
    if hint and len(hint.strip()) <= 96 and best_direct_fragment:
        direct_domain_score += 8.0
    if (
        has_visual_only_image_clue
        and str((best_direct_fragment or {}).get('source') or '').strip() in DIRECT_SECRET_FRAGMENT_SOURCES
        and float((best_direct_fragment or {}).get('confidence') or 0.0) < 0.72
    ):
        direct_domain_score -= 14.0

    hidden_domain_score = float(hidden_score)
    if best_hidden_fragment:
        hidden_domain_score += float(best_hidden_fragment.get('score') or 0.0) * 0.75

    image_domain_score = float(image_score)
    if best_image_fragment:
        image_domain_score += float(best_image_fragment.get('score') or 0.0) * 0.75

    domain_scores = {
        'direct': round(max(0.0, direct_domain_score), 2),
        'hidden': round(max(0.0, hidden_domain_score), 2),
        'image': round(max(0.0, image_domain_score), 2),
    }
    sorted_domain_scores = sorted(domain_scores.items(), key=lambda item: (-item[1], item[0]))
    dominant_evidence_domain = sorted_domain_scores[0][0] if sorted_domain_scores and sorted_domain_scores[0][1] > 0 else None
    second_domain_score = sorted_domain_scores[1][1] if len(sorted_domain_scores) > 1 else 0.0
    evidence_domain_gap = round(max(0.0, float(sorted_domain_scores[0][1] if sorted_domain_scores else 0.0) - float(second_domain_score)), 2)
    hybrid_domain_count = sum(1 for score in domain_scores.values() if float(score) >= 24.0)
    is_hybrid_listing = hybrid_domain_count >= 2
    ambiguous_domains = [
        domain
        for domain, score in sorted_domain_scores
        if float(score) >= 24.0 and dominant_evidence_domain and (float(domain_scores.get(dominant_evidence_domain) or 0.0) - float(score)) < 10.0
    ]
    is_ambiguous_hybrid = is_hybrid_listing and len(ambiguous_domains) >= 2 and evidence_domain_gap < 10.0

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
            'has_pi_theme': has_pi_theme,
            'pi_position_token_count': pi_position_token_count,
            'pi_coordinate_axes': pi_coordinate_axes,
            'image_count': image_count,
            'image_hint_count': image_hint_count,
            'image_hint_sources': image_hint_sources,
            'checker_count': checker_count,
            'waypoint_count': waypoint_count,
            'formula_signal_count': len(formula_signals),
            'variable_assignment_count': len(variable_assignments),
            'has_formula_coordinate_placeholders': has_formula_coordinate_placeholders,
            'projection_keyword_count': len(projection_keyword_matches),
            'visual_image_signal_count': len(visual_image_action_matches),
            'direct_structured_fragment_count': direct_structured_fragment_count,
            'hidden_structured_fragment_count': hidden_structured_fragment_count,
            'image_structured_fragment_count': image_structured_fragment_count,
            'direct_domain_score': domain_scores['direct'],
            'hidden_domain_score': domain_scores['hidden'],
            'image_domain_score': domain_scores['image'],
            'dominant_evidence_domain': dominant_evidence_domain,
            'evidence_domain_gap': evidence_domain_gap,
            'hybrid_domain_count': hybrid_domain_count,
            'is_hybrid_listing': is_hybrid_listing,
            'ambiguous_domains': ambiguous_domains,
            'is_ambiguous_hybrid': is_ambiguous_hybrid,
            'has_visual_only_image_clue': has_visual_only_image_clue,
            'hidden_signal_count': len(hidden_signals),
            'hidden_comment_count': len(hidden_comments),
            'hidden_text_count': len(hidden_texts),
            'secret_fragment_count': len(secret_fragments),
            'best_secret_fragment_source': (secret_fragments[0] or {}).get('source') if secret_fragments else None,
            'best_secret_fragment_confidence': float((secret_fragments[0] or {}).get('confidence') or 0.0) if secret_fragments else 0.0,
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


def _load_listing_analysis_inputs(data: Dict[str, Any]) -> Dict[str, Any]:
    geocache_id = data.get('geocache_id')
    source = 'direct_input'
    metadata: Dict[str, Any] | None = None
    geocache_record: Optional[Geocache] = None

    if geocache_id is not None:
        try:
            geocache_id = int(geocache_id)
        except (TypeError, ValueError):
            raise ValueError("Le champ 'geocache_id' doit etre un entier")

        geocache_record = Geocache.query.get(geocache_id)
        if not geocache_record:
            raise LookupError(f"Aucune geocache avec l'id {geocache_id}")

        payload = _serialize_geocache_listing(geocache_record)
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

    if not any((title, description, description_html, hint, waypoint_text)) and not images:
        raise ValueError("Fournissez au moins un contenu de listing, des images ou un geocache_id")

    return {
        'source': source,
        'metadata': metadata,
        'geocache_id': geocache_id if isinstance(geocache_id, int) else None,
        'geocache_record': geocache_record,
        'title': title,
        'description': description,
        'description_html': description_html,
        'hint': hint,
        'waypoints': waypoints,
        'checkers': checkers,
        'images': images,
        'waypoint_text': waypoint_text,
    }


def _build_listing_classification_response(listing_inputs: Dict[str, Any], max_secret_fragments: int) -> Dict[str, Any]:
    classification = _build_listing_classification(
        title=listing_inputs.get('title') or '',
        description=listing_inputs.get('description') or '',
        description_html=listing_inputs.get('description_html') or '',
        hint=listing_inputs.get('hint') or '',
        waypoint_text=listing_inputs.get('waypoint_text') or '',
        images=listing_inputs.get('images') or [],
        checker_count=len(listing_inputs.get('checkers') or []),
        waypoint_count=len(listing_inputs.get('waypoints') or []),
        max_secret_fragments=max_secret_fragments,
    )

    return {
        'source': listing_inputs.get('source') or 'direct_input',
        'geocache': listing_inputs.get('metadata'),
        'title': listing_inputs.get('title') or None,
        'max_secret_fragments': max_secret_fragments,
        **classification,
    }


def _recommend_metasolver_plugins_response(
    *,
    text: str,
    requested_preset: str = '',
    mode: str = 'decode',
    max_plugins: int = 8,
) -> Dict[str, Any]:
    manager = get_plugin_manager()
    presets = _load_metasolver_presets(manager)
    signature = _analyze_metasolver_signature(text)

    normalized_mode = mode if mode in {'decode', 'detect'} else 'decode'
    effective_preset = requested_preset or signature.get('suggested_preset') or 'frequent'
    if effective_preset not in presets:
        effective_preset = 'all'

    preset_info = presets.get(effective_preset, {})
    preset_filter = preset_info.get('filter') or {}
    candidates = _collect_metasolver_candidates(preset_filter=preset_filter, mode=normalized_mode)
    scored = [_score_metasolver_candidate(candidate, signature) for candidate in candidates]
    scored.sort(key=lambda item: (-item['score'], -item['priority'], item['name']))

    selected = scored[:max_plugins]
    top_score = selected[0]['score'] if selected and selected[0]['score'] else 1.0

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
        f"Plugins recommandes: {len(selected_plugins)} / {len(scored)} eligibles",
    ]

    return {
        'requested_preset': requested_preset or None,
        'effective_preset': effective_preset,
        'effective_preset_label': preset_info.get('label', effective_preset),
        'preset_filter': preset_filter or None,
        'mode': normalized_mode,
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
    }


def _extract_plugin_summary_text(summary: Any) -> str:
    if isinstance(summary, dict):
        for key in ('message', 'summary', 'status'):
            value = str(summary.get(key) or '').strip()
            if value:
                return value
        return ''
    return str(summary or '').strip()


def _summarize_direct_plugin_result(
    plugin_name: str,
    result: Dict[str, Any],
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    raw_results = result.get('results') or []
    top_results: List[Dict[str, Any]] = []
    for item in raw_results[:limit]:
        if not isinstance(item, dict):
            continue
        top_results.append({
            'text_output': item.get('text_output'),
            'coordinates': item.get('coordinates'),
            'confidence': item.get('confidence'),
            'method': item.get('method'),
            'plugin': item.get('plugin') or item.get('source_plugin') or plugin_name,
        })

    coordinates = result.get('coordinates') or result.get('primary_coordinates')
    if not coordinates:
        for item in top_results:
            if item.get('coordinates'):
                coordinates = item.get('coordinates')
                break

    summary_text = _extract_plugin_summary_text(result.get('summary'))
    if not summary_text and top_results:
        summary_text = f"{len(top_results)} resultat(s)"
    if not summary_text:
        summary_text = str(result.get('status') or 'plugin executed')

    return {
        'plugin_name': plugin_name,
        'status': result.get('status'),
        'summary': summary_text,
        'results_count': len(raw_results),
        'top_results': top_results,
        'coordinates': coordinates,
    }


def _direct_plugin_result_succeeded(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get('status') or '').strip().lower()
    return status in {'success', 'ok', 'valid'} and int(result.get('results_count') or 0) > 0


def _build_secret_code_direct_plugin_candidate(
    listing_inputs: Dict[str, Any],
    classification: Dict[str, Any],
    recommendation: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    if not bool(signal_summary.get('has_pi_theme')):
        return None

    sequences = _extract_pi_coordinate_position_sequences(
        listing_inputs.get('description') or '',
        listing_inputs.get('hint') or '',
        listing_inputs.get('waypoint_text') or '',
        listing_inputs.get('title') or '',
    )
    if not sequences:
        return None

    manager = get_plugin_manager()
    plugin_info = manager.get_plugin_info('pi_digits') or {}
    if not plugin_info or not bool(plugin_info.get('enabled', True)):
        return None

    axes = [str(item or '').strip() for item in (sequences.get('axes') or []) if str(item or '').strip()]
    confidence = 0.84
    if axes and {'N', 'E'}.issubset(set(axes)):
        confidence += 0.09
    if PI_THEME_PATTERN.search(str(listing_inputs.get('title') or '')):
        confidence += 0.04
    confidence = round(min(0.99, confidence), 3)

    fallback_plugin_list = list((recommendation or {}).get('selected_plugins') or [])
    if 'pi_digits' not in fallback_plugin_list:
        fallback_plugin_list.insert(0, 'pi_digits')

    return {
        'plugin_name': 'pi_digits',
        'confidence': confidence,
        'reason': (
            "Theme Pi detecte avec une sequence de positions indexees "
            + ('/'.join(axes) if axes else 'dans le listing')
            + "."
        ),
        'source_kind': 'pi_index_positions',
        'source_text': str(sequences.get('source_text') or ''),
        'should_run_directly': confidence >= 0.9,
        'plugin_inputs': {
            'text': str(sequences.get('source_text') or ''),
            'mode': 'decode',
            'format': 'digits_only',
        },
        'axes': axes,
        'position_count': int(sequences.get('total_positions') or 0),
        'fallback_plugin_list': fallback_plugin_list,
    }


def _execute_direct_plugin_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    plugin_name = str(candidate.get('plugin_name') or '').strip()
    plugin_inputs = candidate.get('plugin_inputs') if isinstance(candidate.get('plugin_inputs'), dict) else {}
    raw_result = get_plugin_manager().execute_plugin(plugin_name, plugin_inputs)
    return _summarize_direct_plugin_result(plugin_name, raw_result or {})


def _normalize_workflow_kind(value: Any) -> Optional[str]:
    normalized = str(value or '').strip().lower()
    if normalized in {'general', 'secret_code', 'formula', 'checker', 'hidden_content', 'image_puzzle', 'coord_transform'}:
        return normalized
    return None


IMAGE_SECRET_FRAGMENT_SOURCES = frozenset({
    'image_alt_text',
    'image_title_text',
    'image_filename_text',
    'image_ocr_text',
    'image_vision_text',
    'image_barcode_text',
    'image_exif_text',
    'image_qr_text',
})

HIDDEN_SECRET_FRAGMENT_SOURCES = frozenset({
    'html_comment',
    'hidden_html_text',
    'hidden_css_text',
})

DIRECT_SECRET_FRAGMENT_SOURCES = frozenset({
    'title',
    'hint',
    'description',
    'waypoints',
})


def _build_workflow_candidates(classification: Dict[str, Any]) -> List[Dict[str, Any]]:
    label_map = {
        item.get('name'): item
        for item in (classification.get('labels') or [])
        if isinstance(item, dict) and item.get('name')
    }
    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    best_secret_fragment = (classification.get('candidate_secret_fragments') or [None])[0] or {}
    best_fragment_confidence = float(signal_summary.get('best_secret_fragment_confidence') or best_secret_fragment.get('confidence') or 0.0)
    best_fragment_source = str(signal_summary.get('best_secret_fragment_source') or best_secret_fragment.get('source') or '').strip()
    hidden_signal_count = int(signal_summary.get('hidden_signal_count') or len(classification.get('hidden_signals') or []))
    hidden_fragment_count = sum(
        1
        for fragment in (classification.get('candidate_secret_fragments') or [])
        if str((fragment or {}).get('source') or '') in {'html_comment', 'hidden_html_text', 'hidden_css_text'}
    )
    image_hint_count = int(signal_summary.get('image_hint_count') or 0)
    image_hint_sources = {
        str(source or '').strip()
        for source in (signal_summary.get('image_hint_sources') or [])
        if str(source or '').strip()
    }
    formula_signal_count = int(signal_summary.get('formula_signal_count') or len(classification.get('formula_signals') or []))
    variable_assignment_count = int(signal_summary.get('variable_assignment_count') or 0)
    has_formula_coordinate_placeholders = bool(signal_summary.get('has_formula_coordinate_placeholders'))
    projection_keyword_count = int(signal_summary.get('projection_keyword_count') or 0)
    visual_image_signal_count = int(signal_summary.get('visual_image_signal_count') or 0)
    has_pi_theme = bool(signal_summary.get('has_pi_theme'))
    pi_position_token_count = int(signal_summary.get('pi_position_token_count') or 0)
    pi_coordinate_axes = [
        str(item or '').strip()
        for item in (signal_summary.get('pi_coordinate_axes') or [])
        if str(item or '').strip()
    ]
    dominant_evidence_domain = str(signal_summary.get('dominant_evidence_domain') or '').strip()
    evidence_domain_gap = float(signal_summary.get('evidence_domain_gap') or 0.0)
    is_hybrid_listing = bool(signal_summary.get('is_hybrid_listing'))
    is_ambiguous_hybrid = bool(signal_summary.get('is_ambiguous_hybrid'))
    ambiguous_domains = [
        str(item or '').strip()
        for item in (signal_summary.get('ambiguous_domains') or [])
        if str(item or '').strip()
    ]
    has_visual_only_image_clue = bool(signal_summary.get('has_visual_only_image_clue'))
    candidates: List[Dict[str, Any]] = []

    def add_candidate(kind: str, label_name: str, base_bonus: float, reason: str) -> None:
        label = label_map.get(label_name)
        if not label:
            return
        confidence = float(label.get('confidence') or 0.0)
        score = confidence + base_bonus
        supporting_labels = [label_name]
        reason_parts = [reason]

        if kind == 'secret_code' and best_fragment_confidence:
            score += 0.08 if best_fragment_confidence >= 0.8 else 0.03
            reason_parts.append(f"Fragment structure fort: {(best_fragment_confidence * 100):.0f}%.")
            if has_pi_theme and pi_position_token_count:
                score += 0.1
                reason_parts.append(
                    "Le theme Pi avec des positions indexees "
                    + ('/'.join(pi_coordinate_axes) if pi_coordinate_axes else 'du listing')
                    + " renforce un decodeur direct de type pi_digits."
                )
            if best_fragment_source in HIDDEN_SECRET_FRAGMENT_SOURCES and label_map.get('hidden_content'):
                score -= 0.08
                if 'hidden_content' not in supporting_labels:
                    supporting_labels.append('hidden_content')
                reason_parts.append("Le meilleur fragment secret provient du HTML cache.")
            if best_fragment_source in IMAGE_SECRET_FRAGMENT_SOURCES and label_map.get('image_puzzle'):
                score -= 0.08
                if 'image_puzzle' not in supporting_labels:
                    supporting_labels.append('image_puzzle')
                reason_parts.append("Le meilleur fragment secret provient d un indice image.")
            if hidden_signal_count >= 2 and label_map.get('hidden_content'):
                score -= 0.03
                reason_parts.append("Des signaux HTML caches concurrencent la piste purement textuelle.")
            if image_hint_count >= 2 and label_map.get('image_puzzle'):
                score -= 0.02
                reason_parts.append("Plusieurs indices image sont presents dans le listing.")
            if (
                has_visual_only_image_clue
                and best_fragment_source in DIRECT_SECRET_FRAGMENT_SOURCES
                and best_fragment_confidence < 0.72
                and label_map.get('image_puzzle')
            ):
                score -= 0.1
                if 'image_puzzle' not in supporting_labels:
                    supporting_labels.append('image_puzzle')
                reason_parts.append("Le listing semble surtout demander une inspection visuelle de l image.")
            if is_hybrid_listing and dominant_evidence_domain == 'direct' and evidence_domain_gap >= 10.0:
                score += 0.06
                reason_parts.append("La piste visible domine dans un listing hybride.")
            elif is_hybrid_listing and dominant_evidence_domain in {'hidden', 'image'} and evidence_domain_gap >= 10.0:
                score -= 0.05
                reason_parts.append("Un autre domaine d indices domine dans ce listing hybride.")
        elif kind == 'formula':
            if variable_assignment_count:
                score += 0.04 if variable_assignment_count == 1 else 0.08
                reason_parts.append(f"{variable_assignment_count} affectation(s) de variable renforcent la piste formule.")
            if has_formula_coordinate_placeholders:
                score += 0.08
                reason_parts.append("Les coordonnees comportent des placeholders de formule.")
            if formula_signal_count >= 2:
                score += 0.04
                reason_parts.append("Plusieurs signaux de formule convergent.")
            if projection_keyword_count >= 2 and not variable_assignment_count and not has_formula_coordinate_placeholders and label_map.get('coord_transform'):
                score -= 0.03
                if 'coord_transform' not in supporting_labels:
                    supporting_labels.append('coord_transform')
                reason_parts.append("La piste peut aussi relever d une projection pure.")
        elif kind == 'hidden_content':
            if classification.get('hidden_signals'):
                score += 0.04
                reason_parts.append("Des signaux HTML caches ont ete trouves.")
            if hidden_fragment_count:
                score += 0.04
                reason_parts.append(f"{hidden_fragment_count} fragment(s) secret(s) proviennent du HTML cache.")
            if best_fragment_source in HIDDEN_SECRET_FRAGMENT_SOURCES and best_fragment_confidence:
                score += 0.08 if best_fragment_confidence >= 0.7 else 0.04
                if label_map.get('secret_code') and 'secret_code' not in supporting_labels:
                    supporting_labels.append('secret_code')
                reason_parts.append("La meilleure piste se situe dans le contenu cache.")
            if is_hybrid_listing and dominant_evidence_domain == 'hidden' and evidence_domain_gap >= 10.0:
                score += 0.06
                reason_parts.append("Le domaine HTML cache domine dans ce listing hybride.")
            elif is_hybrid_listing and dominant_evidence_domain in {'direct', 'image'} and evidence_domain_gap >= 10.0:
                score -= 0.03
                reason_parts.append("Un autre domaine d indices domine dans ce listing hybride.")
        elif kind == 'image_puzzle':
            if signal_summary.get('image_count'):
                score += 0.04
                reason_parts.append("Des images sont presentes dans le listing.")
            if image_hint_count:
                score += 0.03 if image_hint_count == 1 else 0.07
                reason_parts.append(f"{image_hint_count} indice(s) image textuel(s) ont ete extraits.")
            if visual_image_signal_count:
                score += 0.03 if visual_image_signal_count == 1 else 0.06
                reason_parts.append(f"{visual_image_signal_count} indice(s) de lecture visuelle d image ont ete detectes.")
            if image_hint_sources & {'image_alt_text', 'image_title_text', 'image_filename_text'}:
                score += 0.03
                reason_parts.append("Les metadonnees ou noms de fichiers image donnent deja des pistes.")
            if has_visual_only_image_clue:
                score += 0.09
                reason_parts.append("La consigne implique une inspection visuelle de l image meme sans texte extrait.")
            if best_fragment_source in IMAGE_SECRET_FRAGMENT_SOURCES and best_fragment_confidence:
                score += 0.08 if best_fragment_confidence >= 0.7 else 0.04
                if label_map.get('secret_code') and 'secret_code' not in supporting_labels:
                    supporting_labels.append('secret_code')
                reason_parts.append("La meilleure piste se situe dans une image.")
            if is_hybrid_listing and dominant_evidence_domain == 'image' and evidence_domain_gap >= 10.0:
                score += 0.06
                reason_parts.append("Le domaine image domine dans ce listing hybride.")
            elif is_hybrid_listing and dominant_evidence_domain in {'direct', 'hidden'} and evidence_domain_gap >= 10.0:
                score -= 0.03
                reason_parts.append("Un autre domaine d indices domine dans ce listing hybride.")
        elif kind == 'coord_transform':
            if signal_summary.get('waypoint_count'):
                score += 0.05
                reason_parts.append("Des waypoints ou projections sont disponibles.")
            if projection_keyword_count:
                score += 0.03 if projection_keyword_count == 1 else 0.06
                reason_parts.append(f"{projection_keyword_count} indice(s) de projection ou de waypoint ont ete trouves.")
            if variable_assignment_count and label_map.get('formula'):
                score -= 0.07
                if 'formula' not in supporting_labels:
                    supporting_labels.append('formula')
                reason_parts.append("Les affectations de variables orientent plutot vers une formule.")
            if has_formula_coordinate_placeholders and label_map.get('formula'):
                score -= 0.08
                if 'formula' not in supporting_labels:
                    supporting_labels.append('formula')
                reason_parts.append("Les placeholders de coordonnees ressemblent a une formule a resoudre.")
            elif formula_signal_count >= 2 and label_map.get('formula'):
                score -= 0.04
                if 'formula' not in supporting_labels:
                    supporting_labels.append('formula')
                reason_parts.append("Plusieurs signaux formels concurrencent la simple transformation.")

        candidates.append({
            'kind': kind,
            'confidence': round(confidence, 3),
            'score': round(score, 3),
            'reason': ' '.join(dict.fromkeys(part.strip() for part in reason_parts if part.strip())),
            'supporting_labels': supporting_labels,
        })

    add_candidate('formula', 'formula', 0.24, "Le listing contient des signaux de formule ou de coordonnees a variables.")
    add_candidate('secret_code', 'secret_code', 0.08, "Le listing contient un code secret structure ou un fragment compact exploitable.")
    add_candidate('hidden_content', 'hidden_content', 0.05, "Le HTML contient probablement des indices caches.")
    add_candidate('image_puzzle', 'image_puzzle', 0.05, "Le listing semble s appuyer sur des images ou de l OCR.")
    add_candidate('coord_transform', 'coord_transform', 0.02, "Le listing demande probablement une projection ou une transformation de coordonnees.")

    checker_label = label_map.get('checker_available')
    if checker_label:
        candidates.append({
            'kind': 'checker',
            'confidence': round(float(checker_label.get('confidence') or 0.0), 3),
            'score': round(float(checker_label.get('confidence') or 0.0) + 0.01, 3),
            'reason': "Un checker est disponible pour valider une hypothese, pas pour demarrer la resolution.",
            'supporting_labels': ['checker_available'],
        })

    candidates.sort(key=lambda item: (-item['score'], item['kind']))
    return candidates


def _append_candidate_reason(candidate: Dict[str, Any], extra_reason: str) -> Dict[str, Any]:
    if not extra_reason:
        return candidate

    reason_parts: List[str] = []
    for part in (str(candidate.get('reason') or '').strip(), str(extra_reason or '').strip()):
        if part and part not in reason_parts:
            reason_parts.append(part)
    return {**candidate, 'reason': ' '.join(reason_parts).strip()}


def _select_primary_workflow_candidate(
    workflow_candidates: List[Dict[str, Any]],
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    if not workflow_candidates:
        return {
            'kind': 'general',
            'confidence': 0.2,
            'score': 0.2,
            'reason': "Aucun workflow specialise ne ressort nettement du listing.",
            'supporting_labels': [],
        }

    top_candidate = workflow_candidates[0]
    formula_candidate = next((item for item in workflow_candidates if item['kind'] == 'formula'), None)
    image_candidate = next((item for item in workflow_candidates if item['kind'] == 'image_puzzle'), None)
    hidden_candidate = next((item for item in workflow_candidates if item['kind'] == 'hidden_content'), None)
    secret_candidate = next((item for item in workflow_candidates if item['kind'] == 'secret_code'), None)
    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    best_secret_fragment_source = str(signal_summary.get('best_secret_fragment_source') or '').strip()
    best_secret_fragment_confidence = float(signal_summary.get('best_secret_fragment_confidence') or 0.0)
    dominant_evidence_domain = str(signal_summary.get('dominant_evidence_domain') or '').strip()
    evidence_domain_gap = float(signal_summary.get('evidence_domain_gap') or 0.0)
    is_hybrid_listing = bool(signal_summary.get('is_hybrid_listing'))
    is_ambiguous_hybrid = bool(signal_summary.get('is_ambiguous_hybrid'))
    ambiguous_domains = [
        str(item or '').strip()
        for item in (signal_summary.get('ambiguous_domains') or [])
        if str(item or '').strip()
    ]
    has_visual_only_image_clue = bool(signal_summary.get('has_visual_only_image_clue'))

    if formula_candidate and top_candidate['kind'] in {'coord_transform', 'checker'}:
        return _append_candidate_reason(
            formula_candidate,
            "La piste formule est prioritaire sur une simple transformation ou validation.",
        )

    if (
        top_candidate['kind'] == 'secret_code'
        and image_candidate
        and has_visual_only_image_clue
        and best_secret_fragment_source in DIRECT_SECRET_FRAGMENT_SOURCES
        and best_secret_fragment_confidence < 0.72
    ):
        return _append_candidate_reason(
            image_candidate,
            "La consigne impose surtout une lecture visuelle de l image; le fragment visible seul reste trop faible.",
        )

    hybrid_domain_candidate = None
    hybrid_domain_reason = ''
    if is_hybrid_listing and evidence_domain_gap >= 10.0:
        if dominant_evidence_domain == 'direct':
            hybrid_domain_candidate = secret_candidate
            hybrid_domain_reason = "Le domaine visible domine dans ce listing hybride."
        elif dominant_evidence_domain == 'hidden':
            hybrid_domain_candidate = hidden_candidate
            hybrid_domain_reason = "Le domaine HTML cache domine dans ce listing hybride."
        elif dominant_evidence_domain == 'image':
            hybrid_domain_candidate = image_candidate
            hybrid_domain_reason = "Le domaine image domine dans ce listing hybride."

    if hybrid_domain_candidate and hybrid_domain_candidate['kind'] != top_candidate['kind']:
        return _append_candidate_reason(hybrid_domain_candidate, hybrid_domain_reason)

    if (
        is_ambiguous_hybrid
        and top_candidate['kind'] in {'secret_code', 'hidden_content', 'image_puzzle'}
        and ambiguous_domains
    ):
        return _append_candidate_reason(
            top_candidate,
            "Aucun domaine "
            + ' / '.join(ambiguous_domains)
            + " ne domine nettement; verifier plusieurs sources avant de figer le workflow.",
        )

    if top_candidate['kind'] not in {'secret_code', 'hidden_content', 'image_puzzle'}:
        return top_candidate

    source_workflow_kind: Optional[str] = None
    source_reason = ''
    if best_secret_fragment_source in HIDDEN_SECRET_FRAGMENT_SOURCES:
        source_workflow_kind = 'hidden_content'
        source_reason = "Le meilleur fragment secret provient du HTML cache."
    elif best_secret_fragment_source in IMAGE_SECRET_FRAGMENT_SOURCES:
        source_workflow_kind = 'image_puzzle'
        source_reason = "Le meilleur fragment secret provient d un indice image."
    elif best_secret_fragment_source in DIRECT_SECRET_FRAGMENT_SOURCES:
        if has_visual_only_image_clue and image_candidate and best_secret_fragment_confidence < 0.72:
            return _append_candidate_reason(
                image_candidate,
                "La consigne impose surtout une lecture visuelle de l image; le fragment visible seul reste trop faible.",
            )
        source_workflow_kind = 'secret_code'
        source_reason = "Le meilleur fragment secret provient du texte visible ou du hint."

    if not source_workflow_kind or source_workflow_kind == top_candidate['kind']:
        return top_candidate

    source_candidate = next((item for item in workflow_candidates if item['kind'] == source_workflow_kind), None)
    if not source_candidate:
        return top_candidate

    return _append_candidate_reason(source_candidate, source_reason)


def _extract_formula_variables(formulas: List[Dict[str, Any]]) -> List[str]:
    variables: set[str] = set()
    for formula in formulas:
        if not isinstance(formula, dict):
            continue
        formula_text = ' '.join(
            str(formula.get(field) or '')
            for field in ('north', 'east', 'text_output')
        )
        for letter in re.findall(r'[A-Z]', formula_text.upper()):
            if letter not in {'N', 'S', 'E', 'W'}:
                variables.add(letter)
    return sorted(variables)


def _select_primary_secret_fragment(classification: Dict[str, Any], listing_inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fragments = [
        item for item in (classification.get('candidate_secret_fragments') or [])
        if isinstance(item, dict) and str(item.get('text') or '').strip()
    ]
    if not fragments:
        return None

    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    if bool(signal_summary.get('has_pi_theme')) and int(signal_summary.get('pi_position_token_count') or 0) >= 6:
        pi_fragment = next(
            (
                item for item in fragments
                if bool(((item.get('signature') or {}).get('looks_like_pi_index_positions')))
            ),
            None,
        )
        if pi_fragment:
            return pi_fragment

    hint = str(listing_inputs.get('hint') or '').strip()
    if hint:
        exact_hint = next((item for item in fragments if str(item.get('text') or '').strip() == hint), None)
        if exact_hint:
            return exact_hint

    title = str(listing_inputs.get('title') or '').strip()
    if title:
        exact_title = next((item for item in fragments if str(item.get('text') or '').strip() == title), None)
        if exact_title:
            return exact_title

    return fragments[0]


def _summarize_plugin_results(result: Dict[str, Any], *, limit: int = 5) -> Dict[str, Any]:
    raw_results = result.get('results') or []
    top_results: List[Dict[str, Any]] = []
    for item in raw_results[:limit]:
        if not isinstance(item, dict):
            continue
        top_results.append({
            'text_output': item.get('text_output'),
            'coordinates': item.get('coordinates'),
            'confidence': item.get('confidence'),
            'method': item.get('method'),
            'plugin': item.get('plugin') or item.get('source_plugin'),
        })

    coordinates = result.get('coordinates') or result.get('primary_coordinates')

    return {
        'status': result.get('status'),
        'summary': result.get('summary'),
        'results_count': len(raw_results),
        'top_results': top_results,
        'coordinates': coordinates,
        'failed_plugins': (result.get('failed_plugins') or [])[:5],
    }


def _recompute_workflow_next_actions(plan: List[Dict[str, Any]], classification: Dict[str, Any]) -> List[str]:
    next_actions: List[str] = []
    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    is_ambiguous_hybrid = bool(signal_summary.get('is_ambiguous_hybrid'))
    ambiguous_domains = [
        str(item or '').strip()
        for item in (signal_summary.get('ambiguous_domains') or [])
        if str(item or '').strip()
    ]

    if is_ambiguous_hybrid and ambiguous_domains:
        review_action = (
            "Comparer les indices "
            + ' / '.join(ambiguous_domains)
            + " avant de figer le workflow."
        )
        next_actions.append(review_action)

    for step in plan:
        if step.get('status') == 'planned':
            title = str(step.get('title') or '').strip()
            if title and title not in next_actions:
                next_actions.append(title)
    for action in classification.get('recommended_actions') or []:
        action_text = str(action or '').strip()
        if action_text and action_text not in next_actions:
            next_actions.append(action_text)
    return next_actions[:8]


def _mark_plan_step(
    plan: List[Dict[str, Any]],
    step_id: str,
    *,
    status: Optional[str] = None,
    detail: Optional[str] = None,
    automated: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    for step in plan:
        if step.get('id') != step_id:
            continue
        if status is not None:
            step['status'] = status
        if detail is not None:
            step['detail'] = detail
        if automated is not None:
            step['automated'] = automated
        return step
    return None


def _inject_hybrid_review_steps(
    plan: List[Dict[str, Any]],
    classification: Dict[str, Any],
) -> List[Dict[str, Any]]:
    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    if not bool(signal_summary.get('is_ambiguous_hybrid')):
        return plan

    label_names = {
        str(item.get('name') or '').strip()
        for item in (classification.get('labels') or [])
        if isinstance(item, dict) and str(item.get('name') or '').strip()
    }
    existing_ids = {str(step.get('id') or '').strip() for step in plan}
    review_steps: List[Dict[str, Any]] = []

    if 'hidden_content' in label_names and 'inspect-hidden-html' not in existing_ids:
        review_steps.append({
            'id': 'inspect-hidden-html',
            'title': 'Inspecter le HTML cache',
            'status': 'planned',
            'automated': True,
            'tool': 'geoapp.plugins.workflow.run-step',
            'detail': 'Le listing est hybride; verifier aussi les indices caches.',
        })
    if 'image_puzzle' in label_names and 'inspect-images' not in existing_ids:
        review_steps.append({
            'id': 'inspect-images',
            'title': 'Inspecter les images',
            'status': 'planned',
            'automated': True,
            'tool': 'geoapp.plugins.workflow.run-step',
            'detail': 'Le listing est hybride; verifier aussi les indices image.',
        })

    if not review_steps:
        return plan

    insertion_index = 2 if len(plan) >= 2 else len(plan)
    return plan[:insertion_index] + review_steps + plan[insertion_index:]


def _suggest_formula_value_candidates(answer: str, question: str = '') -> List[Dict[str, Any]]:
    normalized = str(answer or '').strip()
    if not normalized:
        return []

    suggestions: List[Dict[str, Any]] = []
    compact = normalized.replace(' ', '')
    digits = re.findall(r'\d', normalized)
    integer_match = re.search(r'-?\d+', normalized)
    question_hint = str(question or '').lower()

    length_confidence = 0.8 if compact and len(compact) < 100 else 0.3
    suggestions.append({
        'type': 'length',
        'confidence': length_confidence,
        'result': len(compact),
        'description': 'Longueur du texte sans espaces',
    })

    checksum = sum(int(digit) for digit in digits)
    checksum_confidence = 0.7 if digits else 0.1
    suggestions.append({
        'type': 'checksum',
        'confidence': checksum_confidence,
        'result': checksum,
        'description': f'Checksum de {len(digits)} chiffre(s)',
    })

    reduced_checksum = checksum
    while reduced_checksum >= 10:
        reduced_checksum = sum(int(digit) for digit in str(reduced_checksum))
    suggestions.append({
        'type': 'reduced_checksum',
        'confidence': checksum_confidence * 0.9,
        'result': reduced_checksum,
        'description': 'Checksum reduit a un chiffre',
    })

    if integer_match:
        direct_value = int(integer_match.group(0))
        value_confidence = 0.95 if normalized == integer_match.group(0) else 0.82
        if any(token in question_hint for token in ('annee', 'year', 'nombre', 'number', 'combien', 'how many')):
            value_confidence = min(0.99, value_confidence + 0.05)
        suggestions.append({
            'type': 'value',
            'confidence': value_confidence,
            'result': direct_value,
            'description': 'Valeur numerique detectee dans la reponse',
        })

    suggestions.sort(key=lambda item: (-float(item.get('confidence') or 0.0), item.get('type') or ''))
    return suggestions


def _calculate_formula_value(answer: Any, value_type: str) -> int:
    normalized = str(answer or '').strip()
    if not normalized:
        raise ValueError("Impossible de calculer une valeur vide")

    normalized_type = str(value_type or '').strip().lower()
    digits = re.findall(r'\d', normalized)

    if normalized_type == 'length':
        return len(normalized.replace(' ', ''))
    if normalized_type == 'checksum':
        return sum(int(digit) for digit in digits)
    if normalized_type == 'reduced_checksum':
        checksum = sum(int(digit) for digit in digits)
        while checksum >= 10:
            checksum = sum(int(digit) for digit in str(checksum))
        return checksum
    if normalized_type == 'value':
        match = re.search(r'-?\d+', normalized)
        if not match:
            raise ValueError(f"Aucune valeur numerique exploitable dans '{normalized[:40]}'")
        return int(match.group(0))
    raise ValueError(f"Type de calcul inconnu: {value_type}")


def _extract_formula_coordinates(formula_entry: Dict[str, Any]) -> tuple[str, str]:
    north = str(
        formula_entry.get('north')
        or formula_entry.get('north_formula')
        or formula_entry.get('northFormula')
        or ''
    ).strip()
    east = str(
        formula_entry.get('east')
        or formula_entry.get('east_formula')
        or formula_entry.get('eastFormula')
        or ''
    ).strip()
    if north and east:
        return north, east

    text_output = str(formula_entry.get('text_output') or '').strip()
    match = re.search(r'([NS][^EW]+)\s+([EW].+)$', text_output, re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return '', ''


def _coerce_decimal_coordinate(value: Any, *, is_latitude: bool) -> Optional[float]:
    try:
        numeric_value = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric_value) or math.isinf(numeric_value):
        return None
    limit = 90.0 if is_latitude else 180.0
    if abs(numeric_value) > limit:
        return None
    return round(numeric_value, 8)


def _extract_decimal_coordinates(candidate: Any) -> Optional[Dict[str, float]]:
    if isinstance(candidate, dict):
        latitude = _coerce_decimal_coordinate(candidate.get('latitude'), is_latitude=True)
        longitude = _coerce_decimal_coordinate(candidate.get('longitude'), is_latitude=False)
        if latitude is not None and longitude is not None:
            return {
                'latitude': latitude,
                'longitude': longitude,
            }
        for key in ('ddm', 'formatted', 'coordinates_raw', 'decimal', 'text_output', 'value'):
            nested_value = candidate.get(key)
            parsed = _extract_decimal_coordinates(nested_value)
            if parsed:
                return parsed
        return None

    if isinstance(candidate, (list, tuple)) and len(candidate) >= 2:
        latitude = _coerce_decimal_coordinate(candidate[0], is_latitude=True)
        longitude = _coerce_decimal_coordinate(candidate[1], is_latitude=False)
        if latitude is not None and longitude is not None:
            return {
                'latitude': latitude,
                'longitude': longitude,
            }
        return None

    text = str(candidate or '').strip()
    if not text:
        return None

    decimal_match = DECIMAL_COORDINATE_PAIR_PATTERN.search(text)
    if decimal_match:
        latitude = _coerce_decimal_coordinate(decimal_match.group(1), is_latitude=True)
        longitude = _coerce_decimal_coordinate(decimal_match.group(2), is_latitude=False)
        if latitude is not None and longitude is not None:
            return {
                'latitude': latitude,
                'longitude': longitude,
            }

    ddm_match = DDM_COORDINATE_PAIR_PATTERN.search(' '.join(text.split()))
    if ddm_match:
        try:
            from gc_backend.blueprints.coordinates import convert_ddm_to_decimal

            converted = convert_ddm_to_decimal(ddm_match.group(1), ddm_match.group(2))
        except Exception:
            converted = None
        if isinstance(converted, dict):
            latitude = _coerce_decimal_coordinate(converted.get('latitude'), is_latitude=True)
            longitude = _coerce_decimal_coordinate(converted.get('longitude'), is_latitude=False)
            if latitude is not None and longitude is not None:
                return {
                    'latitude': latitude,
                    'longitude': longitude,
                }

    return None


def _collect_geographic_reference_points(listing_inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    references: List[Dict[str, Any]] = []
    seen_keys: set[Tuple[str, float, float]] = set()

    def add_reference(reference_type: str, label: str, coordinates: Any) -> None:
        parsed = _extract_decimal_coordinates(coordinates)
        if not parsed:
            return
        latitude = parsed['latitude']
        longitude = parsed['longitude']
        dedupe_key = (reference_type, round(latitude, 5), round(longitude, 5))
        if dedupe_key in seen_keys:
            return
        seen_keys.add(dedupe_key)
        references.append({
            'type': reference_type,
            'label': label,
            'latitude': latitude,
            'longitude': longitude,
        })

    geocache_record = listing_inputs.get('geocache_record')
    if geocache_record is not None:
        add_reference(
            'published',
            'Coordonnees publiees',
            {
                'latitude': getattr(geocache_record, 'latitude', None),
                'longitude': getattr(geocache_record, 'longitude', None),
            },
        )
        add_reference(
            'original',
            'Coordonnees originales',
            {
                'latitude': getattr(geocache_record, 'original_latitude', None),
                'longitude': getattr(geocache_record, 'original_longitude', None),
            },
        )
        for waypoint in getattr(geocache_record, 'waypoints', []) or []:
            add_reference(
                'waypoint',
                f"Waypoint {getattr(waypoint, 'name', None) or getattr(waypoint, 'prefix', None) or 'sans nom'}",
                {
                    'latitude': getattr(waypoint, 'latitude', None),
                    'longitude': getattr(waypoint, 'longitude', None),
                    'coordinates_raw': getattr(waypoint, 'gc_coords', None),
                },
            )

    for waypoint in listing_inputs.get('waypoints') or []:
        if not isinstance(waypoint, dict):
            continue
        add_reference(
            'waypoint',
            f"Waypoint {str(waypoint.get('name') or waypoint.get('prefix') or waypoint.get('lookup') or 'sans nom').strip()}",
            waypoint,
        )

    return references


def _build_geographic_plausibility(candidate_coordinates: Any, listing_inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    parsed_candidate = _extract_decimal_coordinates(candidate_coordinates)
    if not parsed_candidate:
        return None

    references = _collect_geographic_reference_points(listing_inputs)
    if not references:
        return {
            'status': 'unknown',
            'score': 0.0,
            'summary': 'Aucun point de reference geographique disponible.',
            'reasons': ['La geocache ne fournit pas de coordonnees d origine ou de waypoint exploitable.'],
            'reference_count': 0,
            'published_distance_km': None,
            'original_distance_km': None,
            'nearest_waypoint_distance_km': None,
            'nearest_reference': None,
            'reference_distances': [],
        }

    from gc_backend.utils.coordinate_calculator import CoordinateCalculator

    calculator = CoordinateCalculator()
    measured_distances: List[Dict[str, Any]] = []
    published_distance_km: Optional[float] = None
    original_distance_km: Optional[float] = None
    nearest_waypoint_distance_km: Optional[float] = None

    for reference in references:
        distance_km = calculator.calculate_distance(
            parsed_candidate['latitude'],
            parsed_candidate['longitude'],
            reference['latitude'],
            reference['longitude'],
        )
        distance_km = round(distance_km, 2)
        measured = {
            **reference,
            'distance_km': distance_km,
        }
        measured_distances.append(measured)
        if reference['type'] == 'published':
            published_distance_km = distance_km
        elif reference['type'] == 'original':
            original_distance_km = distance_km
        elif reference['type'] == 'waypoint':
            nearest_waypoint_distance_km = (
                distance_km
                if nearest_waypoint_distance_km is None
                else min(nearest_waypoint_distance_km, distance_km)
            )

    measured_distances.sort(key=lambda item: (item['distance_km'], item['type'], item['label']))
    nearest_reference = measured_distances[0]
    reference_anchor_distance = nearest_reference['distance_km']

    if reference_anchor_distance <= 0.35:
        status = 'very_plausible'
        score = 0.97
    elif reference_anchor_distance <= 3:
        status = 'plausible'
        score = 0.88
    elif reference_anchor_distance <= 25:
        status = 'plausible'
        score = 0.76
    elif reference_anchor_distance <= 80:
        status = 'uncertain'
        score = 0.52
    elif reference_anchor_distance <= 200:
        status = 'unlikely'
        score = 0.28
    else:
        status = 'unlikely'
        score = 0.12

    if nearest_reference['type'] == 'waypoint' and reference_anchor_distance <= 1:
        score = max(score, 0.93)
        status = 'very_plausible'

    reasons = [
        f"A {reference_anchor_distance:.2f} km de {nearest_reference['label'].lower()}."
    ]
    if published_distance_km is not None and nearest_reference['type'] != 'published':
        reasons.append(f"A {published_distance_km:.2f} km des coordonnees publiees.")
    if original_distance_km is not None and nearest_reference['type'] != 'original':
        reasons.append(f"A {original_distance_km:.2f} km des coordonnees originales.")
    if nearest_waypoint_distance_km is not None:
        reasons.append(f"Waypoint le plus proche a {nearest_waypoint_distance_km:.2f} km.")

    summary = {
        'very_plausible': 'Les coordonnees restent tres proches d un point de reference du listing.',
        'plausible': 'Les coordonnees restent dans une zone geographiquement plausible pour la geocache.',
        'uncertain': 'Les coordonnees sont assez eloignees; verification humaine recommandee.',
        'unlikely': 'Les coordonnees semblent trop eloignees des references connues.',
        'unknown': 'Plausibilite geographique non evaluable.',
    }[status]

    return {
        'status': status,
        'score': round(score, 3),
        'summary': summary,
        'reasons': reasons[:3],
        'reference_count': len(measured_distances),
        'published_distance_km': published_distance_km,
        'original_distance_km': original_distance_km,
        'nearest_waypoint_distance_km': nearest_waypoint_distance_km,
        'nearest_reference': {
            'type': nearest_reference['type'],
            'label': nearest_reference['label'],
            'distance_km': nearest_reference['distance_km'],
        },
        'reference_distances': [
            {
                'type': item['type'],
                'label': item['label'],
                'distance_km': item['distance_km'],
            }
            for item in measured_distances[:4]
        ],
    }


def _extract_primary_metasolver_coordinates_candidate(metasolver_result: Dict[str, Any]) -> Any:
    candidate = metasolver_result.get('coordinates')
    if candidate:
        return candidate

    for item in metasolver_result.get('top_results') or []:
        if not isinstance(item, dict):
            continue
        if item.get('coordinates'):
            return item.get('coordinates')
        text_output = str(item.get('text_output') or '').strip()
        if _extract_decimal_coordinates(text_output):
            return text_output

    return None


def _attach_metasolver_geographic_plausibility(
    metasolver_result: Optional[Dict[str, Any]],
    listing_inputs: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(metasolver_result, dict):
        return metasolver_result

    candidate_coordinates = _extract_primary_metasolver_coordinates_candidate(metasolver_result)
    plausibility = _build_geographic_plausibility(candidate_coordinates, listing_inputs)
    if plausibility:
        metasolver_result['geographic_plausibility'] = plausibility
    return metasolver_result


def _format_checker_candidate_from_coordinates(coordinates: Any) -> str:
    if isinstance(coordinates, dict):
        for key in ('ddm', 'formatted', 'decimal', 'coordinates_raw'):
            value = str(coordinates.get(key) or '').strip()
            if value:
                return value
        latitude = coordinates.get('latitude')
        longitude = coordinates.get('longitude')
        if latitude is not None and longitude is not None:
            return f"{latitude}, {longitude}"
    if isinstance(coordinates, str):
        return coordinates.strip()
    return ''


def _resolve_checker_candidate(data: Dict[str, Any], workflow_resolution: Dict[str, Any]) -> str:
    explicit = str(
        data.get('checker_candidate')
        or data.get('candidate')
        or ''
    ).strip()
    if explicit:
        return explicit

    execution = workflow_resolution.get('execution') or {}
    formula_payload = execution.get('formula') or {}
    calculated = (formula_payload.get('calculated_coordinates') or {}).get('coordinates')
    candidate = _format_checker_candidate_from_coordinates(calculated)
    if candidate:
        return candidate

    secret_payload = execution.get('secret_code') or {}
    direct_plugin_result = secret_payload.get('direct_plugin_result') or {}
    candidate = _format_checker_candidate_from_coordinates(direct_plugin_result.get('coordinates'))
    if candidate:
        return candidate

    for item in direct_plugin_result.get('top_results') or []:
        if not isinstance(item, dict):
            continue
        candidate = _format_checker_candidate_from_coordinates(item.get('coordinates'))
        if candidate:
            return candidate
        text_output = str(item.get('text_output') or '').strip()
        if text_output:
            return text_output

    metasolver_result = secret_payload.get('metasolver_result') or {}
    candidate = _format_checker_candidate_from_coordinates(metasolver_result.get('coordinates'))
    if candidate:
        return candidate

    for item in metasolver_result.get('top_results') or []:
        if not isinstance(item, dict):
            continue
        candidate = _format_checker_candidate_from_coordinates(item.get('coordinates'))
        if candidate:
            return candidate
        text_output = str(item.get('text_output') or '').strip()
        if text_output:
            return text_output

    selected_fragment = secret_payload.get('selected_fragment') or {}
    fragment_text = str(selected_fragment.get('text') or '').strip()
    if fragment_text:
        return fragment_text

    return ''


def _is_certitudes_url(url: str) -> bool:
    raw = (url or '').lower()
    return 'certitudes.org' in raw or 'www.certitudes.org' in raw


def _is_geocaching_url(url: str) -> bool:
    raw = (url or '').lower()
    if 'geocaching.com' not in raw:
        return False
    return '/geocache/' in raw or 'cache_details.aspx' in raw


def _normalize_certitudes_url(url: str, wp: Optional[str]) -> str:
    raw = str(url or '').strip()
    if not raw:
        raise ValueError('Missing checker url')

    if '://' not in raw:
        raw = f"https://{raw.lstrip('/')}"

    parsed = urlparse(raw)
    host = (parsed.hostname or '').lower()
    if not host.endswith('certitudes.org'):
        return raw

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if wp and not query.get('wp'):
        query['wp'] = wp

    path = parsed.path or ''
    if 'certitude' not in path.lower():
        path = '/certitude'

    normalized = parsed._replace(
        scheme='https',
        netloc='www.certitudes.org',
        path=path,
        query=urlencode(query),
    )
    return urlunparse(normalized)


def _normalize_geocaching_url(url: str, wp: Optional[str]) -> str:
    raw = str(url or '').strip()
    if not raw:
        raise ValueError('Missing checker url')

    lowered = raw.lower()
    if raw.startswith('#') or raw in {'solution-checker', '#solution-checker'}:
        if not wp:
            raise ValueError('Invalid Geocaching checker url (#solution-checker) without GC code')
        return f'https://www.geocaching.com/geocache/{wp}'

    if '/geocache/#solution-checker' in lowered or '/geocache/#' in lowered:
        if not wp:
            raise ValueError('Geocaching checker url is missing the GC code')
        return f'https://www.geocaching.com/geocache/{wp}'

    if raw.startswith('/'):
        return f'https://www.geocaching.com{raw}'

    if '://' not in raw and 'geocaching.com' in lowered:
        return f"https://{raw.replace('http://', '').replace('https://', '')}"

    return raw


def _resolve_checker_target(listing_inputs: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    metadata = listing_inputs.get('metadata') or {}
    wp = str(data.get('wp') or metadata.get('gc_code') or '').strip() or None

    explicit_url = str(data.get('checker_url') or data.get('url') or '').strip()
    if explicit_url:
        target = {
            'url': explicit_url,
            'name': str(data.get('checker_name') or 'Checker').strip() or 'Checker',
            'wp': wp,
        }
    else:
        requested_checker_id = data.get('checker_id')
        checkers = listing_inputs.get('checkers') or []
        chosen = None
        if requested_checker_id is not None:
            chosen = next(
                (checker for checker in checkers if str(checker.get('id')) == str(requested_checker_id)),
                None,
            )
        if chosen is None:
            def _pick(*predicates):
                for predicate in predicates:
                    found = next((checker for checker in checkers if predicate(checker)), None)
                    if found is not None:
                        return found
                return None

            chosen = _pick(
                lambda checker: 'certitudes.org' in str(checker.get('url') or '').lower(),
                lambda checker: 'certitude' in str(checker.get('name') or '').lower(),
                lambda checker: 'geocaching' in str(checker.get('name') or '').lower(),
                lambda checker: 'geocaching.com' in str(checker.get('url') or '').lower(),
                lambda checker: True,
            )

        if not chosen:
            raise ValueError('No checkers available for this geocache or listing')

        target = {
            'url': str(chosen.get('url') or '').strip(),
            'name': str(chosen.get('name') or 'Checker').strip() or 'Checker',
            'wp': wp,
        }

    if not target['url']:
        raise ValueError('Checker URL is missing for this geocache or listing')

    if _is_geocaching_url(target['url']) or 'geocaching.com' in target['url'].lower() or target['url'].startswith('#'):
        target['url'] = _normalize_geocaching_url(target['url'], target['wp'])
    if _is_certitudes_url(target['url']) or 'certitudes.org' in target['url'].lower():
        target['url'] = _normalize_certitudes_url(target['url'], target['wp'])

    target['interactive'] = _is_certitudes_url(target['url']) or _is_geocaching_url(target['url'])
    target['provider'] = 'geocaching' if _is_geocaching_url(target['url']) else ('certitudes' if _is_certitudes_url(target['url']) else 'generic')
    return target


def _run_checker_with_target(
    *,
    url: str,
    candidate: str,
    wp: Optional[str],
    interactive: bool,
    provider: str,
    auto_login: bool,
    login_timeout_sec: int,
    timeout_sec: int,
) -> Dict[str, Any]:
    from gc_backend.blueprints.checkers import (
        _build_runner,
        _is_checkers_enabled,
        _run_playwright_blocking,
        _should_keep_checker_page_open,
    )

    if not _is_checkers_enabled():
        raise RuntimeError('checkers_disabled')

    runner = _build_runner()

    if provider == 'geocaching':
        from gc_backend.services.checkers.session import GeocachingSessionManager
        from gc_backend.services.checkers.storage import get_default_profile_dir
        from gc_backend.utils.preferences import get_value_or_default

        profile_dir_raw = get_value_or_default('geoApp.checkers.profileDir', '')
        profile_dir = Path(profile_dir_raw) if profile_dir_raw else get_default_profile_dir()
        timeout_ms = int(get_value_or_default('geoApp.checkers.timeoutMs', 20000))
        headless = bool(get_value_or_default('geoApp.checkers.playwright.headless', True))

        manager = GeocachingSessionManager(profile_dir=profile_dir, timeout_ms=timeout_ms)
        logged_in = bool(_run_playwright_blocking(lambda: manager.is_logged_in(headless=headless)))
        if not logged_in and auto_login:
            logged_in = bool(_run_playwright_blocking(lambda: manager.login_interactive(timeout_sec=login_timeout_sec)))
        if not logged_in:
            return {
                'status': 'requires_login',
                'message': 'Geocaching.com session is not logged in. Use login_checker_session or retry with manual login.',
                'provider': provider,
                'url': url,
                'wp': wp,
            }

    input_payload = {'candidate': candidate}
    if interactive:
        result = _run_playwright_blocking(
            lambda: runner.run_interactive(
                url=url,
                input_payload=input_payload,
                timeout_sec=timeout_sec,
                keep_open=_should_keep_checker_page_open(url),
            )
        )
    else:
        result = _run_playwright_blocking(lambda: runner.run(url=url, input_payload=input_payload))

    return {
        'status': 'success',
        'provider': provider,
        'url': url,
        'wp': wp,
        'interactive': interactive,
        'candidate': candidate,
        'result': result,
    }


def _derive_formula_values(data: Dict[str, Any]) -> Dict[str, int]:
    values: Dict[str, int] = {}

    raw_values = data.get('formula_values')
    if isinstance(raw_values, dict):
        for key, value in raw_values.items():
            variable = str(key or '').strip().upper()
            if not variable:
                continue
            try:
                values[variable] = int(value)
            except (TypeError, ValueError):
                continue

    raw_answers = data.get('formula_answers')
    raw_types = data.get('formula_value_types')
    if isinstance(raw_answers, dict):
        for key, answer in raw_answers.items():
            variable = str(key or '').strip().upper()
            if not variable or variable in values:
                continue
            value_type = ''
            if isinstance(raw_types, dict):
                value_type = str(raw_types.get(key) or raw_types.get(variable) or '').strip().lower()
            if not value_type:
                suggestions = _suggest_formula_value_candidates(str(answer or ''))
                value_type = str((suggestions[0] if suggestions else {}).get('type') or 'length')
            try:
                values[variable] = _calculate_formula_value(answer, value_type)
            except ValueError:
                continue

    return values


def _extract_previous_workflow_control(data: Dict[str, Any]) -> Dict[str, Any]:
    raw_control = data.get('workflow_control')
    return raw_control if isinstance(raw_control, dict) else {}


def _build_workflow_budget(data: Dict[str, Any], workflow_kind: str, previous_control: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = dict(WORKFLOW_BUDGET_DEFAULTS.get(workflow_kind) or WORKFLOW_BUDGET_DEFAULTS['general'])
    raw_budget = data.get('workflow_budget')
    if not isinstance(raw_budget, dict):
        raw_budget = (previous_control or {}).get('budget')
    if not isinstance(raw_budget, dict):
        raw_budget = {}

    return {
        'max_automated_steps': _normalize_positive_int(raw_budget.get('max_automated_steps'), defaults['max_automated_steps'], minimum=0, maximum=20),
        'max_metasolver_runs': _normalize_positive_int(raw_budget.get('max_metasolver_runs'), defaults['max_metasolver_runs'], minimum=0, maximum=10),
        'max_search_questions': _normalize_positive_int(raw_budget.get('max_search_questions'), defaults['max_search_questions'], minimum=0, maximum=50),
        'max_checker_runs': _normalize_positive_int(raw_budget.get('max_checker_runs'), defaults['max_checker_runs'], minimum=0, maximum=10),
        'max_coordinate_calculations': _normalize_positive_int(raw_budget.get('max_coordinate_calculations'), defaults['max_coordinate_calculations'], minimum=0, maximum=10),
        'max_vision_ocr_runs': _normalize_positive_int(raw_budget.get('max_vision_ocr_runs'), defaults['max_vision_ocr_runs'], minimum=0, maximum=10),
        'stop_on_checker_success': _normalize_bool(raw_budget.get('stop_on_checker_success'), bool(defaults['stop_on_checker_success'])),
    }


def _build_workflow_usage(
    execution: Dict[str, Any],
    previous_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    usage = {
        'automated_steps': 0,
        'metasolver_runs': 0,
        'search_questions': 0,
        'checker_runs': 0,
        'coordinate_calculations': 0,
        'vision_ocr_runs': 0,
    }
    raw_previous_usage = (previous_control or {}).get('usage')
    if isinstance(raw_previous_usage, dict):
        for key in usage:
            usage[key] = _normalize_positive_int(raw_previous_usage.get(key), usage[key], minimum=0, maximum=1000)

    secret_payload = execution.get('secret_code') or {}
    if (secret_payload.get('direct_plugin_result') or {}).get('status'):
        usage['automated_steps'] = max(usage['automated_steps'], 1)
    if (secret_payload.get('metasolver_result') or {}).get('status'):
        usage['metasolver_runs'] = max(usage['metasolver_runs'], 1)

    formula_payload = execution.get('formula') or {}
    answer_search = formula_payload.get('answer_search') or {}
    if isinstance(answer_search.get('answers'), dict):
        usage['search_questions'] = max(usage['search_questions'], len(answer_search.get('answers') or {}))
    if (formula_payload.get('calculated_coordinates') or {}).get('status'):
        usage['coordinate_calculations'] = max(usage['coordinate_calculations'], 1)

    checker_payload = execution.get('checker') or {}
    if checker_payload.get('status') or checker_payload.get('result'):
        usage['checker_runs'] = max(usage['checker_runs'], 1)

    hidden_payload = execution.get('hidden_content') or {}
    if hidden_payload.get('inspected'):
        usage['automated_steps'] = max(usage['automated_steps'], 1)
    image_payload = execution.get('image_puzzle') or {}
    if image_payload.get('inspected'):
        usage['automated_steps'] = max(usage['automated_steps'], 1)
    current_vision_usage = _normalize_positive_int(
        image_payload.get('vision_ocr_budget_cost'),
        0,
        minimum=0,
        maximum=1000,
    )
    if current_vision_usage <= 0:
        current_vision_usage = _normalize_positive_int(
            image_payload.get('vision_ocr_images_analyzed'),
            0,
            minimum=0,
            maximum=1000,
        )
    if current_vision_usage <= 0:
        current_vision_usage = sum(
            1
            for item in (image_payload.get('items') or [])
            if str(item.get('source') or '') == 'image_vision_text'
        )
    if current_vision_usage > 0:
        usage['vision_ocr_runs'] += current_vision_usage

    inferred_automated_steps = 0
    inferred_automated_steps += 1 if (secret_payload.get('direct_plugin_result') or {}).get('status') else 0
    inferred_automated_steps += usage['metasolver_runs']
    inferred_automated_steps += 1 if hidden_payload.get('inspected') else 0
    inferred_automated_steps += 1 if image_payload.get('inspected') else 0
    inferred_automated_steps += 1 if usage['search_questions'] > 0 else 0
    inferred_automated_steps += usage['coordinate_calculations']
    inferred_automated_steps += usage['checker_runs']
    usage['automated_steps'] = max(usage['automated_steps'], inferred_automated_steps)
    return usage


def _checker_execution_succeeded(checker_payload: Any) -> bool:
    if not isinstance(checker_payload, dict):
        return False
    result = checker_payload.get('result') or {}
    statuses = {
        str(checker_payload.get('status') or '').strip().lower(),
        str(result.get('status') or '').strip().lower(),
    }
    if {'success', 'ok', 'valid'} & statuses:
        return True
    message = ' '.join(
        str(value or '').strip().lower()
        for value in (checker_payload.get('message'), result.get('message'), result.get('evidence'))
        if value
    )
    return any(token in message for token in ('felicitation', 'congrat', 'correct', 'valid'))


def _get_step_budget_block_reason(step_id: str, remaining: Dict[str, Any]) -> Optional[str]:
    if step_id in SUPPORTED_AUTOMATED_WORKFLOW_STEPS and int(remaining.get('automated_steps') or 0) <= 0:
        return 'Le budget global d automatisation est epuise.'
    if step_id == 'execute-metasolver' and int(remaining.get('metasolver_runs') or 0) <= 0:
        return 'Le budget metasolver est epuise.'
    if step_id == 'search-answers' and int(remaining.get('search_questions') or 0) <= 0:
        return 'Le budget de recherche web est epuise.'
    if step_id == 'calculate-final-coordinates' and int(remaining.get('coordinate_calculations') or 0) <= 0:
        return 'Le budget de calcul de coordonnees est epuise.'
    if step_id == 'validate-with-checker' and int(remaining.get('checker_runs') or 0) <= 0:
        return 'Le budget checker est epuise.'
    return None


def _estimate_workflow_final_confidence(
    workflow_kind: str,
    execution: Dict[str, Any],
    classification: Dict[str, Any],
) -> float:
    checker_payload = execution.get('checker') or {}
    if _checker_execution_succeeded(checker_payload):
        return 0.99

    confidence = 0.15
    if workflow_kind == 'secret_code':
        secret_payload = execution.get('secret_code') or {}
        if secret_payload.get('selected_fragment'):
            confidence = max(confidence, 0.34)
        direct_plugin_candidate = secret_payload.get('direct_plugin_candidate') or {}
        if direct_plugin_candidate.get('plugin_name'):
            confidence = max(confidence, 0.56)
        direct_plugin_result = secret_payload.get('direct_plugin_result') or {}
        if direct_plugin_result.get('results_count'):
            confidence = max(confidence, 0.72)
        if direct_plugin_result.get('coordinates'):
            confidence = max(confidence, 0.8)
        if secret_payload.get('recommendation'):
            confidence = max(confidence, 0.48)
        metasolver_result = secret_payload.get('metasolver_result') or {}
        if metasolver_result.get('results_count'):
            confidence = max(confidence, 0.62)
        if metasolver_result.get('coordinates'):
            confidence = max(confidence, 0.78)
        plausibility_score = float(((metasolver_result.get('geographic_plausibility') or {}).get('score')) or 0.0)
        if plausibility_score >= 0.9:
            confidence = max(confidence, 0.88)
        elif plausibility_score >= 0.75:
            confidence = max(confidence, 0.82)
        elif plausibility_score >= 0.45:
            confidence = max(confidence, 0.7)
        elif plausibility_score > 0:
            confidence = min(confidence, 0.58)
    elif workflow_kind == 'formula':
        formula_payload = execution.get('formula') or {}
        formula_count = int(formula_payload.get('formula_count') or 0)
        if formula_count:
            confidence = max(confidence, 0.46)
        found_question_count = int(formula_payload.get('found_question_count') or 0)
        if found_question_count:
            confidence = max(confidence, min(0.66, 0.46 + found_question_count * 0.04))
        answer_search = formula_payload.get('answer_search') or {}
        found_answers = int(answer_search.get('found_count') or 0)
        if found_answers:
            confidence = max(confidence, min(0.74, 0.56 + found_answers * 0.03))
        calculated = formula_payload.get('calculated_coordinates') or {}
        if calculated.get('status') == 'success':
            confidence = max(confidence, 0.82)
            distance_km = ((calculated.get('distance') or {}).get('km'))
            try:
                if distance_km is not None:
                    distance_km = float(distance_km)
                    if distance_km <= 25:
                        confidence = min(0.9, confidence + 0.05)
                    elif distance_km >= 200:
                        confidence = max(0.55, confidence - 0.12)
            except (TypeError, ValueError):
                pass
            plausibility_score = float(((calculated.get('geographic_plausibility') or {}).get('score')) or 0.0)
            if plausibility_score >= 0.9:
                confidence = max(confidence, 0.93)
            elif plausibility_score >= 0.75:
                confidence = max(confidence, 0.88)
            elif plausibility_score >= 0.45:
                confidence = max(confidence, 0.76)
            elif plausibility_score > 0:
                confidence = min(confidence, 0.63)
    elif workflow_kind == 'hidden_content':
        hidden_payload = execution.get('hidden_content') or {}
        if hidden_payload.get('hidden_signals'):
            confidence = max(confidence, 0.36)
        if hidden_payload.get('items'):
            confidence = max(confidence, 0.48)
        if hidden_payload.get('selected_fragment'):
            confidence = max(confidence, 0.62)
        if hidden_payload.get('recommendation'):
            confidence = max(confidence, 0.71)
    elif workflow_kind == 'image_puzzle':
        image_payload = execution.get('image_puzzle') or {}
        if int(image_payload.get('image_count') or 0) > 0:
            confidence = max(confidence, 0.3)
        if image_payload.get('items'):
            confidence = max(confidence, 0.44)
        if image_payload.get('selected_fragment'):
            confidence = max(confidence, 0.6)
        if image_payload.get('recommendation'):
            confidence = max(confidence, 0.69)
        plausibility_score = float(((image_payload.get('geographic_plausibility') or {}).get('score')) or 0.0)
        if plausibility_score >= 0.9:
            confidence = max(confidence, 0.88)
        elif plausibility_score >= 0.75:
            confidence = max(confidence, 0.81)
        elif plausibility_score >= 0.45:
            confidence = max(confidence, 0.67)
        elif plausibility_score > 0:
            confidence = min(confidence, 0.54)
    else:
        labels = classification.get('labels') or []
        if labels:
            confidence = max(confidence, float((labels[0] or {}).get('confidence') or 0.2))

    signal_summary = classification.get('signal_summary') if isinstance(classification.get('signal_summary'), dict) else {}
    if bool(signal_summary.get('is_ambiguous_hybrid')) and workflow_kind in {'secret_code', 'hidden_content', 'image_puzzle'}:
        ambiguous_domains = [
            str(item or '').strip()
            for item in (signal_summary.get('ambiguous_domains') or [])
            if str(item or '').strip()
        ]
        if len(ambiguous_domains) >= 2:
            confidence = max(0.05, confidence - 0.1)

    return round(min(0.99, max(0.05, confidence)), 3)


def _get_step_control_block_reason(step_id: str, control: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(control, dict):
        return None

    remaining = control.get('remaining') if isinstance(control.get('remaining'), dict) else {}
    status = str(control.get('status') or '').strip().lower()
    stop_reasons = [str(item or '').strip() for item in (control.get('stop_reasons') or []) if str(item or '').strip()]

    budget_reason = _get_step_budget_block_reason(step_id, remaining)
    if budget_reason:
        return budget_reason
    if status in {'stopped', 'budget_exhausted'} and stop_reasons:
        return stop_reasons[0]
    return None


def _apply_workflow_control_to_plan(plan: List[Dict[str, Any]], control: Dict[str, Any]) -> None:
    for step in plan:
        if step.get('status') != 'planned':
            continue
        step_id = str(step.get('id') or '')
        if step_id not in SUPPORTED_AUTOMATED_WORKFLOW_STEPS:
            continue
        reason = _get_step_control_block_reason(step_id, control)
        if reason:
            step['status'] = 'skipped'
            step['detail'] = reason


def _build_workflow_control(
    *,
    data: Dict[str, Any],
    workflow_kind: str,
    plan: List[Dict[str, Any]],
    classification: Dict[str, Any],
    execution: Dict[str, Any],
    previous_control: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    budget = _build_workflow_budget(data, workflow_kind, previous_control=previous_control)
    usage = _build_workflow_usage(execution, previous_control=previous_control)
    remaining = {
        'automated_steps': max(0, int(budget['max_automated_steps']) - int(usage['automated_steps'])),
        'metasolver_runs': max(0, int(budget['max_metasolver_runs']) - int(usage['metasolver_runs'])),
        'search_questions': max(0, int(budget['max_search_questions']) - int(usage['search_questions'])),
        'checker_runs': max(0, int(budget['max_checker_runs']) - int(usage['checker_runs'])),
        'coordinate_calculations': max(0, int(budget['max_coordinate_calculations']) - int(usage['coordinate_calculations'])),
        'vision_ocr_runs': max(0, int(budget['max_vision_ocr_runs']) - int(usage['vision_ocr_runs'])),
    }
    final_confidence = _estimate_workflow_final_confidence(workflow_kind, execution, classification)

    stop_reasons: List[str] = []
    if budget.get('stop_on_checker_success') and _checker_execution_succeeded(execution.get('checker')):
        stop_reasons.append('Le checker a valide l hypothese courante.')

    planned_supported_steps = [
        str(step.get('id') or '')
        for step in plan
        if step.get('status') == 'planned' and str(step.get('id') or '') in SUPPORTED_AUTOMATED_WORKFLOW_STEPS
    ]

    step_budget_reasons = {
        step_id: _get_step_budget_block_reason(step_id, remaining)
        for step_id in planned_supported_steps
    }

    requires_user_input = False
    if workflow_kind == 'formula':
        formula_payload = execution.get('formula') or {}
        formula_values = _derive_formula_values(data)
        if formula_payload.get('answer_search') and not formula_values and 'calculate-final-coordinates' in planned_supported_steps:
            requires_user_input = True
        elif 'search-answers' not in planned_supported_steps and 'calculate-final-coordinates' in planned_supported_steps and not formula_values:
            requires_user_input = True

    executable_steps = [
        step_id for step_id in planned_supported_steps
        if not step_budget_reasons.get(step_id)
    ]
    if not stop_reasons and planned_supported_steps and not executable_steps:
        unique_budget_reasons = [
            reason for reason in dict.fromkeys(step_budget_reasons.values()).keys()
            if reason
        ]
        stop_reasons.extend(unique_budget_reasons[:2] or ['Le budget d automatisation est epuise.'])

    can_run_next_step = bool(executable_steps) and not stop_reasons

    if stop_reasons and any('budget' in reason.lower() or 'epuise' in reason.lower() for reason in stop_reasons):
        status = 'budget_exhausted'
    elif stop_reasons:
        status = 'stopped'
    elif can_run_next_step:
        status = 'ready'
    elif requires_user_input:
        status = 'awaiting_input'
    else:
        status = 'completed'

    summary = {
        'ready': 'Des etapes automatisees restent executables.',
        'awaiting_input': 'L automatisation attend des valeurs ou une intervention utilisateur.',
        'budget_exhausted': 'Le budget d automatisation est epuise.',
        'stopped': 'Le workflow a atteint une condition d arret explicite.',
        'completed': 'Aucune etape automatisee restante.',
    }[status]

    return {
        'status': status,
        'budget': budget,
        'usage': usage,
        'remaining': remaining,
        'stop_reasons': list(dict.fromkeys(stop_reasons))[:6],
        'can_run_next_step': can_run_next_step,
        'requires_user_input': requires_user_input,
        'final_confidence': final_confidence,
        'summary': summary,
    }


def _build_resolution_plan(
    *,
    workflow_kind: str,
    classification: Dict[str, Any],
    secret_payload: Optional[Dict[str, Any]],
    formula_payload: Optional[Dict[str, Any]],
    auto_execute: bool,
) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = [
        {
            'id': 'classify-listing',
            'title': 'Classifier le listing',
            'status': 'completed',
            'automated': True,
            'tool': 'geoapp.plugins.listing.classify',
            'detail': ', '.join(item.get('name') for item in (classification.get('labels') or []) if item.get('name')) or 'Aucun label fort',
        },
        {
            'id': 'choose-workflow',
            'title': f'Selectionner le workflow principal: {workflow_kind}',
            'status': 'completed',
            'automated': True,
            'tool': 'geoapp.plugins.workflow.resolve',
        },
    ]

    if workflow_kind == 'secret_code':
        selected_fragment = (secret_payload or {}).get('selected_fragment')
        direct_plugin_candidate = (secret_payload or {}).get('direct_plugin_candidate')
        direct_plugin_result = (secret_payload or {}).get('direct_plugin_result')
        recommendation = (secret_payload or {}).get('recommendation')
        metasolver_result = (secret_payload or {}).get('metasolver_result')
        plan.extend([
            {
                'id': 'extract-secret-fragment',
                'title': 'Extraire le meilleur fragment de code',
                'status': 'completed' if selected_fragment else 'blocked',
                'automated': True,
                'tool': 'geoapp.plugins.listing.classify',
                'detail': (selected_fragment or {}).get('text'),
            },
            {
                'id': 'execute-direct-plugin',
                'title': 'Executer directement le plugin le plus specifique',
                'status': 'completed' if direct_plugin_result else ('planned' if direct_plugin_candidate and direct_plugin_candidate.get('should_run_directly') else 'skipped'),
                'automated': bool(direct_plugin_candidate and direct_plugin_candidate.get('should_run_directly')),
                'tool': str((direct_plugin_candidate or {}).get('plugin_name') or ''),
                'detail': (direct_plugin_result or {}).get('summary') or (direct_plugin_candidate or {}).get('reason'),
            },
            {
                'id': 'recommend-metasolver-plugins',
                'title': 'Recommander une liste de plugins metasolver',
                'status': 'completed' if recommendation else 'blocked',
                'automated': True,
                'tool': 'geoapp.plugins.metasolver.recommend',
                'detail': ', '.join((recommendation or {}).get('selected_plugins') or []),
            },
            {
                'id': 'execute-metasolver',
                'title': 'Executer metasolver avec la sous-liste recommandee',
                'status': 'completed' if metasolver_result else ('planned' if auto_execute and recommendation else 'planned'),
                'automated': auto_execute,
                'tool': 'plugin.metasolver',
                'detail': (metasolver_result or {}).get('summary'),
            },
        ])
    elif workflow_kind == 'formula':
        plan.extend([
            {
                'id': 'detect-formulas',
                'title': 'Detecter les formules de coordonnees',
                'status': 'completed' if (formula_payload or {}).get('formula_count') else 'blocked',
                'automated': True,
                'tool': 'formula-solver.detect-formula',
                'detail': f"{(formula_payload or {}).get('formula_count', 0)} formule(s)",
            },
            {
                'id': 'extract-questions',
                'title': 'Associer les questions aux variables',
                'status': 'completed' if formula_payload is not None else 'planned',
                'automated': True,
                'tool': 'formula-solver.find-questions',
                'detail': f"{(formula_payload or {}).get('found_question_count', 0)} question(s) trouvee(s)",
            },
            {
                'id': 'search-answers',
                'title': 'Chercher les reponses factuelles manquantes',
                'status': 'planned',
                'automated': False,
                'tool': 'formula-solver.search-answer',
            },
            {
                'id': 'calculate-final-coordinates',
                'title': 'Calculer les coordonnees finales',
                'status': 'planned',
                'automated': False,
                'tool': 'formula-solver.calculate-coordinates',
            },
        ])
    elif workflow_kind == 'hidden_content':
        plan.append({
            'id': 'inspect-hidden-html',
            'title': 'Inspecter le HTML cache avant tout decodage',
            'status': 'planned',
            'automated': False,
            'tool': 'geoapp.plugins.listing.classify',
            'detail': 'Commentaires HTML, styles inline, classes/ids CSS caches, attributs hidden',
        })
    elif workflow_kind == 'image_puzzle':
        plan.append({
            'id': 'inspect-images',
            'title': 'Analyser les images et lancer OCR/QR si necessaire',
            'status': 'planned',
            'automated': False,
            'detail': f"{classification.get('signal_summary', {}).get('image_count', 0)} image(s) detectee(s)",
        })
    elif workflow_kind == 'coord_transform':
        plan.append({
            'id': 'compare-waypoints',
            'title': 'Comparer coordonnees publiees, waypoints et indices de projection',
            'status': 'planned',
            'automated': False,
            'detail': f"{classification.get('signal_summary', {}).get('waypoint_count', 0)} waypoint(s) disponible(s)",
        })

    if any(item.get('name') == 'checker_available' for item in (classification.get('labels') or [])):
        plan.append({
            'id': 'validate-with-checker',
            'title': 'Valider l hypothese finale avec le checker',
            'status': 'planned',
            'automated': False,
            'tool': 'geoapp.checkers.run',
        })

    return _inject_hybrid_review_steps(plan, classification)


def _resolve_workflow_orchestrator(
    data: Dict[str, Any],
    *,
    max_secret_fragments: int,
    max_plugins: int,
    auto_execute: bool,
) -> Dict[str, Any]:
    listing_inputs = _load_listing_analysis_inputs(data)
    classification_response = _build_listing_classification_response(listing_inputs, max_secret_fragments)
    workflow_candidates = _build_workflow_candidates(classification_response)

    preferred_workflow = _normalize_workflow_kind(data.get('preferred_workflow'))
    if preferred_workflow and preferred_workflow != 'general':
        forced_candidate = next((item for item in workflow_candidates if item['kind'] == preferred_workflow), None)
        if not forced_candidate:
            forced_candidate = {
                'kind': preferred_workflow,
                'confidence': 1.0,
                'score': 1.0,
                'reason': "Workflow force explicitement par la requete.",
                'supporting_labels': [],
            }
        workflow = {**forced_candidate, 'forced': True}
    elif workflow_candidates:
        workflow = {**_select_primary_workflow_candidate(workflow_candidates, classification_response), 'forced': False}
    else:
        workflow = {
            'kind': 'general',
            'confidence': 0.2,
            'score': 0.2,
            'reason': "Aucun workflow specialise ne ressort nettement du listing.",
            'supporting_labels': [],
            'forced': False,
        }

    secret_payload: Optional[Dict[str, Any]] = None
    formula_payload: Optional[Dict[str, Any]] = None
    hidden_payload: Optional[Dict[str, Any]] = None
    image_payload: Optional[Dict[str, Any]] = None
    explanation: List[str] = [
        f"Workflow principal: {workflow['kind']} ({workflow['confidence']:.2f})",
        workflow.get('reason') or '',
    ]
    signal_summary = classification_response.get('signal_summary') if isinstance(classification_response.get('signal_summary'), dict) else {}
    if bool(signal_summary.get('is_ambiguous_hybrid')):
        ambiguous_domains = [
            str(item or '').strip()
            for item in (signal_summary.get('ambiguous_domains') or [])
            if str(item or '').strip()
        ]
        if ambiguous_domains:
            explanation.append(
                "Listing hybride ambigu entre "
                + ' / '.join(ambiguous_domains)
                + ". Confirmer plusieurs domaines avant de figer la resolution."
            )

    if workflow['kind'] == 'secret_code':
        selected_fragment = _select_primary_secret_fragment(classification_response, listing_inputs)
        direct_plugin_candidate = None
        direct_plugin_result = None
        recommendation = None
        metasolver_result = None

        if selected_fragment and isinstance(selected_fragment, dict):
            fragment_text = str(selected_fragment.get('text') or '').strip()
            if fragment_text:
                recommendation = _recommend_metasolver_plugins_response(
                    text=fragment_text,
                    requested_preset=(str(data.get('metasolver_preset') or '')).strip().lower(),
                    mode=(str(data.get('metasolver_mode') or 'decode')).strip().lower(),
                    max_plugins=max_plugins,
                )
                direct_plugin_candidate = _build_secret_code_direct_plugin_candidate(
                    listing_inputs,
                    classification_response,
                    recommendation=recommendation,
                )
                if auto_execute:
                    if direct_plugin_candidate and direct_plugin_candidate.get('should_run_directly'):
                        direct_plugin_result = _execute_direct_plugin_candidate(direct_plugin_candidate)
                    if not _direct_plugin_result_succeeded(direct_plugin_result) and recommendation:
                        metasolver_inputs = {
                            'text': fragment_text,
                            'mode': recommendation.get('mode') or 'decode',
                            'preset': recommendation.get('effective_preset') or 'all',
                            'plugin_list': recommendation.get('plugin_list') or '',
                            'max_plugins': max_plugins,
                        }
                        metasolver_result = _summarize_plugin_results(
                            get_plugin_manager().execute_plugin('metasolver', metasolver_inputs)
                        )
                        metasolver_result = _attach_metasolver_geographic_plausibility(metasolver_result, listing_inputs)

        secret_payload = {
            'selected_fragment': selected_fragment,
            'direct_plugin_candidate': direct_plugin_candidate,
            'direct_plugin_result': direct_plugin_result,
            'recommendation': recommendation,
            'metasolver_result': metasolver_result,
        }
        if selected_fragment:
            explanation.append(
                f"Fragment principal: {str(selected_fragment.get('text') or '')[:80]}"
            )
        if direct_plugin_candidate:
            explanation.append(
                f"Plugin direct candidat: {direct_plugin_candidate.get('plugin_name')} ({float(direct_plugin_candidate.get('confidence') or 0.0):.2f})"
            )
        if direct_plugin_result:
            explanation.append(
                f"Plugin direct execute: {direct_plugin_result.get('plugin_name')} - {direct_plugin_result.get('summary')}"
            )
        if recommendation:
            explanation.append(
                f"Plugins metasolver recommandes: {', '.join(recommendation.get('selected_plugins') or [])}"
            )
    elif workflow['kind'] == 'formula':
        formula_text = '\n\n'.join(
            part for part in (
                listing_inputs.get('description') or '',
                listing_inputs.get('waypoint_text') or '',
                listing_inputs.get('hint') or '',
            )
            if part
        )
        formula_result = get_plugin_manager().execute_plugin('formula_parser', {'text': formula_text})
        formulas = [
            item for item in (formula_result.get('results') or [])
            if isinstance(item, dict)
        ]
        variables = _extract_formula_variables(formulas)

        from gc_backend.services.formula_questions_service import formula_questions_service

        content = listing_inputs.get('geocache_record') or formula_text
        questions = formula_questions_service.extract_questions_with_regex(content, variables) if variables else {}
        found_question_count = len([value for value in questions.values() if value])

        formula_payload = {
            'formula_count': len(formulas),
            'formulas': formulas[:3],
            'variables': variables,
            'questions': questions,
            'found_question_count': found_question_count,
        }
        explanation.append(f"Formules detectees: {len(formulas)}")
        if variables:
            explanation.append(f"Variables detectees: {', '.join(variables)}")
        if found_question_count:
            explanation.append(f"Questions trouvees: {found_question_count}/{len(variables)}")
    elif workflow['kind'] == 'hidden_content':
        hidden_info = _extract_hidden_content_signals(listing_inputs.get('description_html') or '')
        hidden_payload = {
            'inspected': False,
            'hidden_signals': hidden_info.get('signals') or [],
            'comments': hidden_info.get('comments') or [],
            'hidden_texts': hidden_info.get('hidden_texts') or [],
            'items': hidden_info.get('items') or [],
            'candidate_secret_fragments': [],
            'selected_fragment': None,
            'recommendation': None,
            'summary': (
                f"{len(hidden_info.get('signals') or [])} signal(s) HTML cache detecte(s)"
                if hidden_info.get('signals') else
                'Aucun contenu HTML cache detaille n a encore ete inspecte.'
            ),
        }
        if hidden_payload['hidden_signals']:
            explanation.append(
                f"Signaux HTML caches: {', '.join((hidden_payload.get('hidden_signals') or [])[:3])}"
            )
    elif workflow['kind'] == 'image_puzzle':
        image_payload = _build_image_puzzle_execution(
            listing_inputs=listing_inputs,
            data=data,
            max_secret_fragments=max_secret_fragments,
            max_plugins=max_plugins,
            include_plugin_runs=False,
            inspected=False,
            max_vision_ocr_cost_units=0,
        )
        if image_payload.get('image_count'):
            explanation.append(f"Images detectees: {int(image_payload.get('image_count') or 0)}")
        if image_payload.get('items'):
            explanation.append(
                f"Indices image: {', '.join(str(item.get('reason') or '') for item in (image_payload.get('items') or [])[:2])}"
            )

    plan = _build_resolution_plan(
        workflow_kind=workflow['kind'],
        classification=classification_response,
        secret_payload=secret_payload,
        formula_payload=formula_payload,
        auto_execute=auto_execute,
    )

    response: Dict[str, Any] = {
        'source': classification_response.get('source'),
        'geocache': classification_response.get('geocache'),
        'title': classification_response.get('title'),
        'workflow': workflow,
        'workflow_candidates': workflow_candidates,
        'classification': classification_response,
        'plan': plan,
        'execution': {
            'secret_code': secret_payload,
            'formula': formula_payload,
            'hidden_content': hidden_payload,
            'image_puzzle': image_payload,
            'checker': None,
        },
        'next_actions': _recompute_workflow_next_actions(plan, classification_response),
        'explanation': [item for item in explanation if item],
    }
    previous_control = _extract_previous_workflow_control(data)
    control = _build_workflow_control(
        data=data,
        workflow_kind=str(workflow['kind']),
        plan=plan,
        classification=classification_response,
        execution=response['execution'],
        previous_control=previous_control,
    )
    _apply_workflow_control_to_plan(plan, control)
    response['next_actions'] = _recompute_workflow_next_actions(plan, classification_response)
    response['control'] = control
    if control.get('stop_reasons'):
        response['explanation'].extend(
            reason for reason in control.get('stop_reasons')[:2]
            if reason not in response['explanation']
        )
    elif control.get('summary') and control['summary'] not in response['explanation']:
        response['explanation'].append(str(control['summary']))
    return response


def _run_workflow_step_orchestrator(
    data: Dict[str, Any],
    *,
    max_secret_fragments: int,
    max_plugins: int,
) -> Dict[str, Any]:
    workflow_resolution = _resolve_workflow_orchestrator(
        data,
        max_secret_fragments=max_secret_fragments,
        max_plugins=max_plugins,
        auto_execute=False,
    )
    plan = workflow_resolution.get('plan') or []
    classification = workflow_resolution.get('classification') or {}
    listing_inputs = _load_listing_analysis_inputs(data)
    control = workflow_resolution.get('control') or {}
    supported_step_ids = set(SUPPORTED_AUTOMATED_WORKFLOW_STEPS)

    target_step_id = str(data.get('target_step_id') or '').strip()
    selected_step: Optional[Dict[str, Any]] = None
    if target_step_id:
        selected_step = next((step for step in plan if str(step.get('id') or '') == target_step_id), None)
        if not selected_step:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': f"Etape inconnue ou indisponible: {target_step_id}",
                'step': None,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }
        blocked_reason = _get_step_control_block_reason(target_step_id, control)
        if blocked_reason:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': blocked_reason,
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }
    else:
        selected_step = next(
            (
                step for step in plan
                if step.get('status') == 'planned'
                and str(step.get('id') or '') in supported_step_ids
                and _get_step_control_block_reason(str(step.get('id') or ''), control) is None
            ),
            None,
        )
        if not selected_step:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': str((control or {}).get('summary') or 'Aucune etape automatisable restante pour ce workflow.'),
                'step': None,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

    step_id = str(selected_step.get('id') or '')
    if step_id not in supported_step_ids:
        return {
            'status': 'blocked',
            'executed_step': None,
            'message': f"L etape '{step_id}' n est pas encore automatisable cote backend.",
            'step': selected_step,
            'result': None,
            'workflow_resolution': workflow_resolution,
        }

    execution = workflow_resolution.setdefault('execution', {})
    message = ''
    result_payload: Optional[Dict[str, Any]] = None

    if step_id == 'inspect-hidden-html':
        hidden_payload = _build_hidden_content_execution(
            listing_inputs=listing_inputs,
            data=data,
            max_secret_fragments=max_secret_fragments,
            max_plugins=max_plugins,
        )
        execution['hidden_content'] = hidden_payload
        detail = str(hidden_payload.get('summary') or 'Inspection HTML terminee.').strip()
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        if hidden_payload.get('selected_fragment'):
            workflow_resolution['next_actions'] = list(dict.fromkeys([
                'Appliquer la recommandation metasolver issue du fragment cache.',
                'Injecter le fragment cache principal dans metasolver ou le chat GeoApp.',
                *(workflow_resolution.get('next_actions') or []),
            ]))[:8]
        workflow_resolution.setdefault('explanation', []).append(f"Inspection HTML cache: {detail}")
        message = 'Inspection du HTML cache terminee.'
        result_payload = hidden_payload

    elif step_id == 'inspect-images':
        remaining_control = control.get('remaining') if isinstance(control.get('remaining'), dict) else {}
        image_payload = _build_image_puzzle_execution(
            listing_inputs=listing_inputs,
            data=data,
            max_secret_fragments=max_secret_fragments,
            max_plugins=max_plugins,
            max_vision_ocr_cost_units=max(0, int(remaining_control.get('vision_ocr_runs') or 0)),
        )
        execution['image_puzzle'] = image_payload
        detail = str(image_payload.get('summary') or 'Inspection images terminee.').strip()
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        if image_payload.get('selected_fragment'):
            workflow_resolution['next_actions'] = list(dict.fromkeys([
                'Appliquer la recommandation metasolver issue des indices image.',
                'Injecter le fragment image principal dans metasolver ou le chat GeoApp.',
                *(workflow_resolution.get('next_actions') or []),
            ]))[:8]
        workflow_resolution.setdefault('explanation', []).append(f"Inspection images: {detail}")
        message = 'Inspection des images terminee.'
        result_payload = image_payload

    elif step_id == 'execute-direct-plugin':
        secret_payload = execution.get('secret_code') or {}
        direct_plugin_candidate = secret_payload.get('direct_plugin_candidate') or {}
        if not direct_plugin_candidate or not direct_plugin_candidate.get('plugin_name'):
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucun plugin direct suffisamment fiable n est disponible pour ce fragment.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        direct_plugin_result = _execute_direct_plugin_candidate(direct_plugin_candidate)
        secret_payload['direct_plugin_result'] = direct_plugin_result
        execution['secret_code'] = secret_payload
        detail = direct_plugin_result.get('summary') or f"{direct_plugin_result.get('results_count', 0)} resultat(s)"
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        workflow_resolution.setdefault('explanation', []).append(
            f"Plugin direct execute: {direct_plugin_result.get('plugin_name')} - {detail}"
        )
        message = f"Plugin direct execute: {direct_plugin_result.get('plugin_name')}"
        result_payload = {
            'selected_fragment': secret_payload.get('selected_fragment'),
            'direct_plugin_candidate': direct_plugin_candidate,
            'direct_plugin_result': direct_plugin_result,
            'recommendation': secret_payload.get('recommendation'),
            'metasolver_result': secret_payload.get('metasolver_result'),
        }

    elif step_id == 'execute-metasolver':
        secret_payload = execution.get('secret_code') or {}
        selected_fragment = secret_payload.get('selected_fragment') or {}
        recommendation = secret_payload.get('recommendation') or {}
        fragment_text = str(selected_fragment.get('text') or '').strip()
        if not fragment_text or not recommendation:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucun fragment secret ou aucune recommandation metasolver disponible.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        metasolver_inputs = {
            'text': fragment_text,
            'mode': recommendation.get('mode') or 'decode',
            'preset': recommendation.get('effective_preset') or 'all',
            'plugin_list': recommendation.get('plugin_list') or '',
            'max_plugins': max_plugins,
        }
        metasolver_result = _summarize_plugin_results(
            get_plugin_manager().execute_plugin('metasolver', metasolver_inputs)
        )
        metasolver_result = _attach_metasolver_geographic_plausibility(metasolver_result, listing_inputs)
        secret_payload['metasolver_result'] = metasolver_result
        execution['secret_code'] = secret_payload
        detail = metasolver_result.get('summary') or f"{metasolver_result.get('results_count', 0)} resultat(s)"
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        workflow_resolution.setdefault('explanation', []).append(f"Metasolver execute: {detail}")
        message = 'Metasolver execute sur le fragment principal.'
        result_payload = {
            'selected_fragment': selected_fragment,
            'direct_plugin_candidate': secret_payload.get('direct_plugin_candidate'),
            'direct_plugin_result': secret_payload.get('direct_plugin_result'),
            'recommendation': recommendation,
            'metasolver_result': metasolver_result,
        }

    elif step_id == 'search-answers':
        formula_payload = execution.get('formula') or {}
        questions = formula_payload.get('questions') or {}
        searchable_questions = {
            str(variable).strip().upper(): str(question).strip()
            for variable, question in questions.items()
            if str(question or '').strip()
        }
        if not searchable_questions:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucune question exploitable a rechercher pour ce workflow formule.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        from gc_backend.services.web_search_service import web_search_service

        remaining_search_budget = int(((control.get('remaining') or {}).get('search_questions') or 0))
        if remaining_search_budget > 0:
            ordered_questions = list(searchable_questions.items())
            searchable_questions = dict(ordered_questions[:remaining_search_budget])
        if not searchable_questions:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Le budget de recherche web est epuise pour ce workflow.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        search_context_parts = [
            str(listing_inputs.get('title') or '').strip(),
            str(listing_inputs.get('hint') or '').strip(),
            str(data.get('search_context') or '').strip(),
        ]
        search_context = ' | '.join(part for part in search_context_parts if part)
        max_results = data.get('max_search_results', 5)
        try:
            max_results = max(1, min(10, int(max_results)))
        except (TypeError, ValueError):
            max_results = 5

        answers: Dict[str, Any] = {}
        found_count = 0
        missing: List[str] = []
        for variable, question in searchable_questions.items():
            results = web_search_service.search(question, search_context or None, max_results)
            best_answer = web_search_service.extract_answer(results)
            if best_answer:
                found_count += 1
            else:
                missing.append(variable)
            suggestions = _suggest_formula_value_candidates(best_answer, question) if best_answer else []
            answers[variable] = {
                'question': question,
                'best_answer': best_answer,
                'results': results[:3],
                'suggested_values': suggestions,
                'recommended_value_type': (suggestions[0] if suggestions else {}).get('type'),
            }

        answer_search = {
            'answers': answers,
            'found_count': found_count,
            'missing': missing,
            'search_context': search_context,
        }
        formula_payload['answer_search'] = answer_search
        execution['formula'] = formula_payload
        detail = f"{found_count}/{len(searchable_questions)} reponse(s) trouvee(s)"
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        workflow_resolution.setdefault('explanation', []).append(f"Recherche web des reponses: {detail}")
        message = 'Recherche web terminee pour les questions de formule.'
        result_payload = answer_search

    elif step_id == 'calculate-final-coordinates':
        formula_payload = execution.get('formula') or {}
        formulas = formula_payload.get('formulas') or []
        if not formulas:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucune formule exploitable pour calculer les coordonnees.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        try:
            formula_index = int(data.get('formula_index', 0))
        except (TypeError, ValueError):
            formula_index = 0
        if formula_index < 0 or formula_index >= len(formulas):
            formula_index = 0

        selected_formula = formulas[formula_index]
        north_formula, east_formula = _extract_formula_coordinates(selected_formula)
        if not north_formula or not east_formula:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'La formule selectionnee ne contient pas de composantes nord/est exploitables.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        values = _derive_formula_values(data)
        if not values:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucune valeur de variable fournie pour le calcul final.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        from gc_backend.utils.coordinate_calculator import CoordinateCalculator

        calculator = CoordinateCalculator()
        calculation = calculator.calculate_coordinates(north_formula, east_formula, values)
        if calculation.get('status') == 'error':
            return {
                'status': 'error',
                'executed_step': None,
                'message': str(calculation.get('error') or 'Erreur de calcul des coordonnees'),
                'step': selected_step,
                'result': calculation,
                'workflow_resolution': workflow_resolution,
            }

        geocache_record = listing_inputs.get('geocache_record')
        if geocache_record and geocache_record.latitude is not None and geocache_record.longitude is not None:
            distance_km = calculator.calculate_distance(
                geocache_record.latitude,
                geocache_record.longitude,
                calculation['coordinates']['latitude'],
                calculation['coordinates']['longitude'],
            )
            calculation['distance'] = {
                'km': round(distance_km, 2),
                'miles': round(distance_km * 0.621371, 2),
            }
        geographic_plausibility = _build_geographic_plausibility(calculation.get('coordinates'), listing_inputs)
        if geographic_plausibility:
            calculation['geographic_plausibility'] = geographic_plausibility

        calculated_coordinates = {
            'formula_index': formula_index,
            'north_formula': north_formula,
            'east_formula': east_formula,
            'values': values,
            **calculation,
        }
        formula_payload['calculated_coordinates'] = calculated_coordinates
        execution['formula'] = formula_payload
        coordinates_detail = (
            ((calculation.get('coordinates') or {}).get('ddm'))
            or ((calculation.get('coordinates') or {}).get('decimal'))
            or 'Coordonnees calculees'
        )
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=coordinates_detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        workflow_resolution.setdefault('explanation', []).append(f"Coordonnees calculees: {coordinates_detail}")
        message = 'Coordonnees finales calculees.'
        result_payload = calculated_coordinates

    elif step_id == 'validate-with-checker':
        candidate = _resolve_checker_candidate(data, workflow_resolution)
        if not candidate:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': 'Aucun candidat exploitable pour le checker. Fournissez checker_candidate ou calculez d abord une hypothese.',
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        target = _resolve_checker_target(listing_inputs, data)
        try:
            checker_result = _run_checker_with_target(
                url=target['url'],
                candidate=candidate,
                wp=target.get('wp'),
                interactive=bool(target.get('interactive')),
                provider=str(target.get('provider') or 'generic'),
                auto_login=bool(data.get('checker_auto_login', True)),
                login_timeout_sec=int(data.get('checker_login_timeout_sec') or 180),
                timeout_sec=int(data.get('checker_timeout_sec') or 300),
            )
        except RuntimeError as checker_runtime_error:
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': str(checker_runtime_error),
                'step': selected_step,
                'result': None,
                'workflow_resolution': workflow_resolution,
            }

        checker_payload = {
            'checker_name': target.get('name'),
            'checker_url': target.get('url'),
            'provider': target.get('provider'),
            'interactive': bool(target.get('interactive')),
            'candidate': candidate,
            'wp': target.get('wp'),
            'result': checker_result.get('result'),
            'status': checker_result.get('status'),
            'message': checker_result.get('message'),
        }
        execution['checker'] = checker_payload

        raw_result = checker_result.get('result') or {}
        result_status = str(raw_result.get('status') or checker_result.get('status') or '').strip().lower()
        result_message = str(raw_result.get('message') or checker_result.get('message') or '').strip()
        if checker_result.get('status') == 'requires_login':
            selected_step = _mark_plan_step(plan, step_id, status='blocked', detail=checker_result.get('message'), automated=False) or selected_step
            workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
            workflow_resolution.setdefault('explanation', []).append(str(checker_result.get('message') or 'Checker requires login'))
            return {
                'status': 'blocked',
                'executed_step': None,
                'message': str(checker_result.get('message') or 'Checker requires login'),
                'step': selected_step,
                'result': checker_payload,
                'workflow_resolution': workflow_resolution,
            }

        detail = result_message or f"Checker status: {result_status or 'unknown'}"
        selected_step = _mark_plan_step(plan, step_id, status='completed', detail=detail, automated=True) or selected_step
        workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
        workflow_resolution.setdefault('explanation', []).append(f"Checker execute: {detail}")
        message = 'Validation checker executee.'
        result_payload = checker_payload

    updated_control = _build_workflow_control(
        data=data,
        workflow_kind=str((workflow_resolution.get('workflow') or {}).get('kind') or 'general'),
        plan=plan,
        classification=classification,
        execution=execution,
        previous_control=_extract_previous_workflow_control(data),
    )
    _apply_workflow_control_to_plan(plan, updated_control)
    workflow_resolution['next_actions'] = _recompute_workflow_next_actions(plan, classification)
    workflow_resolution['control'] = updated_control
    if updated_control.get('stop_reasons'):
        for reason in updated_control.get('stop_reasons')[:2]:
            if reason not in workflow_resolution.setdefault('explanation', []):
                workflow_resolution['explanation'].append(reason)

    return {
        'status': 'success',
        'executed_step': step_id,
        'message': message,
        'step': selected_step,
        'result': result_payload,
        'workflow_resolution': workflow_resolution,
    }

class BatchPluginTask:
    """
    Classe pour gérer l'exécution batch d'un plugin sur plusieurs géocaches.
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
        
        # État de la tâche
        self.status = 'pending'  # pending, running, completed, failed, cancelled
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.cancelled = False
        
        # Résultats par géocache
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
        Exécute la tâche batch selon le mode configuré.
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
        Exécution séquentielle des géocaches.
        """
        for i, geocache in enumerate(self.geocaches):
            if self.cancelled:
                break
            
            result = self.results[i]
            result['status'] = 'executing'
            result['started_at'] = datetime.utcnow()
            
            try:
                start_time = time.time()
                
                # Préparer les inputs pour cette géocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # Exécuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les résultats
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
        Exécution parallèle des géocaches avec ThreadPoolExecutor.
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
                
                # Préparer les inputs pour cette géocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # Exécuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les résultats
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
        
        # Exécuter en parallèle avec ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            # Soumettre toutes les tâches
            future_to_index = {
                executor.submit(execute_single_geocache, (geocache, i)): i 
                for i, geocache in enumerate(self.geocaches)
            }
            
            # Traiter les résultats au fur et à mesure
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
        Prépare les inputs du plugin pour une géocache spécifique.
        """
        inputs = self.inputs.copy()
        
        # Injecter les données spécifiques à la géocache
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
        Traite les résultats du plugin (détection de coordonnées, etc.).
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
                        # Utiliser directement la fonction de détection (pas l'API)
                        from gc_backend.blueprints.coordinates import detect_gps_coordinates
                        
                        logger.info(f"[Batch] Détection de coordonnées dans: {text_output[:100]}...")
                        
                        coords = detect_gps_coordinates(text_output, include_numeric_only=False)
                        
                        if coords.get('exist'):
                            logger.info(f"[Batch] Coordonnées trouvées: {coords.get('ddm')}")
                            processed['coordinates'] = {
                                'latitude': coords.get('decimal_latitude', 0),
                                'longitude': coords.get('decimal_longitude', 0),
                                'formatted': coords.get('ddm', '')
                            }
                            break
                        else:
                            logger.info(f"[Batch] Aucune coordonnée détectée dans ce résultat")
                    except Exception as e:
                        logger.warning(f"Error detecting coordinates: {str(e)}")
                        import traceback
                        traceback.print_exc()
        
        return processed
    
    def cancel(self):
        """
        Annule la tâche.
        """
        self.cancelled = True
        if self.status == 'running':
            self.status = 'cancelled'
        logger.info(f"Batch task {self.task_id} cancellation requested")
    
    def get_status(self) -> Dict:
        """
        Retourne le statut actuel de la tâche.
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
    Récupère l'instance du PluginManager.
    
    Returns:
        PluginManager: Instance du gestionnaire
        
    Raises:
        RuntimeError: Si le manager n'est pas initialisé
    """
    if _plugin_manager is None:
        raise RuntimeError(
            "PluginManager non initialisé. "
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
                "message": f"Le body de la requête doit être un JSON valide: {str(json_error)}"
            }), 400

        if not data or not isinstance(data, dict):
            return jsonify({
                "error": "Requête invalide",
                "message": "Le body doit être un objet JSON"
            }), 400

        context = data.get('context')
        if context is not None and not isinstance(context, dict):
            return jsonify({
                "error": "Requête invalide",
                "message": "Le champ 'context' doit être un objet"
            }), 400

        from gc_backend.plugins.scoring import score_text

        if 'texts' in data:
            texts = data.get('texts')
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                return jsonify({
                    "error": "Requête invalide",
                    "message": "Le champ 'texts' doit être une liste de strings"
                }), 400

            results: List[Dict[str, Any]] = []
            for t in texts:
                results.append(score_text(t, context=context or {}))
            return jsonify({"results": results}), 200

        text = data.get('text')
        if not isinstance(text, str):
            return jsonify({
                "error": "Requête invalide",
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
        category (str, optional): Filtrer par catégorie
        enabled (bool, optional): Filtrer par statut (true/false)
        
    Returns:
        JSON: {
            "plugins": [liste des plugins],
            "total": nombre total,
            "filters": filtres appliqués
        }
        
    Example:
        GET /api/plugins
        GET /api/plugins?source=official
        GET /api/plugins?category=Substitution
        GET /api/plugins?enabled=true
    """
    try:
        manager = get_plugin_manager()
        
        # Récupérer les paramètres de filtre
        source = request.args.get('source')
        category = request.args.get('category')
        enabled_param = request.args.get('enabled')
        
        # Convertir enabled en booléen
        enabled_only = True  # Par défaut
        if enabled_param is not None:
            enabled_only = enabled_param.lower() in ['true', '1', 'yes']
        
        # Lister les plugins avec filtres
        plugins = manager.list_plugins(
            source=source,
            category=category,
            enabled_only=enabled_only
        )
        
        logger.info(
            f"Liste plugins : {len(plugins)} résultats "
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
    Liste les plugins éligibles au metasolver, optionnellement filtrés par preset.

    Query Parameters:
        preset (str, optional): Nom du preset à appliquer (défaut: 'all')

    Returns:
        JSON: {
            "preset": nom du preset,
            "preset_filter": filtre appliqué,
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
    Analyse la signature d'entrée d'un texte et recommande une sous-liste de plugins metasolver.

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
            "message": f"Le body doit être un JSON valide: {str(json_error)}"
        }), 400

    if not data or not isinstance(data, dict):
        return jsonify({
            "error": "Requête invalide",
            "message": "Le body doit être un objet JSON"
        }), 400

    text = data.get('text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({
            "error": "Requête invalide",
            "message": "Le champ 'text' (string non vide) est requis"
        }), 400

    requested_preset = (data.get('preset') or '').strip().lower()
    mode = (data.get('mode') or 'decode').strip().lower()
    max_plugins = _normalize_max_plugins(data.get('max_plugins'), default=8)

    try:
        payload = _recommend_metasolver_plugins_response(
            text=text,
            requested_preset=requested_preset,
            mode=mode,
            max_plugins=max_plugins,
        )
        return jsonify(payload), 200
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
        try:
            listing_inputs = _load_listing_analysis_inputs(data)
        except ValueError as value_error:
            return jsonify({
                "error": "Requete invalide",
                "message": str(value_error)
            }), 400
        except LookupError as lookup_error:
            return jsonify({
                "error": "Geocache introuvable",
                "message": str(lookup_error)
            }), 404

        return jsonify(_build_listing_classification_response(listing_inputs, max_secret_fragments)), 200
    except Exception as e:
        logger.error(f"Erreur classification listing: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur classification listing",
            "message": str(e)
        }), 500


@bp.route('/workflow/resolve', methods=['POST'])
def resolve_workflow():
    """
    Orchestre la resolution initiale d'un listing:
    - classification
    - choix du workflow principal
    - pre-analyse deterministe du workflow
    - execution optionnelle du metasolver sur le premier fragment secret

    Request body:
        {
            "geocache_id": 123,
            "preferred_workflow": "secret_code",
            "auto_execute": false,
            "max_secret_fragments": 6,
            "max_plugins": 8
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
    max_plugins = _normalize_max_plugins(data.get('max_plugins'), default=8)
    auto_execute = bool(data.get('auto_execute', False))

    try:
        payload = _resolve_workflow_orchestrator(
            data,
            max_secret_fragments=max_secret_fragments,
            max_plugins=max_plugins,
            auto_execute=auto_execute,
        )
        return jsonify(payload), 200
    except ValueError as value_error:
        return jsonify({
            "error": "Requete invalide",
            "message": str(value_error)
        }), 400
    except LookupError as lookup_error:
        return jsonify({
            "error": "Geocache introuvable",
            "message": str(lookup_error)
        }), 404
    except Exception as e:
        logger.error(f"Erreur orchestrateur workflow: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur orchestrateur workflow",
            "message": str(e)
        }), 500


@bp.route('/workflow/run-next-step', methods=['POST'])
def run_workflow_next_step():
    """
    Execute la prochaine etape automatisable de l orchestrateur, ou une etape ciblee.

    Request body:
        {
            "geocache_id": 123,
            "target_step_id": "search-answers",
            "formula_answers": {"A": "42"},
            "formula_value_types": {"A": "value"},
            "formula_values": {"B": 5}
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
    max_plugins = _normalize_max_plugins(data.get('max_plugins'), default=8)

    try:
        payload = _run_workflow_step_orchestrator(
            data,
            max_secret_fragments=max_secret_fragments,
            max_plugins=max_plugins,
        )
        return jsonify(payload), 200
    except ValueError as value_error:
        return jsonify({
            "error": "Requete invalide",
            "message": str(value_error)
        }), 400
    except LookupError as lookup_error:
        return jsonify({
            "error": "Geocache introuvable",
            "message": str(lookup_error)
        }), 404
    except Exception as e:
        logger.error(f"Erreur execution workflow step: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur execution workflow step",
            "message": str(e)
        }), 500


@bp.route('/metasolver/execute-stream', methods=['POST'])
def metasolver_execute_stream():
    """
    Exécute le metasolver en mode streaming SSE.

    Chaque sous-plugin exécuté émet des événements en temps réel :
    - init         : liste des candidats
    - plugin_start : un sous-plugin démarre
    - plugin_done  : un sous-plugin a terminé (avec résultats)
    - plugin_error : un sous-plugin a échoué
    - progress     : avancement global (pourcentage)
    - result       : résultat final complet

    Request Body (JSON):
        inputs (dict): Paramètres d'entrée identiques à /metasolver/execute

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
            "message": f"Le body doit être un JSON valide: {str(json_error)}"
        }), 400

    if not data or 'inputs' not in data:
        return jsonify({
            "error": "Requête invalide",
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

    # Accéder à l'instance brute du plugin pour appeler execute_streaming
    raw_instance = getattr(wrapper, '_instance', None)
    if not raw_instance or not hasattr(raw_instance, 'execute_streaming'):
        return jsonify({
            "error": "Streaming non supporté",
            "message": "Le plugin metasolver ne supporte pas le mode streaming"
        }), 500

    logger.info(f"Démarrage exécution streaming metasolver avec inputs: {list(inputs.keys())}")

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
            logger.info("[streaming] execute_streaming generator exhausted — all events sent")
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
    Récupère les informations détaillées d'un plugin.
    
    Args:
        plugin_name (str): Nom du plugin
        
    Returns:
        JSON: Informations complètes du plugin incluant metadata
        
    Example:
        GET /api/plugins/caesar
    """
    try:
        manager = get_plugin_manager()
        
        plugin_info = manager.get_plugin_info(plugin_name)
        
        if not plugin_info:
            logger.warning(f"Plugin non trouvé: {plugin_name}")
            return jsonify({
                "error": "Plugin non trouvé",
                "plugin_name": plugin_name
            }), 404
        
        logger.info(f"Informations récupérées pour plugin: {plugin_name}")
        
        return jsonify(plugin_info), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la récupération du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la récupération des informations",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/interface', methods=['GET'])
def get_plugin_interface(plugin_name: str):
    """
    Génère l'interface HTML du formulaire pour un plugin.
    
    L'interface est générée dynamiquement à partir des input_types
    définis dans le plugin.json.
    
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
                "error": "Plugin non trouvé",
                "plugin_name": plugin_name
            }), 404
        
        # Générer l'interface HTML
        html = _generate_plugin_interface_html(plugin_info)
        
        logger.info(f"Interface générée pour plugin: {plugin_name}")
        
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la génération de l'interface pour {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la génération de l'interface",
            "message": str(e)
        }), 500


# =============================================================================
# Routes d'exécution
# =============================================================================

@bp.route('/<plugin_name>/execute', methods=['POST'])
def execute_plugin(plugin_name: str):
    """
    Exécute un plugin de manière synchrone.
    
    Cette route est adaptée pour les plugins rapides (< 1s).
    Pour les plugins longs, utiliser /api/tasks (Phase 2.2).
    
    Args:
        plugin_name (str): Nom du plugin à exécuter
        
    Request Body (JSON):
        inputs (dict): Paramètres d'entrée du plugin
        
    Returns:
        JSON: Résultat de l'exécution au format standardisé
        
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
        
        # Récupérer les inputs depuis le body JSON (gestion explicite des erreurs JSON)
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            return jsonify({
                "error": "JSON invalide",
                "message": f"Le body de la requête doit être un JSON valide: {str(json_error)}"
            }), 400
        
        if not data or 'inputs' not in data:
            return jsonify({
                "error": "Requête invalide",
                "message": "Le champ 'inputs' est requis dans le body JSON"
            }), 400
        
        inputs = data['inputs']
        
        logger.info(
            f"Exécution synchrone du plugin {plugin_name} "
            f"avec inputs: {list(inputs.keys())}"
        )
        
        # Exécuter le plugin
        result = manager.execute_plugin(plugin_name, inputs)
        
        if not result:
            return jsonify({
                "error": "Erreur d'exécution",
                "message": f"Le plugin {plugin_name} n'a pas pu être exécuté"
            }), 500
        
        logger.info(
            f"Plugin {plugin_name} exécuté avec succès "
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
         
        # Tracking : si le plugin s'exécute avec succès sur une géocache, enregistrer dans l'archive
        try:
            geocache_id_raw = inputs.get('geocache_id')
            has_results = bool(result.get('results')) or result.get('status') == 'success'
            if geocache_id_raw and has_results:
                geocache_for_tracking = Geocache.query.get(int(geocache_id_raw))
                if geocache_for_tracking:
                    from ..geocaches.archive_service import ArchiveService
                    ArchiveService.add_resolution_plugin(geocache_for_tracking.gc_code, plugin_name)
        except Exception:
            pass  # Le tracking ne doit jamais bloquer l'exécution du plugin

        return jsonify(result), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de l'exécution du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur d'exécution",
            "message": str(e)
        }), 500


# =============================================================================
# Routes de gestion
# =============================================================================

@bp.route('/discover', methods=['POST'])
def discover_plugins():
    """
    Redéclenche la découverte des plugins.
    
    Scanne les répertoires plugins/official/ et plugins/custom/
    pour découvrir les nouveaux plugins ou détecter les modifications.
    
    Returns:
        JSON: {
            "discovered": nombre de plugins découverts,
            "plugins": liste des plugins,
            "errors": erreurs éventuelles
        }
        
    Example:
        POST /api/plugins/discover
    """
    try:
        manager = get_plugin_manager()
        
        logger.info("Déclenchement de la découverte de plugins")
        
        # Lancer la découverte
        discovered = manager.discover_plugins()
        
        # Récupérer les erreurs
        errors = manager.get_discovery_errors()
        
        logger.info(
            f"Découverte terminée: {len(discovered)} plugins, "
            f"{len(errors)} erreurs"
        )
        
        return jsonify({
            "discovered": len(discovered),
            "plugins": discovered,
            "errors": errors,
            "message": f"{len(discovered)} plugin(s) découvert(s)"
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la découverte: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la découverte",
            "message": str(e)
        }), 500


@bp.route('/status', methods=['GET'])
def get_plugins_status():
    """
    Récupère le statut de tous les plugins (enabled, loaded, errors).
    
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
        
        logger.info(f"Statut récupéré pour {len(status)} plugins")
        
        return jsonify({
            "plugins": status,
            "total": len(status),
            "loaded": sum(1 for p in status.values() if p['loaded']),
            "enabled": sum(1 for p in status.values() if p['enabled'])
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la récupération du statut",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/reload', methods=['POST'])
def reload_plugin(plugin_name: str):
    """
    Recharge un plugin (décharge puis recharge).
    
    Utile après modification du code du plugin.
    
    Args:
        plugin_name (str): Nom du plugin à recharger
        
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
                "message": f"Plugin {plugin_name} rechargé avec succès"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Échec du rechargement du plugin {plugin_name}"
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
# Utilitaires de génération HTML
# =============================================================================

def _generate_plugin_interface_html(plugin_info: Dict[str, Any]) -> str:
    """
    Génère l'interface HTML d'un plugin à partir de ses métadonnées.
    
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
                    <button type="submit" class="btn-primary">Exécuter</button>
                    <button type="reset" class="btn-secondary">Réinitialiser</button>
                </div>
            </form>
        </div>
    </div>
    <script>
        // Pré-remplissage automatique des champs à partir des paramètres d'URL (ex: ?text=...)
        document.addEventListener('DOMContentLoaded', function() {
            const urlParams = new URLSearchParams(window.location.search);
            
            // Pour chaque champ du formulaire
            const form = document.getElementById('plugin-form');
            if (form) {
                Array.from(form.elements).forEach(element => {
                    if (element.name && urlParams.has(element.name)) {
                        const paramValue = urlParams.get(element.name);
                        
                        // Gestion spécifique selon le type
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
    Exécute un plugin sur plusieurs géocaches en mode batch.
    
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
        
        # Validation des paramètres requis
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
        
        # Options par défaut
        execution_mode = options.get('execution_mode', 'sequential')
        max_concurrency = options.get('max_concurrency', 3)
        detect_coordinates = options.get('detect_coordinates', True)
        include_images = options.get('include_images', False)
        
        # Validation du mode d'exécution
        if execution_mode not in ['sequential', 'parallel']:
            return jsonify({"error": "execution_mode must be 'sequential' or 'parallel'"}), 400
        
        # Créer une tâche batch
        task_id = str(uuid.uuid4())
        
        # Récupérer les informations des géocaches
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
        
        # Démarrer la tâche en arrière-plan
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
        
        # Stocker la tâche
        batch_tasks[task_id] = batch_task
        
        # Démarrer l'exécution en arrière-plan
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
    Récupère le statut d'une tâche batch.
    
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
                    "formatted": "N 48° 07.380 E 002° 27.360"
                }
            }
        ],
        "started_at": "2023-...",
        "completed_at": "2023-..."  # si terminé
    }
    """
    if task_id not in batch_tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = batch_tasks[task_id]
    return jsonify(task.get_status())

@bp.route('/batch-cancel/<task_id>', methods=['POST'])
def cancel_batch_task(task_id):
    """
    Annule une tâche batch en cours.
    
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
    Liste toutes les tâches batch (actives et terminées).
    
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
