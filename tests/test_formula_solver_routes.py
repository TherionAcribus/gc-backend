"""
Tests d'intégration pour les routes Formula Solver
"""

import pytest
import json
from gc_backend import create_app
from gc_backend.database import db
from gc_backend.geocaches.models import Geocache


@pytest.fixture
def app():
    """Crée une instance de l'application pour les tests"""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Client de test Flask"""
    return app.test_client()


@pytest.fixture
def sample_geocache(app):
    """Crée une géocache de test dans la DB"""
    with app.app_context():
        geocache = Geocache(
            gc_code='GC12345',
            name='Test Mystery',
            type='Mystery',
            description="""
                <h1>Énigme Test</h1>
                <p>Pour trouver les coordonnées finales:</p>
                <ul>
                    <li>A. Nombre de fenêtres sur la façade</li>
                    <li>B. Année de construction - 1900</li>
                </ul>
                <p>Les coordonnées sont: N 47° 5E.AB E 006° 5C.DE</p>
            """,
            latitude=47.123,
            longitude=6.456,
            difficulty=3.0,
            terrain=2.5,
            owner='TestOwner',
            size='Regular'
        )
        db.session.add(geocache)
        db.session.commit()
        
        geocache_id = geocache.id
        
    return geocache_id


class TestDetectFormulasRoute:
    """Tests de la route /api/formula-solver/detect-formulas"""
    
    def test_detect_with_text(self, client):
        """Test : Détection de formules depuis texte brut"""
        response = client.post(
            '/api/formula-solver/detect-formulas',
            json={'text': 'Les coordonnées sont N 47° 5E.FTN E 006° 5A.JVF'}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        assert len(data['formulas']) >= 1
        assert 'N 47° 5E.FTN' in data['formulas'][0]['north']
        assert 'E 006° 5A.JVF' in data['formulas'][0]['east']
    
    def test_detect_with_geocache_id(self, client, sample_geocache):
        """Test : Détection de formules depuis geocache_id"""
        response = client.post(
            '/api/formula-solver/detect-formulas',
            json={'geocache_id': sample_geocache}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        # Devrait détecter N 47° 5E.AB E 006° 5C.DE
        assert len(data['formulas']) >= 1
    
    def test_detect_missing_params(self, client):
        """Test : Erreur 400 si aucun paramètre"""
        response = client.post(
            '/api/formula-solver/detect-formulas',
            json={}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
    
    def test_detect_geocache_not_found(self, client):
        """Test : Erreur 404 si geocache inexistante"""
        response = client.post(
            '/api/formula-solver/detect-formulas',
            json={'geocache_id': 99999}
        )
        
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data['status'] == 'error'


class TestExtractQuestionsRoute:
    """Tests de la route /api/formula-solver/extract-questions"""
    
    def test_extract_with_text(self, client):
        """Test : Extraction de questions depuis texte"""
        text = """
        Pour résoudre:
        A. Combien de fenêtres?
        B. Année de construction?
        C. Numéro de la rue?
        """
        
        response = client.post(
            '/api/formula-solver/extract-questions',
            json={
                'text': text,
                'letters': ['A', 'B', 'C'],
                'method': 'regex'
            }
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        assert 'fenêtres' in data['questions']['A'].lower()
        assert 'année' in data['questions']['B'].lower()
        assert data['found_count'] >= 2
    
    def test_extract_with_geocache_id(self, client, sample_geocache):
        """Test : Extraction depuis geocache_id"""
        response = client.post(
            '/api/formula-solver/extract-questions',
            json={
                'geocache_id': sample_geocache,
                'letters': ['A', 'B'],
                'method': 'regex'
            }
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        assert data['method'] == 'regex'
        # Devrait trouver au moins A et B depuis la description
        assert data['found_count'] >= 1
    
    def test_extract_missing_letters(self, client):
        """Test : Erreur 400 si letters manquant"""
        response = client.post(
            '/api/formula-solver/extract-questions',
            json={
                'text': 'Test',
                'method': 'regex'
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
    
    def test_extract_invalid_method(self, client):
        """Test : Erreur 400 si method invalide"""
        response = client.post(
            '/api/formula-solver/extract-questions',
            json={
                'text': 'Test',
                'letters': ['A'],
                'method': 'invalid'
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
    
    def test_extract_ai_not_implemented(self, client):
        """Test : Erreur 400 si method=ai (non implémenté)"""
        response = client.post(
            '/api/formula-solver/extract-questions',
            json={
                'text': 'Test',
                'letters': ['A'],
                'method': 'ai'
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert 'AI' in data['error'] or 'ai' in data['error']


class TestCalculateRoute:
    """Tests de la route /api/formula-solver/calculate"""
    
    def test_calculate_simple(self, client):
        """Test : Calcul simple sans opérations"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° 5E.AB',
                'east_formula': 'E 006° 5C.DE',
                'values': {
                    'A': 3,
                    'B': 5,
                    'C': 1,
                    'D': 2,
                    'E': 8
                }
            }
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        assert 'coordinates' in data
        assert 'latitude' in data['coordinates']
        assert 'longitude' in data['coordinates']
        assert 'ddm' in data['coordinates']
        assert 'dms' in data['coordinates']
        assert 'calculation_steps' in data
    
    def test_calculate_with_operations(self, client):
        """Test : Calcul avec opérations arithmétiques"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° (5+3).00',
                'east_formula': 'E 006° (10-2).50',
                'values': {}
            }
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        # 5+3 = 8, donc N 47° 08.00
        # 10-2 = 8, donc E 006° 08.50
    
    def test_calculate_with_distance(self, client):
        """Test : Calcul avec distance depuis origine"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° 50.000',
                'east_formula': 'E 006° 10.000',
                'values': {},
                'origin_lat': 47.0,
                'origin_lon': 6.0
            }
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'success'
        assert 'distance' in data
        assert 'km' in data['distance']
        assert 'miles' in data['distance']
        assert data['distance']['km'] > 0
    
    def test_calculate_missing_formula(self, client):
        """Test : Erreur 400 si formule manquante"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° 50.000',
                'values': {}
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
    
    def test_calculate_missing_values(self, client):
        """Test : Erreur 400 si valeurs manquantes pour variables"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° 5A.BC',
                'east_formula': 'E 006° 5D.EF',
                'values': {
                    'A': 1
                    # B, C, D, E, F manquants !
                }
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert 'manquantes' in data['error'].lower() or 'missing' in data['error'].lower()


class TestCoordinateCalculatorSecurity:
    """Tests de sécurité pour le calculateur de coordonnées"""
    
    def test_no_code_injection(self, client):
        """Test : Pas d'injection de code possible"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° __import__("os").system("ls").00',
                'east_formula': 'E 006° 10.00',
                'values': {}
            }
        )
        
        # Ne doit pas planter mais retourner une erreur
        assert response.status_code in [400, 500]
        data = json.loads(response.data)
        assert data['status'] == 'error'
    
    def test_no_builtin_access(self, client):
        """Test : Pas d'accès aux builtins"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° eval("1+1").00',
                'east_formula': 'E 006° 10.00',
                'values': {}
            }
        )
        
        # Ne doit pas exécuter eval
        assert response.status_code in [400, 500]
    
    def test_division_by_zero(self, client):
        """Test : Division par zéro gérée correctement"""
        response = client.post(
            '/api/formula-solver/calculate',
            json={
                'north_formula': 'N 47° (10/0).00',
                'east_formula': 'E 006° 10.00',
                'values': {}
            }
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert 'division' in data['error'].lower() or 'zero' in data['error'].lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
