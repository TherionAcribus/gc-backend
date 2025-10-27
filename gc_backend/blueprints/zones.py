from flask import Blueprint, jsonify, request

from ..database import db
from ..models import Zone, AppConfig


bp = Blueprint('zones', __name__)


@bp.get('/api/zones')
def list_zones():
    zones = Zone.query.order_by(Zone.created_at.desc()).all()
    return jsonify([z.to_dict() for z in zones])


@bp.post('/api/zones')
def create_zone():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    description = data.get('description') or ''
    if not name:
        return jsonify({'error': 'name requis'}), 400

    z = Zone(name=name, description=description)
    db.session.add(z)
    db.session.commit()

    return jsonify(z.to_dict()), 201


@bp.delete('/api/zones/<int:zone_id>')
def delete_zone(zone_id: int):
    """Supprime une zone."""
    zone = Zone.query.get_or_404(zone_id)

    # Vérifier si la zone contient des géocaches (placeholder - pour l'instant on supprime toujours)
    # TODO: implémenter la logique de suppression des géocaches associées si nécessaire

    db.session.delete(zone)
    db.session.commit()

    return jsonify({'message': f'Zone "{zone.name}" supprimée', 'id': zone_id}), 200


@bp.get('/api/active-zone')
def get_active_zone():
    zone_id_str = AppConfig.get_value('active_zone_id')
    if not zone_id_str:
        return jsonify(None)
    try:
        zone = Zone.query.get(int(zone_id_str))
        return jsonify(zone.to_dict() if zone else None)
    except Exception:
        return jsonify(None)


@bp.post('/api/active-zone')
def set_active_zone():
    data = request.get_json(silent=True) or {}
    zone_id = data.get('zone_id')
    if zone_id is None:
        AppConfig.set_value('active_zone_id', None)
        db.session.commit()
        return jsonify(None)
    zone = Zone.query.get_or_404(zone_id)
    AppConfig.set_value('active_zone_id', str(zone.id))
    db.session.commit()
    return jsonify(zone.to_dict())


# La route /api/zones/<zone_id>/geocaches est fournie par le blueprint geocaches


