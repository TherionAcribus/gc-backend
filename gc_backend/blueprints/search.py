"""
Blueprint pour la recherche globale dans la base de données GeoApp.
Permet de chercher dans les géocaches (nom, description, hints, notes personnelles),
les logs et les notes utilisateur.
"""

from flask import Blueprint, jsonify, request
import logging
import re
from html import unescape

from ..database import db
from ..geocaches.models import Geocache, GeocacheLog, Note, GeocacheNote
from ..plugins.models import Plugin

bp = Blueprint('search', __name__)
logger = logging.getLogger(__name__)

CONTEXT_CHARS = 80  # Nombre de caractères de contexte autour du match


def _strip_html(html: str | None) -> str:
    """Convertit du HTML en texte brut."""
    if not html:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _build_regex(query: str, case_sensitive: bool = False, use_regex: bool = False, use_wildcard: bool = False):
    """Construit un pattern regex à partir de la query utilisateur."""
    flags = 0 if case_sensitive else re.IGNORECASE

    if use_regex:
        try:
            return re.compile(query, flags)
        except re.error:
            return None
    elif use_wildcard:
        escaped = re.escape(query)
        pattern = escaped.replace(r'\*', '.*').replace(r'\?', '.')
        return re.compile(pattern, flags)
    else:
        return re.compile(re.escape(query), flags)


def _extract_snippets(text: str, pattern, max_snippets: int = 3) -> list[dict]:
    """Extrait des snippets de contexte autour des matches."""
    if not text or not pattern:
        return []

    snippets = []
    for match in pattern.finditer(text):
        if len(snippets) >= max_snippets:
            break
        start = max(0, match.start() - CONTEXT_CHARS)
        end = min(len(text), match.end() + CONTEXT_CHARS)

        prefix = ('…' if start > 0 else '') + text[start:match.start()]
        matched = match.group()
        suffix = text[match.end():end] + ('…' if end < len(text) else '')

        snippets.append({
            'prefix': prefix,
            'match': matched,
            'suffix': suffix,
            'offset': match.start()
        })

    return snippets


def _count_matches(text: str, pattern) -> int:
    """Compte le nombre total de matches dans un texte."""
    if not text or not pattern:
        return 0
    return len(pattern.findall(text))


@bp.get('/api/search')
def global_search():
    """
    Recherche globale dans la base de données.

    Query params:
        q (str): Terme de recherche (obligatoire)
        case_sensitive (bool): Sensible à la casse (défaut: false)
        use_regex (bool): Mode regex (défaut: false)
        use_wildcard (bool): Mode wildcard (défaut: false)
        scope (str): Périmètre - 'all', 'geocaches', 'logs', 'notes', 'plugins', 'alphabets' (défaut: 'all')
        zone_id (int): Filtrer par zone (optionnel)
        limit (int): Nombre max de résultats par catégorie (défaut: 50)
    """
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Missing query parameter "q"'}), 400

    case_sensitive = request.args.get('case_sensitive', 'false').lower() == 'true'
    use_regex = request.args.get('use_regex', 'false').lower() == 'true'
    use_wildcard = request.args.get('use_wildcard', 'false').lower() == 'true'
    scope = request.args.get('scope', 'all')
    zone_id = request.args.get('zone_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    limit = min(limit, 200)

    pattern = _build_regex(query, case_sensitive, use_regex, use_wildcard)
    if pattern is None:
        return jsonify({'error': 'Invalid regex pattern'}), 400

    results = {
        'query': query,
        'options': {
            'case_sensitive': case_sensitive,
            'use_regex': use_regex,
            'use_wildcard': use_wildcard,
            'scope': scope
        },
        'geocaches': [],
        'logs': [],
        'notes': [],
        'plugins': [],
        'alphabets': [],
        'total_count': 0
    }

    try:
        # --- Recherche dans les géocaches ---
        if scope in ('all', 'database', 'geocaches'):
            gc_query = Geocache.query
            if zone_id is not None:
                gc_query = gc_query.filter(Geocache.zone_id == zone_id)

            geocaches = gc_query.all()
            gc_results = []

            for gc in geocaches:
                matches_in = {}

                # Chercher dans les champs texte
                fields = {
                    'name': gc.name,
                    'gc_code': gc.gc_code,
                    'owner': gc.owner,
                    'description': _strip_html(gc.description_html or gc.description_raw),
                    'description_override': _strip_html(gc.description_override_html or gc.description_override_raw),
                    'hints': gc.hints_decoded or gc.hints,
                    'hints_override': gc.hints_decoded_override,
                    'personal_note': gc.gc_personal_note,
                    'coordinates': gc.coordinates_raw,
                    'original_coordinates': gc.original_coordinates_raw,
                }

                total_gc_matches = 0
                for field_name, field_value in fields.items():
                    if not field_value:
                        continue
                    count = _count_matches(str(field_value), pattern)
                    if count > 0:
                        snippets = _extract_snippets(str(field_value), pattern)
                        matches_in[field_name] = {
                            'count': count,
                            'snippets': snippets
                        }
                        total_gc_matches += count

                if total_gc_matches > 0:
                    gc_results.append({
                        'id': gc.id,
                        'gc_code': gc.gc_code,
                        'name': gc.name,
                        'type': gc.type,
                        'zone_id': gc.zone_id,
                        'total_matches': total_gc_matches,
                        'matches_in': matches_in
                    })

            # Trier par nombre de matches décroissant
            gc_results.sort(key=lambda x: x['total_matches'], reverse=True)
            results['geocaches'] = gc_results[:limit]

        # --- Recherche dans les logs ---
        if scope in ('all', 'database', 'logs'):
            log_query = GeocacheLog.query.join(Geocache)
            if zone_id is not None:
                log_query = log_query.filter(Geocache.zone_id == zone_id)

            logs = log_query.all()
            log_results = []

            for log in logs:
                text = log.text or ''
                author = log.author or ''
                combined = f"{author} {text}"
                count = _count_matches(combined, pattern)

                if count > 0:
                    snippets = _extract_snippets(text, pattern)
                    log_results.append({
                        'id': log.id,
                        'geocache_id': log.geocache_id,
                        'geocache_gc_code': log.geocache.gc_code if log.geocache else None,
                        'geocache_name': log.geocache.name if log.geocache else None,
                        'author': log.author,
                        'log_type': log.log_type,
                        'date': log.date.isoformat() if log.date else None,
                        'total_matches': count,
                        'snippets': snippets
                    })

            log_results.sort(key=lambda x: x['total_matches'], reverse=True)
            results['logs'] = log_results[:limit]

        # --- Recherche dans les notes ---
        if scope in ('all', 'database', 'notes'):
            note_query = Note.query
            notes = note_query.all()
            note_results = []

            for note in notes:
                text = note.content or ''
                count = _count_matches(text, pattern)

                if count > 0:
                    snippets = _extract_snippets(text, pattern)
                    # Récupérer les géocaches liées
                    linked_geocaches = [
                        {'id': gc.id, 'gc_code': gc.gc_code, 'name': gc.name}
                        for gc in note.geocaches
                    ]
                    note_results.append({
                        'id': note.id,
                        'note_type': note.note_type,
                        'source': note.source,
                        'total_matches': count,
                        'snippets': snippets,
                        'linked_geocaches': linked_geocaches,
                        'updated_at': note.updated_at.isoformat() if note.updated_at else None,
                    })

            note_results.sort(key=lambda x: x['total_matches'], reverse=True)
            results['notes'] = note_results[:limit]

        # --- Recherche dans les plugins ---
        if scope in ('all', 'database', 'plugins'):
            plugin_results = []
            plugins = Plugin.query.all()

            for plugin in plugins:
                count = 0
                matched_fields = {}

                # Chercher dans le nom
                name_count = _count_matches(plugin.name, pattern)
                if name_count > 0:
                    matched_fields['name'] = {
                        'count': name_count,
                        'snippets': _extract_snippets(plugin.name, pattern)
                    }
                    count += name_count

                # Chercher dans la description
                if plugin.description:
                    desc_count = _count_matches(plugin.description, pattern)
                    if desc_count > 0:
                        matched_fields['description'] = {
                            'count': desc_count,
                            'snippets': _extract_snippets(plugin.description, pattern)
                        }
                        count += desc_count

                # Chercher dans l'auteur
                if plugin.author:
                    author_count = _count_matches(plugin.author, pattern)
                    if author_count > 0:
                        matched_fields['author'] = {
                            'count': author_count,
                            'snippets': _extract_snippets(plugin.author, pattern)
                        }
                        count += author_count

                # Chercher dans les catégories
                if plugin.categories:
                    categories_text = ' '.join(plugin.categories)
                    cat_count = _count_matches(categories_text, pattern)
                    if cat_count > 0:
                        matched_fields['categories'] = {
                            'count': cat_count,
                            'snippets': _extract_snippets(categories_text, pattern)
                        }
                        count += cat_count

                if count > 0:
                    plugin_results.append({
                        'id': plugin.id,
                        'name': plugin.name,
                        'version': plugin.version,
                        'description': plugin.description,
                        'author': plugin.author,
                        'categories': plugin.categories or [],
                        'source': plugin.source,
                        'enabled': plugin.enabled,
                        'total_matches': count,
                        'matches_in': matched_fields
                    })

            plugin_results.sort(key=lambda x: x['total_matches'], reverse=True)
            results['plugins'] = plugin_results[:limit]

        # --- Recherche dans les alphabets ---
        if scope in ('all', 'alphabets'):
            import os
            import json
            
            # Chemin: gc_backend/blueprints/search.py -> gc_backend/blueprints/ -> gc_backend/ -> gc-backend/ -> alphabets/
            alphabets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'alphabets')
            alphabet_results = []
            
            logger.debug(f"Searching alphabets in: {alphabets_dir}, exists: {os.path.exists(alphabets_dir)}")

            if os.path.exists(alphabets_dir):
                alphabet_folders = os.listdir(alphabets_dir)
                logger.debug(f"Found {len(alphabet_folders)} items in alphabets directory: {alphabet_folders[:5]}")
                
                for alphabet_name in alphabet_folders:
                    alphabet_path = os.path.join(alphabets_dir, alphabet_name)
                    if not os.path.isdir(alphabet_path):
                        logger.debug(f"Skipping {alphabet_name} (not a directory)")
                        continue

                    alphabet_json_path = os.path.join(alphabet_path, 'alphabet.json')
                    if not os.path.exists(alphabet_json_path):
                        logger.debug(f"Skipping {alphabet_name} (no alphabet.json)")
                        continue

                    try:
                        logger.debug(f"Reading alphabet.json for {alphabet_name}")
                        with open(alphabet_json_path, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        
                        logger.debug(f"Alphabet {alphabet_name}: name={metadata.get('name')}, description={metadata.get('description', '')[:50]}")

                        count = 0
                        matched_fields = {}

                        # Chercher dans le nom
                        name = metadata.get('name', alphabet_name)
                        name_count = _count_matches(name, pattern)
                        if name_count > 0:
                            matched_fields['name'] = {
                                'count': name_count,
                                'snippets': _extract_snippets(name, pattern)
                            }
                            count += name_count

                        # Chercher dans la description
                        description = metadata.get('description', '')
                        if description:
                            desc_count = _count_matches(description, pattern)
                            if desc_count > 0:
                                matched_fields['description'] = {
                                    'count': desc_count,
                                    'snippets': _extract_snippets(description, pattern)
                                }
                                count += desc_count

                        # Chercher dans les alias
                        aliases = metadata.get('aliases', [])
                        if aliases:
                            aliases_text = ' '.join(aliases)
                            alias_count = _count_matches(aliases_text, pattern)
                            if alias_count > 0:
                                matched_fields['aliases'] = {
                                    'count': alias_count,
                                    'snippets': _extract_snippets(aliases_text, pattern)
                                }
                                count += alias_count

                        if count > 0:
                            logger.debug(f"Alphabet {alphabet_name} matched with {count} occurrences")
                            alphabet_results.append({
                                'id': alphabet_name,
                                'name': name,
                                'description': description,
                                'aliases': aliases,
                                'total_matches': count,
                                'matches_in': matched_fields
                            })
                        else:
                            logger.debug(f"Alphabet {alphabet_name} did not match pattern")

                    except (json.JSONDecodeError, IOError) as e:
                        logger.warning(f"Error reading alphabet {alphabet_name}: {e}")
                        continue

            alphabet_results.sort(key=lambda x: x['total_matches'], reverse=True)
            results['alphabets'] = alphabet_results[:limit]
            logger.debug(f"Total alphabets found: {len(alphabet_results)}")

        results['total_count'] = (
            len(results['geocaches']) +
            len(results['logs']) +
            len(results['notes']) +
            len(results['plugins']) +
            len(results['alphabets'])
        )

        return jsonify(results)

    except Exception as e:
        logger.error(f"Global search error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
