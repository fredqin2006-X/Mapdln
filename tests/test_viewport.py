"""Startup location, real WebEngine viewport persistence, and provider datums."""
import json
import math
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication

from mapdln.config import load, save
from mapdln.gui import Window
from mapdln.geometry import from_wgs
from mapdln.viewport import DEFAULT_VIEW, locate, normalize_view, provider_views
from test_map_interactions import js, wait_for, drag, wheel


@pytest.mark.parametrize('value', [None, {}, {'center': [0, math.nan], 'zoom': 12},
    {'center': [0, 91], 'zoom': 12}, {'center': [0, 0], 'zoom': -1},
    {'center': [True, 0], 'zoom': 12}, {'center': [0, 0], 'zoom': True},
    {'center': [0, 0], 'zoom': 12, 'system': 'invalid'}])
def test_invalid_saved_views_are_ignored(value):
    assert normalize_view(value) is None


@pytest.mark.parametrize('system', ['WGS84', 'GCJ02', 'BD09'])
def test_viewport_datums_round_trip(system):
    original = {'center': [116.3974, 39.9093], 'zoom': 13.5}
    displayed = provider_views(original)
    saved = normalize_view({'center': displayed['centers'][system], 'zoom': 13.5, 'system': system})
    assert saved['center'] == pytest.approx(original['center'], abs=2e-6)
    assert saved['zoom'] == 13.5


def test_location_uses_one_keyless_direct_request():
    session = Mock()
    session.get.return_value.json.return_value = {'success': True, 'longitude': 116.4, 'latitude': 39.9}
    with patch('mapdln.viewport.requests.Session') as factory:
        factory.return_value.__enter__.return_value = session
        assert locate() == {'center': [116.4, 39.9], 'zoom': 12}
    assert session.trust_env is False
    session.get.assert_called_once_with('https://ipwho.is/',
        params={'fields': 'success,longitude,latitude'}, timeout=(3, 5))


def open_map():
    window = Window()
    window.show()
    wait_for(lambda: js(window, '!!window.mapDebugInfo && mapDebugInfo().hasMap'))
    return window


def test_first_location_drag_zoom_exit_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME', str(tmp_path))
    save({'preview_provider': 'offline'})
    locator = Mock(return_value={'center': [116.4, 39.9], 'zoom': 12})
    monkeypatch.setattr('mapdln.gui.locate', locator)
    app = QApplication.instance() or QApplication([])
    window = open_map()
    try:
        assert js(window, 'currentMapView().center') == pytest.approx([116.4, 39.9])
        assert locator.call_count == 1
        drag(window, (420, 310), (510, 355))
        wheel(window, 420, 310)
        final = js(window, 'currentMapView()')
        assert final['center'] != pytest.approx([116.4, 39.9])
        assert final['zoom'] > 12
    finally:
        window.close();app.processEvents()
    saved = load()['map_view']
    assert saved['center'] == pytest.approx(final['center'])
    assert saved['zoom'] == final['zoom']
    locator.reset_mock()
    window = open_map()
    try:
        assert js(window, 'currentMapView().center') == pytest.approx(saved['center'], abs=1e-6)
        assert js(window, 'currentMapView().zoom') == saved['zoom']
        locator.assert_not_called()
    finally:
        window.close();app.processEvents()


@pytest.mark.parametrize('failure', [None, RuntimeError('offline')])
def test_location_failure_does_not_block_map_or_repeat_next_launch(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('MAPDLN_HOME', str(tmp_path))
    save({'preview_provider': 'offline', 'map_view': {'center': [0, 999], 'zoom': 12}})
    locator = Mock(side_effect=failure) if failure else Mock(return_value=None)
    monkeypatch.setattr('mapdln.gui.locate', locator)
    app = QApplication.instance() or QApplication([])
    window = open_map()
    assert js(window, 'currentMapView().center') == pytest.approx(DEFAULT_VIEW['center'])
    window.close();app.processEvents()
    window = open_map()
    try:
        assert locator.call_count == 1
    finally:
        window.close();app.processEvents()


def test_provider_test_view_cannot_replace_main_view(tmp_path, monkeypatch):
    monkeypatch.setenv('MAPDLN_HOME', str(tmp_path))
    original = {'center': [116.4, 39.9], 'zoom': 12}
    save({'preview_provider': 'offline', 'map_view': original})
    app = QApplication.instance() or QApplication([])
    window = open_map()
    try:
        window.test_view._bridge.mapViewChanged(json.dumps({'center': [0, 0], 'zoom': 2}))
        assert window.map_view['center'] == pytest.approx(original['center'])
        # Exercise a domestic SDK's reported coordinate through the real bridge.
        window.web._bridge.mapViewChanged(json.dumps({'center': from_wgs([117, 40], 'BD09'), 'zoom': 14, 'system': 'BD09'}))
        assert window.map_view['center'] == pytest.approx([117, 40], abs=2e-6)
    finally:
        window.close();app.processEvents()
