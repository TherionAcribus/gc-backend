import pytest
from pathlib import Path

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.plugins.plugin_manager import PluginManager


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _create_manager(app):
    plugins_dir = Path(__file__).parent.parent / 'plugins'
    if not plugins_dir.exists():
        pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
    manager = PluginManager(str(plugins_dir), app)
    manager.discover_plugins()
    return manager


@pytest.mark.plugin
@pytest.mark.unit
class TestWrittenCoordsFR:
    def test_fr_digits_sequence(self, app):
        manager = _create_manager(app)

        text = (
            "nord quarante six degres douze point un deux trois "
            "est zero zero six degres trente deux point huit cinq six"
        )

        result = manager.execute_plugin(
            'written_coords_fr',
            {
                'text': text,
                'max_candidates': 10,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('results')
        assert result['results'][0]['text_output'] == "N 46° 12.123' E 006° 32.856'"

    def test_fr_hundreds(self, app):
        manager = _create_manager(app)

        text = (
            "nord quarante six degres douze point cent vingt trois "
            "est zero zero six degres trente deux point huit cent cinquante six"
        )

        result = manager.execute_plugin(
            'written_coords_fr',
            {
                'text': text,
                'max_candidates': 10,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('results')
        assert result['results'][0]['text_output'] == "N 46° 12.123' E 006° 32.856'"


@pytest.mark.plugin
@pytest.mark.unit
class TestWrittenCoordsEN:
    def test_en_digits_sequence(self, app):
        manager = _create_manager(app)

        text = (
            "north forty six degrees twelve point one two three "
            "east zero zero six degrees thirty two point eight five six"
        )

        result = manager.execute_plugin(
            'written_coords_en',
            {
                'text': text,
                'max_candidates': 10,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('results')
        assert result['results'][0]['text_output'] == "N 46° 12.123' E 006° 32.856'"

    def test_en_hundreds(self, app):
        manager = _create_manager(app)

        text = (
            "north forty six degrees twelve point one hundred twenty three "
            "east zero zero six degrees thirty two point eight hundred fifty six"
        )

        result = manager.execute_plugin(
            'written_coords_en',
            {
                'text': text,
                'max_candidates': 10,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('results')
        assert result['results'][0]['text_output'] == "N 46° 12.123' E 006° 32.856'"


@pytest.mark.plugin
@pytest.mark.integration
class TestWrittenCoordsConverter:
    def test_converter_validates_and_returns_primary(self, app):
        manager = _create_manager(app)

        text = (
            "nord quarante six degres douze point cent vingt trois "
            "est zero zero six degres trente deux point huit cent cinquante six"
        )

        result = manager.execute_plugin(
            'written_coords_converter',
            {
                'text': text,
                'languages': ['fr'],
                'max_candidates': 20,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('results')
        assert result.get('primary_coordinates')
        assert result['primary_coordinates']['exist'] is True

    def test_converter_auto_languages(self, app):
        manager = _create_manager(app)

        text = (
            "north forty six degrees twelve point one two three "
            "east zero zero six degrees thirty two point eight five six"
        )

        result = manager.execute_plugin(
            'written_coords_converter',
            {
                'text': text,
                'languages': 'auto',
                'max_candidates': 20,
                'include_deconcat': True,
            },
        )

        assert result is not None
        assert result['status'] == 'success'
        assert result.get('primary_coordinates')
        assert result['primary_coordinates']['exist'] is True
