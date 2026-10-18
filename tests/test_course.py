"""Tests du tableau tour par tour, sur une course fictive construite à la main (sans réseau)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import course

DEPART = datetime(2024, 9, 1, 13, 0, tzinfo=UTC)
TEMPS_TOUR = 90.0
NB_TOURS = 10


def iso(secondes):
    return (DEPART + timedelta(seconds=secondes)).isoformat()


def heure(secondes):
    return pd.Timestamp(DEPART + timedelta(seconds=secondes))


PILOTES = [{"driver_number": 1, "name_acronym": "AAA", "team_name": "Rouge"},
           {"driver_number": 2, "name_acronym": "BBB", "team_name": "Bleu"}]


def laps_fictifs():
    """2 pilotes, 10 tours de 90 s ; BBB roule 1 s derrière AAA."""
    laps = []
    for numero, decalage in ((1, 0.0), (2, 1.0)):
        for tour in range(1, NB_TOURS + 1):
            laps.append({"driver_number": numero, "lap_number": tour, "lap_duration": TEMPS_TOUR,
                         "date_start": iso(decalage + (tour - 1) * TEMPS_TOUR),
                         "is_pit_out_lap": False})
    laps[0]["date_start"] = None  # OpenF1 ne donne souvent pas le début du 1er tour
    return laps


def tours_de_base():
    return course.construire_tours(laps_fictifs(), PILOTES)


# --- Construction ----------------------------------------------------------------------

def test_construire_tours():
    tours = tours_de_base()
    assert len(tours) == 2 * NB_TOURS
    aaa = tours[tours["pilote"] == "AAA"].set_index("tour")
    assert aaa.loc[3, "fin"] == heure(3 * TEMPS_TOUR)
    assert aaa.loc[1, "debut"] == heure(0)  # reconstitué : fin - durée
    assert set(tours["equipe"]) == {"Rouge", "Bleu"}


def test_fin_du_tour_sans_duree_prise_au_tour_suivant():
    laps = laps_fictifs()
    laps[4]["lap_duration"] = None  # AAA tour 5
    aaa = course.construire_tours(laps, PILOTES).query("pilote == 'AAA'").set_index("tour")
    assert np.isnan(aaa.loc[5, "temps"])
    assert aaa.loc[5, "fin"] == aaa.loc[6, "debut"]


def test_tour_jamais_boucle_retire():
    # Tour de décélération après l'arrivée : un début, mais ni durée ni tour suivant
    laps = laps_fictifs() + [{"driver_number": 1, "lap_number": NB_TOURS + 1, "lap_duration": None,
                              "date_start": iso(NB_TOURS * TEMPS_TOUR), "is_pit_out_lap": False}]
    tours = course.construire_tours(laps, PILOTES)
    assert tours["tour"].max() == NB_TOURS
    assert tours["fin"].notna().all()


def test_ajouter_pneus_age_et_chevauchement():
    stints = [
        {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 4, "compound": "MEDIUM",
         "tyre_age_at_start": 2},
        # Le relais 2 commence officiellement au tour 4 (tour d'entrée) : ce tour reste en MEDIUM
        {"driver_number": 1, "stint_number": 2, "lap_start": 4, "lap_end": None, "compound": "HARD",
         "tyre_age_at_start": 0},
    ]
    aaa = course.ajouter_pneus(tours_de_base(), stints).query("pilote == 'AAA'").set_index("tour")
    assert aaa.loc[1, ["compose", "age_pneu"]].tolist() == ["MEDIUM", 2.0]
    assert aaa.loc[4, ["compose", "relais"]].tolist() == ["MEDIUM", 1]
    assert aaa.loc[5, ["compose", "age_pneu"]].tolist() == ["HARD", 1.0]
    assert aaa.loc[NB_TOURS, "compose"] == "HARD"  # relais sans fin connue = jusqu'au bout


def test_ajouter_stands():
    pits = [{"driver_number": 2, "lap_number": 4, "pit_duration": 23.5}]
    bbb = course.ajouter_stands(tours_de_base(), pits).query("pilote == 'BBB'").set_index("tour")
    assert bbb.loc[4, ["entree_stand", "sortie_stand", "duree_stand"]].tolist() == [True, False, 23.5]
    assert bbb.loc[5, ["entree_stand", "sortie_stand"]].tolist() == [False, True]
    assert bbb["entree_stand"].sum() == 1


def test_entree_aux_stands_deduite_du_tour_de_sortie():
    # Aucun arrêt enregistré par l'API, mais le tour 6 de BBB est signalé comme sortie des stands
    laps = laps_fictifs()
    laps[NB_TOURS + 5]["is_pit_out_lap"] = True
    bbb = course.ajouter_stands(course.construire_tours(laps, PILOTES), []).query("pilote == 'BBB'")
    bbb = bbb.set_index("tour")
    assert bbb.loc[5, "entree_stand"] and bbb.loc[6, "sortie_stand"]
    assert np.isnan(bbb.loc[5, "duree_stand"])
    assert bbb["entree_stand"].sum() == 1


def test_position_et_ecart_en_fin_de_tour():
    positions = [{"driver_number": 1, "date": iso(0), "position": 1},
                 {"driver_number": 2, "date": iso(0), "position": 2},
                 {"driver_number": 2, "date": iso(5 * TEMPS_TOUR + 30), "position": 1},
                 {"driver_number": 1, "date": iso(5 * TEMPS_TOUR + 30), "position": 2}]
    intervalles = [{"driver_number": 2, "date": iso(t), "interval": 1.0} for t in range(0, 300, 4)]
    intervalles += [{"driver_number": 1, "date": iso(3 * TEMPS_TOUR - 2), "interval": 0.0},  # leader
                    {"driver_number": 2, "date": iso(4 * TEMPS_TOUR - 1), "interval": "+1 LAP"}]
    tours = course.ajouter_position_et_ecart(tours_de_base(), positions, intervalles)
    aaa = tours.query("pilote == 'AAA'").set_index("tour")
    bbb = tours.query("pilote == 'BBB'").set_index("tour")
    assert aaa.loc[5, "position"] == 1 and aaa.loc[6, "position"] == 2  # dépassé pendant le tour 6
    assert bbb.loc[2, "ecart_devant"] == 1.0
    assert np.isnan(aaa.loc[3, "ecart_devant"])  # leader : vide, même si l'API donne 0
    assert np.isnan(bbb.loc[4, "ecart_devant"])  # « +1 LAP »
    assert np.isnan(bbb.loc[8, "ecart_devant"])  # dernière valeur trop ancienne (> 30 s)


# --- Neutralisations et drapeaux --------------------------------------------------------

def message(secondes, texte, flag=None, tour=None, secteur=None, portee=None):
    return {"date": iso(secondes), "message": texte, "flag": flag, "lap_number": tour,
            "sector": secteur, "scope": portee}


def test_periodes_neutralisees():
    tours = tours_de_base()
    messages = [
        message(100, "VIRTUAL SAFETY CAR DEPLOYED", tour=2),
        message(150, "VIRTUAL SAFETY CAR ENDING", tour=2),
        message(400, "SAFETY CAR DEPLOYED", tour=5),
        message(500, "SAFETY CAR IN THIS LAP", tour=6),
        message(700, "RED FLAG", flag="RED", tour=8),
        message(750, "SESSION STARTED", tour=8),
        message(800, "SAFETY CAR DEPLOYED", tour=9),  # jamais rentrée : fin de course
    ]
    periodes = course.periodes_neutralisees(messages, tours).to_dict("records")
    assert [p["type"] for p in periodes] == ["VSC", "SC", "ROUGE", "SC"]
    assert (periodes[0]["debut"], periodes[0]["fin"]) == (heure(100), heure(150))
    # SC jusqu'au 1er passage sur la ligne à la fin du tour 6 (le leader, AAA)
    assert periodes[1]["fin"] == heure(6 * TEMPS_TOUR)
    assert (periodes[2]["debut"], periodes[2]["fin"]) == (heure(700), heure(750))
    assert periodes[3]["fin"] == tours["fin"].max()


def test_drapeau_rouge_ferme_la_voiture_de_securite():
    messages = [message(400, "SAFETY CAR DEPLOYED"), message(450, "RED FLAG", flag="RED"),
                message(600, "SESSION STARTED")]
    periodes = course.periodes_neutralisees(messages, tours_de_base())
    assert periodes["type"].tolist() == ["SC", "ROUGE"]
    assert periodes["fin"].iloc[0] == heure(450)


def test_periodes_double_jaune():
    messages = [
        message(100, "YELLOW IN TRACK SECTOR 3", flag="YELLOW", secteur=3, portee="Sector"),
        message(200, "DOUBLE YELLOW IN TRACK SECTOR 5", flag="DOUBLE YELLOW", secteur=5, portee="Sector"),
        message(230, "CLEAR IN TRACK SECTOR 3", flag="CLEAR", secteur=3, portee="Sector"),
        message(260, "CLEAR IN TRACK SECTOR 5", flag="CLEAR", secteur=5, portee="Sector"),
    ]
    periodes = course.periodes_double_jaune(messages)
    assert periodes.to_dict("records") == [{"debut": heure(200), "fin": heure(260)}]


def test_etat_piste_et_relance():
    tours = tours_de_base()
    neutralisations = pd.DataFrame([{"type": "VSC", "debut": heure(400), "fin": heure(420)},
                                    {"type": "SC", "debut": heure(410), "fin": heure(500)}])
    jaunes = pd.DataFrame([{"debut": heure(650), "fin": heure(660)}])
    aaa = course.ajouter_etat_piste(tours, neutralisations, jaunes).query("pilote == 'AAA'").set_index("tour")
    assert aaa.loc[5, "etat_piste"] == "SC"  # VSC puis SC dans le même tour : le plus grave
    assert aaa.loc[6, "etat_piste"] == "SC"
    assert aaa.loc[7, ["etat_piste", "apres_neutralisation"]].tolist() == ["VERT", True]
    assert aaa.loc[8, "apres_neutralisation"] == False  # noqa: E712
    assert aaa.loc[8, "double_jaune"] and not aaa.loc[7, "double_jaune"]


def test_pluie():
    meteo = [{"date": iso(t), "rainfall": 1 if 300 <= t <= 360 else 0} for t in range(0, 900, 60)]
    aaa = course.ajouter_pluie(tours_de_base(), meteo).query("pilote == 'AAA'").set_index("tour")
    assert aaa.loc[4, "pluie"] and aaa.loc[5, "pluie"]
    assert not aaa.loc[2, "pluie"] and not aaa.loc[8, "pluie"]


# --- Tours propres ---------------------------------------------------------------------

def tours_complets():
    tours = course.ajouter_pneus(tours_de_base(), [
        {"driver_number": n, "stint_number": 1, "lap_start": 1, "lap_end": None,
         "compound": "MEDIUM", "tyre_age_at_start": 0} for n in (1, 2)])
    tours = course.ajouter_stands(tours, [{"driver_number": 2, "lap_number": 4, "pit_duration": 23.0}])
    tours = course.ajouter_etat_piste(tours, pd.DataFrame(
        [{"type": "SC", "debut": heure(560), "fin": heure(600)}]), pd.DataFrame(columns=["debut", "fin"]))
    tours["pluie"] = False
    return tours


def test_marquer_tours_propres():
    tours = tours_complets()
    tours.loc[(tours["pilote"] == "AAA") & (tours["tour"] == 3), "temps"] = 1.2 * TEMPS_TOUR
    tours = course.marquer_tours_propres(tours)
    aaa = tours.query("pilote == 'AAA'").set_index("tour")["exclusion"]
    bbb = tours.query("pilote == 'BBB'").set_index("tour")["exclusion"]
    assert aaa[1] == "1er tour"
    assert aaa[3] == "lent (> 107 %)"
    assert (aaa[7], aaa[8]) == ("neutralisation", "relance après neutralisation")
    assert (bbb[4], bbb[5]) == ("entrée aux stands", "sortie des stands")
    assert aaa[2] == "" and tours.query("pilote == 'AAA' and tour == 2")["propre"].all()


def test_pneus_pluie_et_pluie_exclus():
    tours = tours_complets()
    tours.loc[tours["tour"] == 9, "compose"] = "INTERMEDIATE"
    tours.loc[tours["tour"] == 10, "pluie"] = True
    tours = course.marquer_tours_propres(tours).query("pilote == 'AAA'").set_index("tour")
    assert (tours.loc[9, "exclusion"], tours.loc[10, "exclusion"]) == ("pneus pluie", "pluie")


# --- Chargement complet ----------------------------------------------------------------

def test_charger_course_de_bout_en_bout(monkeypatch):
    reponses = {
        "laps": laps_fictifs(),
        "drivers": PILOTES,
        "stints": [{"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": None,
                    "compound": "HARD", "tyre_age_at_start": 0},
                   {"driver_number": 2, "stint_number": 1, "lap_start": 1, "lap_end": 4,
                    "compound": "MEDIUM", "tyre_age_at_start": 0},
                   {"driver_number": 2, "stint_number": 2, "lap_start": 5, "lap_end": None,
                    "compound": "HARD", "tyre_age_at_start": 0}],
        "pit": [{"driver_number": 2, "lap_number": 4, "pit_duration": 23.0}],
        "position": [{"driver_number": 1, "date": iso(0), "position": 1},
                     {"driver_number": 2, "date": iso(0), "position": 2}],
        "intervals": [{"driver_number": 2, "date": iso(t), "interval": 1.0} for t in range(0, 900, 4)],
        "race_control": [message(560, "SAFETY CAR DEPLOYED", tour=7),
                         message(600, "SAFETY CAR IN THIS LAP", tour=7)],
        "weather": [{"date": iso(t), "rainfall": 0} for t in range(0, 900, 60)],
    }
    monkeypatch.setattr(course, "_openf1", lambda endpoint, **_: reponses[endpoint])
    monkeypatch.setattr(course, "_trouver_session", lambda annee, gp, s: (
        {"meeting_name": "Test Grand Prix"}, {"session_key": 9, "session_name": "Race", "circuit_key": 7}))
    monkeypatch.setattr(course, "_circuit_multiviewer", lambda cle, annee: {
        "pitLoss": {"normal": "21.5", "sc": "13.0", "vsc": ""}})

    c = course.charger_course(2024, "test")
    assert c.titre == "Test Grand Prix 2024 - Race"
    assert c.perte_stand == {"normal": 21.5, "sc": 13.0}  # valeur vide ignorée
    assert c.neutralisations["type"].tolist() == ["SC"]
    colonnes = {"pilote", "tour", "temps", "compose", "age_pneu", "entree_stand", "sortie_stand",
                "position", "ecart_devant", "etat_piste", "pluie", "exclusion", "propre"}
    assert colonnes <= set(c.tours.columns)
    assert c.tours["propre"].sum() == 2 * NB_TOURS - 2 - 2 - 2 - 2  # 1er tour, stands, SC, relance
    assert course.strategies(c.tours) == {"AAA": "HARD 1-10", "BBB": "MEDIUM 1-4 → HARD 5-10"}


@pytest.mark.parametrize("texte", ["São Paulo", "SAO PAULO", "sao paulo"])
def test_recherche_insensible_aux_accents(texte):
    from sources import _sans_accents
    assert _sans_accents(texte) == "sao paulo"
