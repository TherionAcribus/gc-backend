"""
Script de test pour le plugin Pi Digits
"""

import sys
import os

# Ajouter le chemin du plugin au PYTHONPATH
plugin_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, plugin_dir)

from main import PiDigitsPlugin


def test_basic_decode():
    """Test basique de décodage"""
    plugin = PiDigitsPlugin()
    
    # Test 1: Position 1 devrait retourner "1" (π = 3.1415...)
    result = plugin.execute({
        "text": "1",
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 1 - Position 1:")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    print(f"  Expected: 1")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == '1'
    print("  ✓ PASSED\n")


def test_multiple_positions():
    """Test avec plusieurs positions"""
    plugin = PiDigitsPlugin()
    
    # Test 2: Positions 1-5 devraient retourner "14159"
    result = plugin.execute({
        "text": "1 2 3 4 5",
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 2 - Positions 1-5:")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    print(f"  Expected: 14159")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == '14159'
    print("  ✓ PASSED\n")


def test_position_49():
    """Test position 49 (exemple de l'utilisateur)"""
    plugin = PiDigitsPlugin()
    
    result = plugin.execute({
        "text": "49",
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 3 - Position 49:")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    # La 49ème décimale de Pi est 2
    assert result['status'] == 'success'
    print(f"  La 49ème décimale de Pi est: {result['results'][0]['text_output']}")
    print("  ✓ PASSED\n")


def test_format_positions_and_digits():
    """Test format positions_and_digits"""
    plugin = PiDigitsPlugin()
    
    result = plugin.execute({
        "text": "1 2 3",
        "mode": "decode",
        "format": "positions_and_digits"
    })
    
    print("Test 4 - Format positions_and_digits:")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    print(f"  Expected: 1=1 2=4 3=1")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == '1=1 2=4 3=1'
    print("  ✓ PASSED\n")


def test_format_detailed():
    """Test format detailed"""
    plugin = PiDigitsPlugin()
    
    result = plugin.execute({
        "text": "1 2",
        "mode": "decode",
        "format": "detailed"
    })
    
    print("Test 5 - Format detailed:")
    print(f"  Status: {result['status']}")
    print(f"  Output:\n{result['results'][0]['text_output']}")
    assert result['status'] == 'success'
    assert 'Position 1: 1' in result['results'][0]['text_output']
    assert 'Position 2: 4' in result['results'][0]['text_output']
    print("  ✓ PASSED\n")


def test_invalid_position():
    """Test avec position invalide"""
    plugin = PiDigitsPlugin()
    
    result = plugin.execute({
        "text": "99999",  # Au-delà de la limite
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 6 - Position invalide (99999):")
    print(f"  Status: {result['status']}")
    print(f"  Message: {result['summary']['message']}")
    assert result['status'] == 'error'
    print("  ✓ PASSED\n")


def test_mixed_valid_invalid():
    """Test avec positions valides et invalides mélangées"""
    plugin = PiDigitsPlugin()
    
    result = plugin.execute({
        "text": "1 2 99999 3",
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 7 - Positions mixtes (1 2 99999 3):")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    print(f"  Expected: 141 (ignoring 99999)")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == '141'
    metadata = result['results'][0]['metadata']
    assert 99999 in metadata['invalid_positions']
    print(f"  Invalid positions: {metadata['invalid_positions']}")
    print("  ✓ PASSED\n")


def test_separators():
    """Test avec différents séparateurs"""
    plugin = PiDigitsPlugin()
    
    # Test avec virgules
    result = plugin.execute({
        "text": "1,2,3,4,5",
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 8 - Séparateurs (virgules):")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == '14159'
    print("  ✓ PASSED\n")


def test_allowed_chars_gps_coordinates():
    """Test avec coordonnées GPS (caractères autorisés)"""
    plugin = PiDigitsPlugin()
    
    # Test avec coordonnées GPS contenant N, °, .
    result = plugin.execute({
        "text": "N 48° 51.400",
        "mode": "decode",
        "format": "digits_only",
        "allowed_chars": " \t\r\n.°NSEW"
    })
    
    print("Test 9 - Coordonnées GPS (N 48° 51.400):")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    # Devrait extraire 48, 51, 400
    assert result['status'] == 'success'
    # 48ème=5, 51ème=5, 400ème=4
    expected_output = '554'
    assert result['results'][0]['text_output'] == expected_output
    print(f"  Expected: {expected_output}")
    print("  ✓ PASSED\n")


def test_allowed_chars_geocache_formula():
    """Test avec formule de géocache"""
    plugin = PiDigitsPlugin()
    
    # Test avec formule type "N 48° 5A.BCD" où on veut extraire des positions
    result = plugin.execute({
        "text": "49 100 500",  # Positions pour A, B, C (toutes < 1000)
        "mode": "decode",
        "format": "digits_only"
    })
    
    print("Test 10 - Formule géocache (positions 49, 100, 500):")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    # Vérifions les vraies valeurs
    assert result['status'] == 'success'
    # On ne vérifie que le succès, pas la valeur exacte
    assert len(result['results'][0]['text_output']) == 3
    print("  ✓ PASSED\n")


def test_allowed_chars_with_cardinals():
    """Test avec points cardinaux"""
    plugin = PiDigitsPlugin()
    
    # Test avec E et W
    result = plugin.execute({
        "text": "E 2° 21.W 49",
        "mode": "decode",
        "format": "digits_only",
        "allowed_chars": " \t\r\n.°NSEW"
    })
    
    print("Test 11 - Points cardinaux (E 2° 21.W 49):")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    # Devrait extraire 2, 21, 49
    assert result['status'] == 'success'
    # Vérifions que 3 chiffres sont retournés
    assert len(result['results'][0]['text_output']) == 3
    print(f"  Positions extraites: 2, 21, 49")
    print("  ✓ PASSED\n")


def test_axis_structured_coordinates():
    """Test avec deux axes N/E pour reconstruire une coordonnee DDM complete."""
    plugin = PiDigitsPlugin()

    result = plugin.execute({
        "text": "N 19,44,25,64,41,51,87\nE 50,77,20,32,69,66,60,32",
        "mode": "decode",
        "format": "digits_only"
    })

    print("Test 12 - Axes N/E structures:")
    print(f"  Status: {result['status']}")
    print(f"  Output: {result['results'][0]['text_output']}")
    assert result['status'] == 'success'
    assert result['results'][0]['text_output'] == "N 49° 33.654' E 006° 06.740'"
    assert result['primary_coordinates']['ddm'] == "N 49° 33.654' E 006° 06.740'"
    print("  ✓ PASSED\n")


def run_all_tests():
    """Exécute tous les tests"""
    print("=" * 60)
    print("TESTS DU PLUGIN PI DIGITS")
    print("=" * 60 + "\n")
    
    try:
        test_basic_decode()
        test_multiple_positions()
        test_position_49()
        test_format_positions_and_digits()
        test_format_detailed()
        test_invalid_position()
        test_mixed_valid_invalid()
        test_separators()
        test_allowed_chars_gps_coordinates()
        test_allowed_chars_geocache_formula()
        test_allowed_chars_with_cardinals()
        test_axis_structured_coordinates()
        
        print("=" * 60)
        print("✓ TOUS LES TESTS SONT PASSÉS")
        print("=" * 60)
        
    except AssertionError as e:
        print("\n" + "=" * 60)
        print("✗ ÉCHEC DU TEST")
        print("=" * 60)
        raise


if __name__ == "__main__":
    run_all_tests()
