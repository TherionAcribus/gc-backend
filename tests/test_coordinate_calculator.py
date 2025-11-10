"""
Tests unitaires pour CoordinateCalculator
"""

import pytest
from gc_backend.utils.coordinate_calculator import CoordinateCalculator


class TestCoordinateCalculator:
    """Tests du calculateur de coordonnées"""
    
    def setup_method(self):
        """Initialise le calculateur avant chaque test"""
        self.calc = CoordinateCalculator()
    
    def test_substitute_variables_simple(self):
        """Test : Substitution simple de variables"""
        formula = "N 47° 5E.AB"
        values = {'A': 3, 'B': 5, 'E': 8}
        
        result = self.calc.substitute_variables(formula, values)
        
        # Devrait donner "N 47° 58.35"
        assert '58.35' in result
    
    def test_substitute_missing_variable(self):
        """Test : Erreur si variable manquante"""
        formula = "N 47° 5A.BC"
        values = {'A': 1}  # B et C manquants
        
        with pytest.raises(ValueError, match="manquantes"):
            self.calc.substitute_variables(formula, values)
    
    def test_evaluate_simple_expression(self):
        """Test : Évaluation d'expression simple"""
        result = self.calc._safe_eval("3+5")
        assert result == 8
        
        result = self.calc._safe_eval("10-3")
        assert result == 7
        
        result = self.calc._safe_eval("4*2")
        assert result == 8
        
        result = self.calc._safe_eval("15/3")
        assert result == 5.0
    
    def test_evaluate_complex_expression(self):
        """Test : Évaluation d'expression complexe"""
        result = self.calc._safe_eval("(3+2)*4")
        assert result == 20
        
        result = self.calc._safe_eval("10 + 5 * 2")
        assert result == 20
    
    def test_evaluate_division_by_zero(self):
        """Test : Division par zéro gérée"""
        with pytest.raises(ValueError, match="Division par zéro"):
            self.calc._safe_eval("10/0")
    
    def test_evaluate_dangerous_expression(self):
        """Test : Expression dangereuse rejetée"""
        # Test injection __import__
        with pytest.raises(ValueError):
            self.calc._safe_eval("__import__('os').system('ls')")
        
        # Test eval
        with pytest.raises(ValueError):
            self.calc._safe_eval("eval('1+1')")
        
        # Test caractères invalides
        with pytest.raises(ValueError):
            self.calc._safe_eval("import os")
    
    def test_parse_coordinate_ddm(self):
        """Test : Parsing coordonnée DDM"""
        lat = self.calc._parse_coordinate("N 47° 53.900", 'N')
        assert abs(lat - 47.898333) < 0.00001
        
        lon = self.calc._parse_coordinate("E 006° 05.000", 'E')
        assert abs(lon - 6.083333) < 0.00001
    
    def test_parse_coordinate_negative(self):
        """Test : Coordonnées négatives (Sud/Ouest)"""
        lat = self.calc._parse_coordinate("S 47° 53.900", 'S')
        assert lat < 0
        
        lon = self.calc._parse_coordinate("W 006° 05.000", 'W')
        assert lon < 0
    
    def test_parse_coordinate_invalid(self):
        """Test : Format invalide rejeté"""
        with pytest.raises(ValueError, match="invalide"):
            self.calc._parse_coordinate("Invalid", 'N')
    
    def test_format_ddm(self):
        """Test : Formatage en DDM"""
        result = self.calc._format_ddm(47.898333, 6.083333)
        
        assert 'N 47°' in result
        assert 'E 006°' in result
        assert '53.900' in result
        assert '05.000' in result
    
    def test_format_dms(self):
        """Test : Formatage en DMS"""
        result = self.calc._format_dms(47.898333, 6.083333)
        
        assert 'N 47°' in result
        assert 'E 006°' in result
        assert '"' in result  # Symbole des secondes
    
    def test_calculate_distance(self):
        """Test : Calcul de distance Haversine"""
        # Distance Paris (48.8566, 2.3522) -> Lyon (45.7640, 4.8357)
        # Environ 392 km
        distance = self.calc.calculate_distance(48.8566, 2.3522, 45.7640, 4.8357)
        
        assert 390 < distance < 395  # Tolérance
    
    def test_calculate_coordinates_full(self):
        """Test : Calcul complet de coordonnées"""
        result = self.calc.calculate_coordinates(
            north_formula="N 47° 5E.AB",
            east_formula="E 006° 5C.DE",
            values={'A': 3, 'B': 5, 'C': 1, 'D': 2, 'E': 8}
        )
        
        assert result['status'] == 'success'
        assert 'coordinates' in result
        assert 'latitude' in result['coordinates']
        assert 'longitude' in result['coordinates']
        assert 'ddm' in result['coordinates']
        assert 'dms' in result['coordinates']
        assert 'decimal' in result['coordinates']
        assert 'calculation_steps' in result
    
    def test_calculate_with_parentheses(self):
        """Test : Calcul avec expressions entre parenthèses"""
        result = self.calc.calculate_coordinates(
            north_formula="N 47° (5+3).00",
            east_formula="E 006° (10-2).50",
            values={}
        )
        
        assert result['status'] == 'success'
        # (5+3) = 8, donc N 47° 08.00
        # (10-2) = 8, donc E 006° 08.50
        assert '47° 08.000' in result['coordinates']['ddm']
        assert '006° 08.500' in result['coordinates']['ddm']
    
    def test_calculate_error_missing_values(self):
        """Test : Erreur si valeurs manquantes"""
        result = self.calc.calculate_coordinates(
            north_formula="N 47° 5A.BC",
            east_formula="E 006° 5D.EF",
            values={'A': 1}  # B, C, D, E, F manquants
        )
        
        assert result['status'] == 'error'
        assert 'error' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
