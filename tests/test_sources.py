"""Tests de la source OpenF1, sans réseau : les réponses de l'API sont simulées."""

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import sources

DEBUT = datetime(2024, 8, 31, 15, 0, tzinfo=UTC)


def date_iso(d):
    return d.isoformat(timespec="microseconds")


# --- Messages de la direction de course ----------------------------------------------

@pytest.mark.parametrize("message, attendu", [
    ("CAR 81 (PIA) TIME 1:50.311 DELETED - TRACK LIMITS AT TURN 2 LAP 3 16:05:33", (81, 3)),
    ("CAR 44 (HAM) LAP DELETED - TRACK LIMITS AT TURN 2 LAP 4 16:10:08 (PIT)", (44, 4)),
])
def test_regex_tour_supprime(message, attendu):
    trouve = sources.REGEX_TOUR_SUPPRIME.search(message)
    assert (int(trouve.group(1)), int(trouve.group(2))) == attendu


def test_tours_supprimes_ignore_les_autres_messages(monkeypatch):
    messages = [
        {"message": "CAR 16 (LEC) TIME 1:19.000 DELETED - TRACK LIMITS AT TURN 11 LAP 7 15:20:00"},
        {"message": "GREEN LIGHT - PIT EXIT OPEN"},
        {"message": None},
    ]
    monkeypatch.setattr(sources, "_openf1", lambda endpoint, **_: messages)
    assert sources._tours_supprimes(9586) == {(16, 7)}


# --- Recherche de la session ----------------------------------------------------------

def test_trouver_session_prefere_le_grand_prix_aux_essais(monkeypatch):
    reponses = {
        "meetings": [
            {"meeting_key": 1, "meeting_name": "Pre-Season Testing", "location": "Sakhir"},
            {"meeting_key": 2, "meeting_name": "Bahrain Grand Prix", "location": "Sakhir"},
        ],
        "sessions": [{"session_key": 20, "session_name": "Practice 1"},
                     {"session_key": 21, "session_name": "Qualifying"}],
    }
    monkeypatch.setattr(sources, "_openf1", lambda endpoint, **_: reponses[endpoint])
    meeting, session = sources._trouver_session(2024, "sakhir", "Q")
    assert (meeting["meeting_key"], session["session_key"]) == (2, 21)
    with pytest.raises(SystemExit, match="Pas de session"):
        sources._trouver_session(2024, "sakhir", "R")
    with pytest.raises(SystemExit, match="Aucun Grand Prix"):
        sources._trouver_session(2024, "Monza", "Q")


# --- Télémétrie et recalage -----------------------------------------------------------

def vitesse_kmh(t):
    """Profil de vitesse qui varie (sinon le recalage n'a rien à superposer)."""
    return 200 + 80 * np.sin(2 * np.pi * np.asarray(t) / 20)


def fausse_car_data(debut, duree=80.0):
    temps = np.arange(0.1, duree, 0.25)
    return [{"date": date_iso(debut + timedelta(seconds=float(t))), "speed": float(v),
             "throttle": 100, "brake": 100 if v < 150 else 0, "n_gear": 7}
            for t, v in zip(temps, vitesse_kmh(temps), strict=True)]


def test_telemetrie_openf1_integre_la_vitesse(monkeypatch):
    monkeypatch.setattr(sources, "_openf1", lambda endpoint, filtres=(), **_: fausse_car_data(DEBUT))
    tel = sources._telemetrie_openf1(9586, 16, DEBUT, DEBUT + timedelta(seconds=80))
    assert list(tel.columns) == sources.COLONNES_TELEMETRIE
    assert tel["Time"].iloc[0] == pytest.approx(0.1)
    assert tel["Brake"].dtype == bool and tel["Brake"].any()
    # Distance = intégrale de la vitesse : on compare au calcul exact sur une grille fine
    t_fin = np.linspace(0, tel["Time"].iloc[-1], 100001)
    attendu = np.trapezoid(vitesse_kmh(t_fin) / 3.6, t_fin)
    assert tel["Distance"].iloc[-1] == pytest.approx(attendu, rel=0.002)


def test_telemetrie_absente(monkeypatch):
    monkeypatch.setattr(sources, "_openf1", lambda endpoint, filtres=(), **_: [])
    with pytest.raises(SystemExit, match="Pas de télémétrie"):
        sources._telemetrie_openf1(9586, 16, DEBUT, DEBUT + timedelta(seconds=80))


def tel_profil(decalage_m=0.0):
    d = np.arange(0, 5000, 20.0)
    vitesse = 200 + 100 * np.sin(2 * np.pi * (d - decalage_m) / 1300)
    temps = np.concatenate([[0.0], np.cumsum(np.diff(d) / (vitesse[1:] / 3.6))]) + 0.05
    return pd.DataFrame({"Distance": d, "Time": temps, "Speed": vitesse})


def test_decalage_spatial_retrouve_le_decalage():
    # Tour décalé de 30 m (fenêtre ouverte trop tôt) : il faut lui ajouter -30 m
    assert sources._decalage_spatial(tel_profil(), tel_profil(decalage_m=30)) == -30


def test_aligner_sur_retire_les_points_hors_du_tour():
    ref = tel_profil()
    duree = ref["Time"].iloc[-1]
    tel, decalage = sources._aligner_sur(ref, tel_profil(decalage_m=30), duree)
    assert decalage == -30
    assert tel["Time"].between(0, duree, inclusive="neither").all()


# --- Cache et requêtes HTTP -----------------------------------------------------------

class FausseReponse(io.BytesIO):
    def __init__(self, donnees):
        super().__init__(json.dumps(donnees).encode())


def erreur_http(code):
    return urllib.error.HTTPError("https://api.openf1.org", code, "erreur", None, None)


@pytest.fixture
def cache_vide(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "DOSSIER_CACHE_OPENF1", str(tmp_path))
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    return tmp_path


def test_get_json_met_en_cache(cache_vide, monkeypatch):
    appels = []

    def faux_urlopen(requete, timeout):
        appels.append(requete.full_url)
        return FausseReponse([{"ok": 1}])

    monkeypatch.setattr(sources.urllib.request, "urlopen", faux_urlopen)
    assert sources._get_json("https://x/a") == [{"ok": 1}]
    assert sources._get_json("https://x/a") == [{"ok": 1}]
    assert len(appels) == 1  # 2e appel servi par le cache
    assert len(list(cache_vide.iterdir())) == 1


def test_get_json_reessaie_apres_429(cache_vide, monkeypatch):
    reponses = [erreur_http(429), erreur_http(429), FausseReponse({"ok": 2})]

    def faux_urlopen(requete, timeout):
        reponse = reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    monkeypatch.setattr(sources.urllib.request, "urlopen", faux_urlopen)
    assert sources._get_json("https://x/b") == {"ok": 2}
    assert reponses == []


def test_get_json_404_vide_et_pas_en_cache(cache_vide, monkeypatch):
    def faux_urlopen(requete, timeout):
        raise erreur_http(404)

    monkeypatch.setattr(sources.urllib.request, "urlopen", faux_urlopen)
    assert sources._get_json("https://x/c") == []
    assert list(cache_vide.iterdir()) == []


def test_requetes_espacees(monkeypatch):
    attentes = []
    monkeypatch.setattr(sources.time, "sleep", attentes.append)
    monkeypatch.setattr(sources.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(sources, "_derniere_requete", 99.5)  # requête précédente il y a 0,5 s
    sources._patienter_avant_requete()
    assert attentes == [pytest.approx(sources.INTERVALLE_REQUETES_S - 0.5)]
    monkeypatch.setattr(sources, "_derniere_requete", 0.0)  # il y a longtemps : pas d'attente
    attentes.clear()
    sources._patienter_avant_requete()
    assert attentes == []


def test_delai_apres_429_respecte_retry_after():
    avec_entete = urllib.error.HTTPError("https://x", 429, "trop", {"Retry-After": "12"}, None)
    assert sources._delai_apres_429(avec_entete, essai=0) == 12.0
    assert sources._delai_apres_429(erreur_http(429), essai=1) == 10.0  # sans en-tête : 5 × 2^essai
    assert sources._delai_apres_429(erreur_http(429), essai=10) == 60.0  # plafonné


def test_get_json_autre_erreur_remonte(cache_vide, monkeypatch):
    def faux_urlopen(requete, timeout):
        raise erreur_http(500)

    monkeypatch.setattr(sources.urllib.request, "urlopen", faux_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        sources._get_json("https://x/d")


# --- Chargement complet ---------------------------------------------------------------

def lire_debut(filtres):
    """Date de début d'un filtre « date>=... » (format envoyé par _filtres_dates)."""
    texte = next(f for f in filtres if f.startswith("date>="))[len("date>="):]
    return datetime.fromisoformat(texte).replace(tzinfo=UTC)


def tour(numero, duree, debut_s, sortie_stand=False):
    return {"lap_number": numero, "lap_duration": duree, "is_pit_out_lap": sortie_stand,
            "date_start": date_iso(DEBUT + timedelta(seconds=debut_s))}


def faux_openf1(endpoint, filtres=(), **params):
    numero = params.get("driver_number")
    if endpoint == "meetings":
        return [{"meeting_key": 1, "meeting_name": "Test Grand Prix", "location": "Testville"}]
    if endpoint == "sessions":
        return [{"session_key": 9, "session_name": "Qualifying", "circuit_key": 7}]
    if endpoint == "drivers":
        return [{"name_acronym": "AAA", "driver_number": 1, "team_colour": "FF0000"},
                {"name_acronym": "BBB", "driver_number": 2, "team_colour": "0000FF"}]
    if endpoint == "race_control":
        return [{"message": "CAR 1 (AAA) TIME 1:19.000 DELETED - TRACK LIMITS AT TURN 2 LAP 3 15:05:00"}]
    if endpoint == "laps" and numero == 1:
        # Le tour 3 est le plus rapide mais supprimé, le tour 1 est une sortie des stands
        return [tour(1, 70.0, 0, sortie_stand=True), tour(2, 80.0, 100), tour(3, 79.0, 200),
                tour(4, 81.0, 300)]
    if endpoint == "laps":
        return [tour(1, 80.5, 1000), tour(2, None, 1100)]
    if endpoint == "stints" and numero == 1:
        return [{"lap_start": 1, "lap_end": 1, "compound": "MEDIUM"},
                {"lap_start": 2, "lap_end": None, "compound": "SOFT"}]
    if endpoint == "stints":
        return []
    if endpoint == "car_data":
        return fausse_car_data(lire_debut(filtres))
    if endpoint == "location":
        debut = lire_debut(filtres)
        return [{"date": date_iso(debut + timedelta(seconds=t)),
                 "x": 1000 * np.cos(t / 80 * 2 * np.pi), "y": 1000 * np.sin(t / 80 * 2 * np.pi)}
                for t in np.arange(0.1, 80, 0.27)]
    raise AssertionError(f"endpoint inattendu : {endpoint}")


def test_charger_openf1_de_bout_en_bout(monkeypatch):
    monkeypatch.setattr(sources, "_openf1", faux_openf1)
    circuit = {"rotation": 90, "corners": [{"number": 1, "trackPosition": {"x": -1000, "y": 0}}]}
    monkeypatch.setattr(sources, "_circuit_multiviewer", lambda cle, annee: circuit)

    comparaison = sources.charger_openf1(2024, "testville", "Q", ["AAA", "BBB"])
    aaa, bbb = comparaison.tours

    assert comparaison.titre == "Test Grand Prix 2024 - Qualifying"
    assert (aaa.temps, aaa.pneu, aaa.couleur) == (80.0, "SOFT", "#FF0000")  # pas le tour 3 supprimé
    assert (bbb.temps, bbb.pneu) == (80.5, "?")
    assert comparaison.rotation == 90
    assert not comparaison.trace.empty
    # Le virage est à l'opposé du point de départ sur le cercle : à mi-tour
    assert comparaison.virages["Distance"].iloc[0] == pytest.approx(
        aaa.tel["Distance"].iloc[-1] / 2, rel=0.03)


def test_charger_openf1_refuse_avant_2023_et_pilote_inconnu(monkeypatch):
    with pytest.raises(SystemExit, match="2023"):
        sources.charger_openf1(2022, "Monza", "Q", ["LEC", "NOR"])
    monkeypatch.setattr(sources, "_openf1", faux_openf1)
    with pytest.raises(SystemExit, match="ZZZ absent"):
        sources.charger_openf1(2024, "testville", "Q", ["AAA", "ZZZ"])
