"""
Blueprint pour la gestion des alphabets personnalisés.
Réimplémentation pour Theia - API REST uniquement.
"""
import os
import json
from flask import Blueprint, jsonify, send_file, request, current_app

alphabets_bp = Blueprint('alphabets', __name__)

# Fallback si accédé hors contexte Flask (ne devrait pas arriver en pratique)
_DEFAULT_ALPHABETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'alphabets')


def _get_alphabets_dir():
    """Retourne le chemin vers le répertoire des alphabets depuis la config Flask."""
    try:
        return current_app.config.get('ALPHABETS_DIR') or _DEFAULT_ALPHABETS_DIR
    except RuntimeError:
        return _DEFAULT_ALPHABETS_DIR


def load_alphabet_config(alphabet_id):
    """Charge la configuration d'un alphabet depuis son dossier."""
    alphabet_path = os.path.join(_get_alphabets_dir(), alphabet_id, 'alphabet.json')
    if not os.path.exists(alphabet_path):
        return None
        
    with open(alphabet_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        # Ajouter l'ID de l'alphabet (nom du dossier)
        config['id'] = alphabet_id
        return config


def load_alphabet_readme(alphabet_id):
    """Charge le contenu du README d'un alphabet s'il existe."""
    alphabet_dir = os.path.join(_get_alphabets_dir(), alphabet_id)
    possible_names = ["README.md", "Readme.md", "readme.md"]
    
    for name in possible_names:
        readme_path = os.path.join(alphabet_dir, name)
        if os.path.isfile(readme_path):
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except:
                continue
    return ""


def search_alphabets(query, alphabets, search_in_name=True, search_in_tags=True, search_in_readme=True):
    """
    Recherche dans les alphabets selon une requête et les préférences de recherche.
    Recherche dans : nom, tags, et contenu README selon les préférences.
    """
    if not query or query.strip() == "":
        return alphabets
    
    query = query.lower().strip()
    results = []
    
    for alphabet in alphabets:
        score = 0
        matches = []
        
        # Recherche dans le nom (si activé)
        if search_in_name:
            name = alphabet.get('name', '').lower()
            if query in name:
                score += 10
                matches.append(f"nom: {alphabet.get('name', '')}")
        
        # Recherche dans la description (si activé - même checkbox que nom)
        if search_in_name:
            description = alphabet.get('description', '').lower()
            if query in description:
                score += 5
                matches.append(f"description: {alphabet.get('description', '')}")
        
        # Recherche dans les tags (si activé)
        if search_in_tags:
            tags = alphabet.get('tags', [])
            if isinstance(tags, list):
                for tag in tags:
                    if query in tag.lower():
                        score += 8
                        matches.append(f"tag: {tag}")
        
        # Recherche dans le README (si activé)
        if search_in_readme:
            readme_content = load_alphabet_readme(alphabet.get('id', ''))
            if readme_content and query in readme_content.lower():
                score += 3
                matches.append("description longue (README)")
        
        # Recherche partielle (mots séparés) - seulement si au moins une option est activée
        if search_in_name or search_in_tags or search_in_readme:
            query_words = query.split()
            for word in query_words:
                if len(word) >= 3:  # Éviter les mots trop courts
                    if search_in_name:
                        # Recherche partielle dans le nom
                        name = alphabet.get('name', '').lower()
                        if word in name:
                            score += 2
                        # Recherche partielle dans la description
                        description = alphabet.get('description', '').lower()
                        if word in description:
                            score += 1
                    
                    if search_in_tags:
                        # Recherche partielle dans les tags
                        tags = alphabet.get('tags', [])
                        if isinstance(tags, list):
                            for tag in tags:
                                if word in tag.lower():
                                    score += 1
        
        if score > 0:
            alphabet['search_score'] = score
            alphabet['search_matches'] = matches
            results.append(alphabet)
    
    # Trier par score décroissant
    results.sort(key=lambda x: x.get('search_score', 0), reverse=True)
    return results


def get_all_alphabets():
    """Récupère tous les alphabets disponibles."""
    alphabets = []
    
    if os.path.exists(_get_alphabets_dir()):
        for dirname in os.listdir(_get_alphabets_dir()):
            alphabet_dir = os.path.join(_get_alphabets_dir(), dirname)
            if os.path.isdir(alphabet_dir):
                config = load_alphabet_config(dirname)
                if config:
                    # Ajouter source (official/custom) basé sur la présence d'un fichier marker ou convention
                    # Pour l'instant, tous sont considérés comme "official"
                    config['source'] = 'official'
                    alphabets.append(config)
    
    return alphabets


# =============================================================================
# Routes API REST
# =============================================================================

@alphabets_bp.route('/api/alphabets', methods=['GET'])
def get_alphabets():
    """
    Récupère la liste de tous les alphabets disponibles au format JSON.
    Supporte la recherche avec les paramètres:
    - search: terme de recherche
    - search_in_name: true/false (défaut: true)
    - search_in_tags: true/false (défaut: true)
    - search_in_readme: true/false (défaut: false)
    """
    alphabets = get_all_alphabets()
    
    # Gérer la recherche
    search_query = request.args.get('search', '').strip()
    search_in_name = request.args.get('search_in_name', 'true').lower() == 'true'
    search_in_tags = request.args.get('search_in_tags', 'true').lower() == 'true'
    search_in_readme = request.args.get('search_in_readme', 'false').lower() == 'true'
    
    if search_query:
        alphabets = search_alphabets(search_query, alphabets, search_in_name, search_in_tags, search_in_readme)
    
    return jsonify(alphabets)


@alphabets_bp.route('/api/alphabets/<alphabet_id>', methods=['GET'])
def get_alphabet(alphabet_id):
    """Récupère la configuration complète d'un alphabet spécifique."""
    alphabet_dir = os.path.join(_get_alphabets_dir(), alphabet_id)
    
    if not os.path.exists(alphabet_dir):
        return jsonify({"error": f"Alphabet {alphabet_id} non trouvé"}), 404
        
    config = load_alphabet_config(alphabet_id)
    if not config:
        return jsonify({"error": "Configuration de l'alphabet invalide"}), 500
        
    return jsonify(config)


@alphabets_bp.route('/api/alphabets/<alphabet_id>/resource/<path:resource_path>')
def get_alphabet_resource(alphabet_id, resource_path):
    """
    Récupère une ressource (image ou police) d'un alphabet.
    Utilisé pour les images individuelles des symboles.
    """
    resource_full_path = os.path.join(_get_alphabets_dir(), alphabet_id, resource_path)
    
    current_app.logger.info(f"Requested resource: {resource_full_path}")
    
    if not os.path.exists(resource_full_path) or not os.path.isfile(resource_full_path):
        current_app.logger.error(f"Resource not found: {resource_full_path}")
        return jsonify({"error": f"Resource {resource_path} not found"}), 404
        
    return send_file(resource_full_path)


@alphabets_bp.route('/api/alphabets/<alphabet_id>/font')
def get_alphabet_font(alphabet_id):
    """
    Récupère la police TTF d'un alphabet basé sur police.
    Retourne le fichier binaire de la police.
    """
    config = load_alphabet_config(alphabet_id)
    if not config:
        current_app.logger.error(f"Alphabet not found: {alphabet_id}")
        return jsonify({"error": f"Alphabet {alphabet_id} non trouvé"}), 404
        
    if config['alphabetConfig']['type'] != 'font':
        current_app.logger.error(f"Not a font-based alphabet: {alphabet_id}")
        return jsonify({"error": "Not a font-based alphabet"}), 404
    
    font_path = os.path.join(_get_alphabets_dir(), alphabet_id, config['alphabetConfig']['fontFile'])
    
    current_app.logger.info(f"Loading font: {font_path}")
    
    if not os.path.exists(font_path):
        current_app.logger.error(f"Font file not found: {font_path}")
        return jsonify({"error": f"Police {config['alphabetConfig']['fontFile']} non trouvée"}), 404
    
    return send_file(font_path, mimetype='font/ttf')


@alphabets_bp.route('/api/alphabets/<alphabet_id>/sources', methods=['GET'])
def get_alphabet_sources(alphabet_id):
    """Récupère les sources et crédits d'un alphabet."""
    config = load_alphabet_config(alphabet_id)
    if not config:
        return jsonify({"error": f"Alphabet {alphabet_id} non trouvé"}), 404
        
    sources = config.get('sources', [])
    return jsonify({
        "alphabet_id": alphabet_id,
        "alphabet_name": config.get('name', alphabet_id),
        "sources": sources
    })


@alphabets_bp.route('/api/alphabets/<alphabet_id>/readme', methods=['GET'])
def get_alphabet_readme(alphabet_id):
    """Récupère le contenu du README d'un alphabet."""
    alphabet_dir = os.path.join(_get_alphabets_dir(), alphabet_id)
    
    if not os.path.exists(alphabet_dir):
        return jsonify({"error": f"Alphabet {alphabet_id} not found"}), 404
    
    readme_content = load_alphabet_readme(alphabet_id)
    
    return jsonify({
        "alphabet_id": alphabet_id,
        "readme": readme_content
    })


@alphabets_bp.route('/api/alphabets/discover', methods=['POST'])
def discover_alphabets():
    """
    Force la redécouverte des alphabets (scan du répertoire).
    Retourne la liste mise à jour des alphabets.
    """
    alphabets = get_all_alphabets()
    return jsonify({
        "status": "success",
        "count": len(alphabets),
        "alphabets": alphabets
    })




