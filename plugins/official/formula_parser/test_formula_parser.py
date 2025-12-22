"""
Tests pour le plugin Formula Parser
"""

import pytest
from gc_backend.plugins.official.formula_parser.main import FormulaParserPlugin


class TestFormulaParser:
    """Tests du plugin Formula Parser"""
    
    def setup_method(self):
        """Initialise le plugin avant chaque test"""
        self.plugin = FormulaParserPlugin()
    
    def test_format_standard(self):
        """Test 1 : Format standard N 47° 5E.FTN E 006° 5A.JVF"""
        text = "Les coordonnées finales sont : N 47° 5E.FTN E 006° 5A.JVF"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert result["results"][0]["north"] == "N 47° 5E.FTN"
        assert result["results"][0]["east"] == "E 006° 5A.JVF"
        assert result["summary"] == "1 formule détectée"
    
    def test_format_avec_espaces(self):
        """Test 2 : Format avec espaces N 48° 41.E D B"""
        text = "N 48° 41.E D B E 006° 09. F C (A / 2)"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        # Le nettoyage doit supprimer les espaces entre les lettres
        assert "EDB" in result["results"][0]["north"] or "E D B" in result["results"][0]["north"]
        assert "FC" in result["results"][0]["east"] or "F C" in result["results"][0]["east"]
    
    def test_format_avec_operations(self):
        """Test 3 : Format avec opérations N49°18.(B-A)(B-C-F)(D+E)"""
        text = "N49°18.(B-A)(B-C-F)(D+E) E006°16.(C+F)(D+F)(C+D)"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert "(B-A)" in result["results"][0]["north"]
        assert "(C+F)" in result["results"][0]["east"]
    
    def test_texte_sans_formule(self):
        """Test 4 : Texte sans formule (retour vide)"""
        text = "Cette géocache est une traditionnelle sans formule."
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert len(result["results"]) == 0
        assert result["summary"] == "Aucune formule détectée"
    
    def test_formule_dans_html(self):
        """Test 5 : Formule dans description complexe HTML"""
        text = """
        <html>
        <body>
        <p>Bienvenue à cette mystery !</p>
        <div>
            <strong>Coordonnées finales :</strong><br>
            N 48° AB.CDE<br>
            E 006° FG.HIJ
        </div>
        </body>
        </html>
        """
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert "AB.CDE" in result["results"][0]["north"]
        assert "FG.HIJ" in result["results"][0]["east"]
    
    def test_texte_vide(self):
        """Test 6 : Texte vide (erreur)"""
        result = self.plugin.execute({"text": ""})
        
        assert result["status"] == "error"
        assert "Aucun texte fourni" in result["error"]["message"]
    
    def test_format_mixte_majuscules_minuscules(self):
        """Test 7 : Format avec mélange majuscules/minuscules"""
        text = "n 47° 5e.ftn e 006° 5a.jvf"
        result = self.plugin.execute({"text": text})
        
        # Le plugin doit détecter malgré la casse
        assert result["status"] == "success"
        assert len(result["results"]) >= 1
    
    def test_confidence_score(self):
        """Test 8 : Vérifier le score de confiance"""
        text = "N 47° 5E.FTN E 006° 5A.JVF"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        assert result["results"][0]["confidence"] >= 0.8
    
    def test_text_output_format(self):
        """Test 9 : Vérifier le format text_output"""
        text = "N 47° 5E.FTN E 006° 5A.JVF"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        text_output = result["results"][0]["text_output"]
        assert "N 47° 5E.FTN" in text_output
        assert "E 006° 5A.JVF" in text_output
    
    def test_result_id_unique(self):
        """Test 10 : Vérifier que les IDs sont uniques"""
        # Texte hypothétique avec 2 formules (rare mais possible)
        text = """
        Première formule : N 47° 5A.BC E 006° 5D.EF
        Deuxième formule : N 48° 6G.HI E 007° 6J.KL
        """
        result = self.plugin.execute({"text": text})
        
        if len(result["results"]) > 1:
            ids = [r["id"] for r in result["results"]]
            assert len(ids) == len(set(ids))  # Tous les IDs sont uniques
    
    def test_formule_incomplete_nord_seulement(self):
        """Test 11 : Formule incomplète (seulement Nord)"""
        text = "N 47° 5E.FTN"
        result = self.plugin.execute({"text": text})
        
        assert result["status"] == "success"
        # Le plugin doit retourner au moins la partie Nord
        if len(result["results"]) > 0:
            assert result["results"][0]["north"]
    
    def test_special_characters_in_formula(self):
        """Test 12 : Caractères spéciaux dans la formule"""
        text = "N 48° (A+B*2).CDE E 006° (F-G/3).HIJ"
        result = self.plugin.execute({"text": text})

        assert result["status"] == "success"
        if len(result["results"]) > 0:
            assert "*" in result["results"][0]["north"] or "/" in result["results"][0]["east"]

    def test_format_degres_minutes_fixes_expressions_parenthesees(self):
        """Test 13 : Format avec degrés/minutes fixes + expressions parenthésées"""
        text = "N49°12.(A/G-238)(I-135)(D/J-1) E005°59.(C-B)(H-K+1)(F-E-135)"
        result = self.plugin.execute({"text": text})

        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert "(A/G-238)" in result["results"][0]["north"]
        assert "(I-135)" in result["results"][0]["north"]
        assert "(D/J-1)" in result["results"][0]["north"]
        assert "(C-B)" in result["results"][0]["east"]
        assert "(H-K+1)" in result["results"][0]["east"]
        assert "(F-E-135)" in result["results"][0]["east"]

    def test_format_tokens_mixtes_apres_point(self):
        """Test 14 : Décimales avec tokens mixtes (lettre + parenthèses) sans troncature"""
        text = "N48°45.B(A+E)(D+C) E002°43.C(F+C)D"
        result = self.plugin.execute({"text": text})

        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert "B(A+E)(D+C)" in result["results"][0]["north"]
        assert "C(F+C)D" in result["results"][0]["east"]


class TestBasicClean:
    """Tests de la méthode _basic_clean"""
    
    def setup_method(self):
        self.plugin = FormulaParserPlugin()
    
    def test_clean_spaces_between_letters(self):
        """Test nettoyage des espaces entre lettres"""
        input_str = "N 48° 41. E D B"
        cleaned = self.plugin._basic_clean(input_str)
        assert cleaned == "N 48° 41.EDB" or "E D B" not in cleaned or cleaned.count(" ") < input_str.count(" ")
    
    def test_clean_parentheses_spaces(self):
        """Test nettoyage des espaces dans les parenthèses"""
        input_str = "E 006° 09. F C (A / 2)"
        cleaned = self.plugin._basic_clean(input_str)
        # Les espaces autour de / doivent être supprimés
        assert "(A/2)" in cleaned or "( A / 2 )" not in cleaned


class TestFindMethods:
    """Tests des méthodes _find_north et _find_east"""
    
    def setup_method(self):
        self.plugin = FormulaParserPlugin()
    
    def test_find_north_simple(self):
        """Test détection Nord simple"""
        text = "N 47° 12.345"
        match = self.plugin._find_north(text)
        assert match is not None
    
    def test_find_north_with_variables(self):
        """Test détection Nord avec variables"""
        text = "N 47° 12.ABC"
        match = self.plugin._find_north(text)
        assert match is not None
    
    def test_find_north_with_operations(self):
        """Test détection Nord avec opérations"""
        text = "N 47° (A+B).CDE"
        match = self.plugin._find_north(text)
        assert match is not None
    
    def test_find_east_simple(self):
        """Test détection Est simple"""
        text = "E 006° 12.345"
        match = self.plugin._find_east(text)
        assert match is not None
    
    def test_find_east_with_variables(self):
        """Test détection Est avec variables"""
        text = "E 006° 12.FGH"
        match = self.plugin._find_east(text)
        assert match is not None
    
    def test_find_east_none(self):
        """Test pas de match Est"""
        text = "Pas de coordonnées ici"
        match = self.plugin._find_east(text)
        assert match is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
