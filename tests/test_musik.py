import pytest

from alfred.musik import Musik


class FakeSpotify:
    def __init__(self, geraete_nach_start):
        self.gestartet = False
        self._geraete_nach_start = geraete_nach_start
        self.gespielt = []

    def devices(self):
        geraete = self._geraete_nach_start if self.gestartet else []
        return {"devices": [{"id": g, "name": g, "is_active": False} for g in geraete]}

    def search(self, q, type, limit):
        return {"tracks": {"items": [{"uri": "spotify:track:1", "name": "Lied", "artists": [{"name": "Band"}]}]}}

    def start_playback(self, device_id=None, uris=None, context_uri=None):
        self.gespielt.append(device_id)


def musik(tmp_path, spotify, starter):
    m = Musik("id", "geheim", tmp_path / "token.json", starter=starter, warten=lambda s: None)
    m._sp = spotify
    return m


def test_ohne_geraet_oeffnet_es_die_app_und_spielt(tmp_path):
    # Am 6.10.2026: "Kein Spotify-Gerät verfügbar", weil die App nicht offen war.
    spotify = FakeSpotify(["ALEXANDER-PC"])

    def starter():
        spotify.gestartet = True
        return True

    assert musik(tmp_path, spotify, starter).abspielen("Entspannung", "titel") == "Lied von Band"
    assert spotify.gespielt == ["ALEXANDER-PC"]


def test_ohne_app_bleibt_der_fehler(tmp_path):
    with pytest.raises(RuntimeError, match="Kein Spotify-Gerät"):
        musik(tmp_path, FakeSpotify([]), starter=lambda: False).abspielen("Entspannung")
    with pytest.raises(RuntimeError, match="Kein Spotify-Gerät"):
        musik(tmp_path, FakeSpotify(["X"]), starter=None).abspielen("Entspannung")
