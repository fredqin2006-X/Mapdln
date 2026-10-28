"""Keep GUI regressions independent of external IP-location services."""
import pytest


@pytest.fixture(scope='session', autouse=True)
def offline_startup_location():
    # Module-scoped editor windows are constructed before function fixtures.
    # Isolate location before those windows start, too.
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr('mapdln.gui.locate', lambda: None)
        yield
