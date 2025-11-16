import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from ..models import AppConfig
from ..database import db


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'shared' / 'preferences' / 'geo-preferences-schema.json'


@lru_cache
def load_preference_schema() -> Dict[str, Any]:
    with _schema_path().open(encoding='utf-8') as handle:
        return json.load(handle)


def get_preference_definition(key: str) -> Optional[Dict[str, Any]]:
    schema = load_preference_schema()
    return schema.get('properties', {}).get(key)


def list_preferences() -> Dict[str, Any]:
    properties = load_preference_schema().get('properties', {})
    preferences: Dict[str, Any] = {}
    for key, definition in properties.items():
        stored = AppConfig.get_value(key)
        if stored is not None:
            preferences[key] = _deserialize_value(stored)
        elif 'default' in definition:
            preferences[key] = definition['default']
        else:
            preferences[key] = None
    return preferences


def get_preference_value(key: str) -> Any:
    definition = get_preference_definition(key)
    if not definition:
        raise KeyError(f"Préférence inconnue: {key}")

    stored = AppConfig.get_value(key)
    if stored is not None:
        return _deserialize_value(stored)
    return definition.get('default')


def get_value_or_default(key: str, fallback: Any) -> Any:
    """
    Récupère la valeur d'une préférence ou retourne une valeur de secours.
    """
    try:
        value = get_preference_value(key)
        return fallback if value is None else value
    except KeyError:
        return fallback


def set_preference_value(key: str, value: Any) -> Any:
    definition = get_preference_definition(key)
    if not definition:
        raise KeyError(f"Préférence inconnue: {key}")

    normalized = _normalize_value(definition, value)
    AppConfig.set_value(key, json.dumps(normalized))
    db.session.commit()
    return normalized


def set_preferences_bulk(values: Dict[str, Any]) -> Dict[str, Any]:
    updated: Dict[str, Any] = {}
    for key, value in values.items():
        updated[key] = set_preference_value(key, value)
    return updated


def _normalize_value(definition: Dict[str, Any], value: Any) -> Any:
    pref_type = definition.get('type')

    if pref_type == 'boolean':
        if isinstance(value, bool):
            normalized = value
        elif isinstance(value, str):
            normalized = value.lower() in ('true', '1', 'yes', 'on')
        else:
            raise ValueError('Valeur booléenne attendue')
    elif pref_type == 'integer':
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            raise ValueError('Valeur entière attendue')
    elif pref_type == 'number':
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            raise ValueError('Valeur numérique attendue')
    else:
        normalized = str(value) if value is not None else None

    if normalized is None:
        return None

    enum_values = definition.get('enum')
    if enum_values and normalized not in enum_values:
        raise ValueError(f"Valeur invalide. Options: {enum_values}")

    minimum = definition.get('minimum')
    maximum = definition.get('maximum')
    if minimum is not None and normalized < minimum:
        raise ValueError(f"Valeur minimale {minimum}")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"Valeur maximale {maximum}")

    return normalized


def _deserialize_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw

