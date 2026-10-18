"""Tests de la vérification sur l'historique, sur des duels construits à la main (sans réseau)."""

import numpy as np
import pandas as pd
import pytest

import validation
from course import Course
from usure import ajuster_usure

NB_TOURS = 30
DEBUT = pd.Timestamp("2024-09-01 13:00", tz="UTC")
COUT_ENTREE, COUT_SORTIE = 4.0, 21.0


def temps_tour(niveau, tour, compose, age):
    return niveau - 0.05 * tour + (-0.40 if compose == "MEDIUM" else 0.0) + \
        (0.08 if compose == "MEDIUM" else 0.03) * age


def course_duel(ecart_depart=1.0, arret_a=11, arret_b=14, sc_tour=None, graine=0):
    """
    B mène, A part `ecart_depart` s derrière ; tous deux en médium puis dur.
    Six autres pilotes, loin derrière, s'arrêtent tôt (tours 4 à 9) pour que le modèle
    connaisse déjà les deux composés au moment de la décision (tour arret_a - 1).
    """
    rng = np.random.default_rng(graine)
    pilotes = [("BBB", 0.0, arret_b, ["MEDIUM", "HARD"]), ("AAA", ecart_depart, arret_a, ["MEDIUM", "HARD"])]
    pilotes += [(f"F{i}", 30.0 + 10 * i, 4 + i, ["MEDIUM", "HARD"] if i % 2 else ["HARD", "MEDIUM"])
                for i in range(6)]
    lignes = []
    for pilote, decalage, arret, ordre in pilotes:
        horloge = DEBUT + pd.Timedelta(decalage, unit="s")
        for tour in range(1, NB_TOURS + 1):
            relais = 1 if tour <= arret else 2
            compose = ordre[relais - 1]
            age = float(tour - 1 if relais == 1 else tour - arret - 1)
            entree, sortie = tour == arret, tour == arret + 1
            temps = temps_tour(90.0, tour, compose, age) + rng.normal(0, 0.05)
            temps += COUT_ENTREE * entree + COUT_SORTIE * sortie
            debut, horloge = horloge, horloge + pd.Timedelta(temps, unit="s")
            etat = "SC" if tour == sc_tour else "VERT"
            lignes.append({"pilote": pilote, "equipe": pilote, "tour": tour, "temps": temps, "debut": debut,
                           "fin": horloge, "compose": compose, "relais": relais, "age_pneu": age,
                           "entree_stand": entree, "sortie_stand": sortie, "duree_stand": np.nan,
                           "etat_piste": etat,
                           "propre": tour > 1 and not (entree or sortie) and etat == "VERT"})
    return Course("Duel Grand Prix 2024 - Race", 2024, 1, 1, {"normal": 24.0},
                  pd.DataFrame(columns=["type", "debut", "fin"]), pd.DataFrame(lignes))


# --- Détection -------------------------------------------------------------------------

def test_detecte_l_undercut():
    course = course_duel()
    cas = validation.detecter_undercuts(course)
    duel = cas[cas["poursuivant"] == "AAA"]
    assert len(duel) == 1
    duel = duel.iloc[0]
    assert (duel["devant"], duel["tour"]) == ("BBB", 10)
    assert (duel["arret_poursuivant"], duel["arret_devant"]) == (11, 14)
    assert duel["reaction"] == 3
    assert duel["ecart_depart"] == pytest.approx(1.0, abs=0.2)
    fins = course.tours.set_index(["pilote", "tour"])["fin"]
    reel = (fins[("AAA", 15)] - fins[("BBB", 15)]).total_seconds()
    assert duel["ecart_reel"] == pytest.approx(reel)
    assert duel["reussi"] == (reel < 0)


@pytest.mark.parametrize("parametres", [
    {"ecart_depart": 4.0},  # trop loin derrière pour une tentative d'undercut
    {"arret_b": 18},  # B réagit 7 tours plus tard
    {"sc_tour": 13},  # voiture de sécurité pendant le cycle
])
def test_cas_ecartes(parametres):
    cas = validation.detecter_undercuts(course_duel(**parametres))
    assert cas.empty or (cas["poursuivant"] != "AAA").all()


# --- Prédiction sans données du futur -----------------------------------------------------

def test_prediction_cohérente_avec_le_resultat():
    # Réaction 3 tours plus tard : environ 1,5 s de gain pour A
    proche = validation.predire_undercuts(course_duel(1.0), validation.detecter_undercuts(course_duel(1.0)),
                                          validation.APriori(), nb_bootstrap=30)
    loin = validation.predire_undercuts(course_duel(2.6), validation.detecter_undercuts(course_duel(2.6)),
                                        validation.APriori(), nb_bootstrap=30)
    p_proche = proche.loc[proche["poursuivant"] == "AAA", "probabilite"].iloc[0]
    p_loin = loin.loc[loin["poursuivant"] == "AAA", "probabilite"].iloc[0]
    assert p_proche > 0.7 and p_loin < 0.3


def test_aucune_fuite_de_donnees_du_futur():
    course = course_duel()
    cas = validation.detecter_undercuts(course)
    cas = cas[cas["poursuivant"] == "AAA"]
    avant = validation.predire_undercuts(course, cas, validation.APriori(), nb_bootstrap=30)
    # On bouleverse tout ce qui se passe après le tour de décision (10)
    futur = course.tours["tour"] > 10
    course.tours.loc[futur, "temps"] += 5.0
    course.tours.loc[futur, "compose"] = "SOFT"
    apres = validation.predire_undercuts(course, cas, validation.APriori(), nb_bootstrap=30)
    assert apres["probabilite"].iloc[0] == avant["probabilite"].iloc[0]
    assert apres["ecart_predit"].iloc[0] == avant["ecart_predit"].iloc[0]


def test_a_priori_et_complement():
    a_priori = validation.APriori()
    modele_complet = ajuster_usure(course_duel().tours, nb_bootstrap=0)
    assert a_priori.valeurs("HARD", "MEDIUM") is None
    a_priori.ajouter(modele_complet)
    a_priori.ajouter(modele_complet)
    ecart, usure, _, _ = a_priori.valeurs("MEDIUM", "HARD")
    assert ecart == pytest.approx(-0.40, abs=0.1) and usure == pytest.approx(0.08, abs=0.02)

    # Modèle qui ne connaît que le médium : le dur est ajouté depuis l'a priori
    seul_medium = ajuster_usure(course_duel().tours.query("compose == 'MEDIUM'"), nb_bootstrap=20)
    complet, ajoutes = validation.completer_avec_a_priori(seul_medium, a_priori, {"HARD"})
    assert ajoutes == ["HARD"] and complet.composes == ["MEDIUM", "HARD"]
    assert complet.coefficient("compose:HARD") == pytest.approx(0.40, abs=0.1)
    assert complet.tirages.shape == (20, len(complet.colonnes))
    with pytest.raises(ValueError, match="pas d'a priori"):
        validation.completer_avec_a_priori(seul_medium, validation.APriori(), {"HARD"})


def test_pilote_sans_tour_propre_non_predit():
    course = course_duel()
    course.tours.loc[(course.tours["pilote"] == "AAA") & (course.tours["tour"] <= 10), "propre"] = False
    cas = validation.detecter_undercuts(course)
    r = validation.predire_undercuts(course, cas[cas["poursuivant"] == "AAA"], validation.APriori(), 20)
    assert np.isnan(r["probabilite"].iloc[0]) and "sans tour propre" in r["ignore"].iloc[0]


# --- Mesures -----------------------------------------------------------------------------

@pytest.fixture
def resultats():
    return pd.DataFrame({
        "probabilite": [0.9, 0.8, 0.3, 0.1, 0.6, np.nan],
        "reussi": [True, True, False, False, False, True],
        "ecart_depart": [0.5, 1.0, 2.5, 2.9, 1.5, 0.2],
    })


def test_brier():
    assert validation.brier([1, 0], [True, False]) == 0.0
    assert validation.brier([0.5, 0.5], [True, False]) == 0.25


def test_mesurer(resultats):
    tableau = validation.mesurer(resultats).set_index("méthode")
    assert tableau.loc["modèle", "bonnes prédictions"] == pytest.approx(4 / 5)
    assert tableau.loc["toujours réussi", "bonnes prédictions"] == pytest.approx(2 / 5)
    assert tableau.loc["pile ou face", "Brier"] == pytest.approx(0.25)
    # Le seuil choisi a posteriori classe parfaitement ces 5 cas (réussis : écart ≤ 1,0 s)
    a_posteriori = [m for m in tableau.index if "a posteriori" in m][0]
    assert tableau.loc[a_posteriori, "bonnes prédictions"] == 1.0


def test_calibration(resultats):
    cal = validation.calibration(resultats)
    assert cal["cas"].sum() == 5  # la ligne sans prédiction est ignorée
    assert (cal["annonce"].between(0, 1)).all() and (cal["observe"].between(0, 1)).all()
