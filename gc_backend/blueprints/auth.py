"""
Blueprint pour l'authentification Geocaching.com.

Routes API pour:
- Login avec credentials (username/password)
- Login avec cookies du navigateur
- Logout
- Vérification du statut d'authentification
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from flask import Blueprint, jsonify, request

from ..services.geocaching_auth import (
    AuthMethod,
    AuthStatus,
    get_auth_service,
)

logger = logging.getLogger(__name__)

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _auth_state_to_dict(state) -> dict:
    """Convertit un AuthState en dictionnaire JSON-serializable."""
    result = {
        "status": state.status.value,
        "method": state.method.value,
        "error_message": state.error_message,
        "last_check": state.last_check.isoformat() if state.last_check else None,
    }
    
    if state.user_info:
        result["user"] = {
            "username": state.user_info.username,
            "reference_code": state.user_info.reference_code,
            "user_type": state.user_info.user_type,
            "public_guid": state.user_info.public_guid,
            "avatar_url": state.user_info.avatar_url,
            "finds_count": state.user_info.finds_count,
            "hides_count": state.user_info.hides_count,
            "favorite_points": state.user_info.favorite_points,
            "awarded_favorite_points": state.user_info.awarded_favorite_points,
            "stats_last_updated": state.user_info.stats_last_updated.isoformat() 
                if state.user_info.stats_last_updated else None,
        }
    else:
        result["user"] = None
    
    return result


@bp.route('/status', methods=['GET'])
def get_auth_status():
    """
    Récupère le statut d'authentification actuel.
    
    Query params:
        force: Si "true", force une vérification même si le cache est valide
        
    Returns:
        {
            "status": "logged_in" | "logged_out" | "not_configured" | ...,
            "method": "credentials" | "browser_cookies" | "none",
            "user": { "username": "...", "user_type": "Premium", ... } | null,
            "error_message": "..." | null,
            "last_check": "2025-01-26T08:30:00" | null
        }
    """
    force = request.args.get('force', '').lower() == 'true'
    
    auth_service = get_auth_service()
    state = auth_service.get_auth_state(force_check=force)
    
    return jsonify(_auth_state_to_dict(state))


@bp.route('/login/credentials', methods=['POST'])
def login_with_credentials():
    """
    Authentification avec username/password.
    
    Body JSON:
        {
            "username": "mon_pseudo_ou_email",
            "password": "mon_mot_de_passe",
            "remember": true  // optionnel, défaut: true
        }
        
    Returns:
        {
            "success": true/false,
            "status": "logged_in" | "login_failed" | ...,
            "method": "credentials",
            "user": { ... } | null,
            "error_message": "..." | null
        }
    """
    data = request.get_json() or {}
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    remember = data.get('remember', True)
    
    if not username or not password:
        return jsonify({
            "success": False,
            "error_message": "Username and password are required"
        }), 400
    
    logger.info(f"Login attempt for user: {username}")
    
    auth_service = get_auth_service()
    state = auth_service.login_with_credentials(username, password, remember=remember)
    
    result = _auth_state_to_dict(state)
    result["success"] = state.status == AuthStatus.LOGGED_IN
    
    status_code = 200 if result["success"] else 401
    return jsonify(result), status_code


@bp.route('/login/browser', methods=['POST'])
def login_with_browser():
    """
    Authentification avec les cookies du navigateur.
    
    Body JSON (optionnel):
        {
            "browser": "auto" | "firefox" | "chrome" | "edge",
            "remember": true  // optionnel, défaut: true
        }
        
    Returns:
        {
            "success": true/false,
            "status": "logged_in" | "login_failed" | ...,
            "method": "browser_cookies",
            "user": { ... } | null,
            "error_message": "..." | null
        }
    """
    data = request.get_json() or {}
    
    browser = data.get('browser', 'auto')
    remember = data.get('remember', True)
    
    logger.info(f"Login attempt with browser cookies: {browser}")
    
    auth_service = get_auth_service()
    state = auth_service.login_with_browser_cookies(browser=browser, remember=remember)
    
    result = _auth_state_to_dict(state)
    result["success"] = state.status == AuthStatus.LOGGED_IN
    
    status_code = 200 if result["success"] else 401
    return jsonify(result), status_code


@bp.route('/logout', methods=['POST'])
def logout():
    """
    Déconnexion de Geocaching.com.
    
    Returns:
        {
            "success": true,
            "status": "logged_out",
            "method": "none"
        }
    """
    logger.info("Logout request")
    
    auth_service = get_auth_service()
    state = auth_service.logout()
    
    result = _auth_state_to_dict(state)
    result["success"] = True
    
    return jsonify(result)


@bp.route('/config', methods=['GET'])
def get_auth_config():
    """
    Récupère la configuration d'authentification actuelle (sans les secrets).
    
    Returns:
        {
            "configured_method": "credentials" | "browser_cookies" | null,
            "has_saved_credentials": true/false
        }
    """
    auth_service = get_auth_service()
    method = auth_service.get_configured_method()
    
    return jsonify({
        "configured_method": method,
        "has_saved_credentials": method is not None
    })


@bp.route('/test', methods=['GET'])
def test_connection():
    """
    Teste la connexion à Geocaching.com.
    
    Effectue une requête test pour vérifier que l'authentification fonctionne.
    
    Returns:
        {
            "success": true/false,
            "status": "logged_in" | ...,
            "user": { ... } | null,
            "test_result": "ok" | "failed",
            "error_message": "..." | null
        }
    """
    auth_service = get_auth_service()
    
    # Force une vérification
    state = auth_service.get_auth_state(force_check=True)
    
    result = _auth_state_to_dict(state)
    result["success"] = state.status == AuthStatus.LOGGED_IN
    result["test_result"] = "ok" if result["success"] else "failed"
    
    return jsonify(result)


@bp.route('/profile', methods=['GET'])
def get_profile_stats():
    """
    Récupère les statistiques du profil utilisateur.
    
    Query params:
        force: Si "true", force le rafraîchissement même si les stats sont récentes
        
    Returns:
        {
            "success": true/false,
            "stats": {
                "finds_count": 123,
                "hides_count": 5,
                "favorite_points": 45,
                "awarded_favorite_points": 12,
                "stats_last_updated": "2025-01-27T08:00:00"
            } | null,
            "error_message": "..." | null
        }
    """
    force = request.args.get('force', '').lower() == 'true'
    
    auth_service = get_auth_service()
    
    if not auth_service.is_logged_in():
        return jsonify({
            "success": False,
            "stats": None,
            "error_message": "Not logged in"
        }), 401
    
    stats = auth_service.fetch_profile_stats(force=force)
    
    if stats:
        return jsonify({
            "success": True,
            "stats": stats,
            "error_message": None
        })
    else:
        return jsonify({
            "success": False,
            "stats": None,
            "error_message": "Failed to fetch profile stats"
        }), 500


@bp.route('/profile/refresh', methods=['POST'])
def refresh_profile_stats():
    """
    Force le rafraîchissement des statistiques du profil.
    
    Returns:
        {
            "success": true/false,
            "stats": { ... } | null,
            "error_message": "..." | null
        }
    """
    auth_service = get_auth_service()
    
    if not auth_service.is_logged_in():
        return jsonify({
            "success": False,
            "stats": None,
            "error_message": "Not logged in"
        }), 401
    
    logger.info("Forcing profile stats refresh")
    stats = auth_service.fetch_profile_stats(force=True)
    
    if stats:
        return jsonify({
            "success": True,
            "stats": stats,
            "error_message": None
        })
    else:
        return jsonify({
            "success": False,
            "stats": None,
            "error_message": "Failed to refresh profile stats"
        }), 500
