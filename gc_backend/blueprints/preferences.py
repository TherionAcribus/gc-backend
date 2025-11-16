"""
Blueprint REST pour la gestion centralisée des préférences GeoApp.

Expose :
- GET /api/preferences
- GET /api/preferences/<key>
- PUT /api/preferences/<key>
- PATCH /api/preferences
- GET /api/preferences/schema
"""

from flask import Blueprint, jsonify, request
from loguru import logger

from ..utils.preferences import (
    list_preferences,
    get_preference_value,
    set_preference_value,
    set_preferences_bulk,
    get_preference_definition,
    load_preference_schema,
)

bp = Blueprint('preferences', __name__, url_prefix='/api/preferences')


@bp.get('')
def get_preferences():
    include_schema = request.args.get('includeSchema', 'false').lower() in ('1', 'true', 'yes')
    preferences = list_preferences()
    response = {
        'preferences': preferences,
        'version': load_preference_schema().get('version')
    }
    if include_schema:
        response['schema'] = load_preference_schema()
    return jsonify(response)


@bp.get('/schema')
def get_preferences_schema():
    return jsonify(load_preference_schema())


@bp.get('/<path:key>')
def get_preference(key: str):
    try:
        value = get_preference_value(key)
        definition = get_preference_definition(key)
        return jsonify({
            'key': key,
            'value': value,
            'definition': definition
        })
    except KeyError:
        return jsonify({'error': 'Préférence inconnue', 'key': key}), 404


@bp.put('/<path:key>')
def update_preference(key: str):
    try:
        payload = request.get_json(force=True) or {}
    except Exception as error:  # pragma: no cover
        logger.error('JSON invalide pour /api/preferences/%s: %s', key, error)
        return jsonify({'error': 'JSON invalide', 'message': str(error)}), 400

    if 'value' not in payload:
        return jsonify({'error': "Le champ 'value' est requis"}), 400

    try:
        value = set_preference_value(key, payload['value'])
        logger.info('Préférence %s mise à jour -> %s', key, value)
        return jsonify({'key': key, 'value': value})
    except KeyError:
        return jsonify({'error': 'Préférence inconnue', 'key': key}), 404
    except ValueError as error:
        return jsonify({'error': 'Valeur invalide', 'message': str(error)}), 400


@bp.patch('')
def update_preferences_bulk():
    try:
        payload = request.get_json(force=True) or {}
    except Exception as error:  # pragma: no cover
        logger.error('JSON invalide pour PATCH /api/preferences: %s', error)
        return jsonify({'error': 'JSON invalide', 'message': str(error)}), 400

    if 'values' not in payload or not isinstance(payload['values'], dict):
        return jsonify({'error': "Le champ 'values' doit être un objet"}), 400

    try:
        updated = set_preferences_bulk(payload['values'])
        logger.info('Préférences mises à jour en bulk: %s', ', '.join(updated.keys()))
        return jsonify({'updated': updated})
    except KeyError as error:
        return jsonify({'error': 'Préférence inconnue', 'message': str(error)}), 404
    except ValueError as error:
        return jsonify({'error': 'Valeur invalide', 'message': str(error)}), 400

