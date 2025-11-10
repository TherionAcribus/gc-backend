"""Debug du nettoyage HTML"""
from gc_backend.services.formula_questions_service import formula_questions_service

class MockGeocache:
    """Mock d'une géocache pour les tests"""
    
    def __init__(self, description="", waypoints=None, hint=""):
        self.description = description
        self.additional_waypoints = waypoints or []
        self.hint = hint


class MockWaypoint:
    """Mock d'un waypoint pour les tests"""
    
    def __init__(self, prefix="", name="", note=""):
        self.prefix = prefix
        self.name = name
        self.note = note


# Créer la géocache de test
geocache = MockGeocache(
    description="""
        <html>
        <body>
        <h1>Énigme du Monument</h1>
        <p>Pour trouver les coordonnées:</p>
        <ul>
            <li>A. Nombre de colonnes</li>
            <li>B. Année d'inauguration</li>
        </ul>
        </body>
        </html>
    """,
    waypoints=[
        MockWaypoint(
            prefix="WP1",
            name="Première étape",
            note="C. Hauteur de la statue en mètres"
        )
    ],
    hint="D. Le code est écrit sur la plaque"
)

# Préparer le contenu
content = formula_questions_service._prepare_content_for_analysis(geocache)

print("=" * 80)
print("CONTENU PRÉPARÉ :")
print("=" * 80)
print(content)
print("=" * 80)

# Tester l'extraction
result = formula_questions_service.extract_questions_with_regex(geocache, ['A', 'B', 'C', 'D'])

print("\nRÉSULTATS :")
for letter, question in result.items():
    print(f"{letter}: {question}")
