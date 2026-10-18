"""Tests de l'aide à la stratégie sur une course synthétique aux paramètres connus (sans réseau)."""

import numpy as np
import pandas as pd
import pytest

import undercut
from course import Course
from usure import ajuster_usure

NB_TOURS = 40
CARBURANT = -0.05
ECART_MEDIUM = -0.40
USURE = {"MEDIUM": 0.08, "HARD": 0.03}
COUT_ENTREE, COUT_SORTIE = 4.0, 21.0  # temps perdu sur le tour d'entrée et le tour de sortie
DEBUT = pd.Timestamp("2024-09-01 13:00", tz="UTC")


def temps_tour(niveau, tour, compose, age):
    return niveau + CARBURANT * tour + (ECART_MEDIUM if compose == "MEDIUM" else 0.0) + USURE[compose] * age


def course_synthetique(graine=3, bruit=0.1, avec_arrets=True):
    """10 pilotes, un arrêt chacun (tours 14 à 23) ; médium puis dur, ou dur puis médium."""
    rng = np.random.default_rng(graine)
    lignes = []
    for i in range(10):
        pilote, equipe, niveau = f"P{i:02d}", ["Rouge", "Bleu", "Vert", "Jaune", "Gris"][i // 2], 90 + 0.2 * i
        arret = 14 + i
        ordre = ["MEDIUM", "HARD"] if i % 2 == 0 else ["HARD", "MEDIUM"]
        horloge = DEBUT
        for tour in range(1, NB_TOURS + 1):
            relais = 1 if tour <= arret else 2
            compose = ordre[relais - 1]
            age = float(tour - 1 if relais == 1 else tour - arret - 1)
            temps = temps_tour(niveau, tour, compose, age) + rng.normal(0, bruit)
            entree, sortie = avec_arrets and tour == arret, avec_arrets and tour == arret + 1
            temps += COUT_ENTREE * entree + COUT_SORTIE * sortie
            debut, horloge = horloge, horloge + pd.Timedelta(temps, unit="s")
            lignes.append({"pilote": pilote, "equipe": equipe, "tour": tour, "temps": temps, "debut": debut,
                           "fin": horloge, "compose": compose, "relais": relais, "age_pneu": age,
                           "entree_stand": entree, "sortie_stand": sortie,
                           "duree_stand": 22.0 if entree else np.nan,
                           "etat_piste": "VERT", "propre": tour > 1 and not entree and not sortie})
    tours = pd.DataFrame(lignes)
    perte = {"normal": 24.0, "sc": 15.2, "vsc": 17.4}
    neutralisations = pd.DataFrame(columns=["type", "debut", "fin"])
    return Course("Test Grand Prix 2024 - Race", 2024, 1, 1, perte, neutralisations, tours)


@pytest.fixture(scope="module")
def course():
    return course_synthetique()


@pytest.fixture(scope="module")
def modele(course):
    return ajuster_usure(course.tours, nb_bootstrap=60)


@pytest.fixture(scope="module")
def cout(course, modele):
    return undercut.mesurer_cout_arret(course, modele)


# --- Coût d'un arrêt ---------------------------------------------------------------------

def test_cout_arret_mesure(cout):
    assert cout.moyen == pytest.approx(COUT_ENTREE + COUT_SORTIE, abs=0.5)
    assert cout.source == "mesuré sur 10 arrêts"
    assert cout.facteurs == pytest.approx({"SC": 15.2 / 24, "VSC": 17.4 / 24})
    assert len(cout.mesures) == 10


def test_cout_arret_multiviewer_sans_arrets_mesurables(course, modele):
    sans = course_synthetique(avec_arrets=False)
    cout = undercut.mesurer_cout_arret(sans, modele)
    assert (cout.moyen, cout.source) == (24.0, "MultiViewer")


def test_arret_sous_neutralisation_non_mesure(modele):
    c = course_synthetique()
    c.tours.loc[c.tours["entree_stand"] & (c.tours["pilote"] == "P00"), "etat_piste"] = "SC"
    assert undercut.mesurer_cout_arret(c, modele).source == "mesuré sur 9 arrêts"


# --- Tours hypothétiques -----------------------------------------------------------------

def test_tours_hypothetiques():
    etat = {"pilote": "P00", "equipe": "Rouge", "compose": "MEDIUM", "age": 9.0}
    tours = undercut.tours_hypothetiques(etat, depuis=10, jusqua=14, arret=12, nouveau="HARD")
    assert tours["tour"].tolist() == [11, 12, 13, 14]
    assert tours["compose"].tolist() == ["MEDIUM", "MEDIUM", "HARD", "HARD"]
    assert tours["age_pneu"].tolist() == [10.0, 11.0, 0.0, 1.0]  # tour de sortie : pneu neuf


def test_temps_simules(modele):
    etat = {"pilote": "P00", "equipe": "Rouge", "compose": "MEDIUM", "age": 0.0}
    tours = undercut.tours_hypothetiques(etat, 0, 3)
    temps = undercut.temps_simules(modele, tours)
    assert temps.shape == (3, len(modele.tirages))
    assert np.median(temps[0]) == pytest.approx(temps_tour(90, 1, "MEDIUM", 1), abs=0.15)
    with pytest.raises(ValueError, match="jamais roulé"):
        undercut.temps_simules(modele, tours.assign(compose="SOFT"))


# --- Duel undercut / overcut -------------------------------------------------------------

def test_duel_gain_croit_avec_le_retard_de_reaction(course, modele, cout):
    # P02 (médium, s'arrête au tour 16) poursuit P00 (médium, s'arrête au tour 14 en vrai) :
    # on se place à la fin du tour 10, quand les deux sont encore sur leur 1er train
    resultats = undercut.analyser_undercut(course, modele, cout, "P02", "P00", 10, reactions=(1, 2, 3))
    gains = [resultats[k].gain for k in (1, 2, 3)]
    assert gains[0] < gains[1] < gains[2]
    probas = [resultats[k].probabilite_devant for k in (1, 2, 3)]
    assert probas == sorted(probas)
    # Réaction au tour suivant : P02 s'arrête au tour 11, P00 au tour 12, comparaison fin du tour 13.
    # Le gain compte aussi l'écart de rythme : P02 (niveau 90,4) est 0,4 s/tour plus lent que P00.
    # Le coût des deux arrêts est le même et s'annule.
    poursuivant = (temps_tour(90.4, 11, "MEDIUM", 10) + temps_tour(90.4, 12, "HARD", 0)
                   + temps_tour(90.4, 13, "HARD", 1))
    devant = (temps_tour(90.0, 11, "MEDIUM", 10) + temps_tour(90.0, 12, "MEDIUM", 11)
              + temps_tour(90.0, 13, "HARD", 0))
    assert gains[0] == pytest.approx(devant - poursuivant, abs=0.3)


def test_duel_ecart_de_depart_et_cas_extremes(course, modele, cout):
    r = undercut.simuler_duel(course, modele, cout, "P02", "P00", 10, 11, 12, "HARD", "HARD")
    fins = course.tours.set_index(["pilote", "tour"])["fin"]
    assert r.ecart_depart == pytest.approx((fins[("P02", 10)] - fins[("P00", 10)]).total_seconds())
    assert r.ecart_depart > 0
    loin = undercut.simuler_duel(course, modele, cout, "P09", "P00", 10, 11, 12, "HARD", "HARD")
    assert loin.probabilite_devant < 0.05  # P09 bien trop loin derrière
    bas, haut = r.intervalle()
    assert bas < r.ecart_final < haut


def test_duel_refuse_un_arret_deja_passe(course, modele, cout):
    with pytest.raises(ValueError, match="après le tour de décision"):
        undercut.simuler_duel(course, modele, cout, "P02", "P00", 10, 10, 12, "HARD", "HARD")
    with pytest.raises(ValueError, match="n'a pas bouclé"):
        undercut.etat_pilote(course, "P02", 99)


# --- Fenêtre d'arrêt -------------------------------------------------------------------

def test_strategies_possibles_respectent_les_regles():
    strategies = list(undercut.strategies_possibles(0, 30, "MEDIUM", set(), ["MEDIUM", "HARD"],
                                                    max_arrets=2, ages_max={"MEDIUM": 20, "HARD": 40}))
    assert [] not in strategies  # 0 arrêt : un seul composé, interdit
    for s in strategies:
        bornes = [0, *[t for t, _ in s], 30]
        assert all(b - a >= undercut.RELAIS_MIN for a, b in zip(bornes[1:], bornes[2:], strict=False))
        assert {"MEDIUM"} | {c for _, c in s} == {"MEDIUM", "HARD"}
        assert bornes[1] <= 20  # le médium de départ ne dépasse pas 20 tours
    assert [(15, "HARD")] in strategies


def test_fenetre_retrouve_l_optimum(course, modele, cout):
    tableau, tirages = undercut.evaluer_strategies(course, modele, cout, "P00")
    meilleure = tableau.iloc[0]
    assert meilleure["nb_arrets"] == 1 and meilleure["arrets"][0][1] == "HARD"
    # Optimum calculé directement avec les vrais paramètres (médium de départ, puis dur)
    def total(arret):
        return (sum(temps_tour(0, 0, "MEDIUM", a) for a in range(arret))
                + sum(temps_tour(0, 0, "HARD", a) for a in range(NB_TOURS - arret)))
    optimum = min(range(5, NB_TOURS - 4), key=total)
    assert abs(meilleure["arrets"][0][0] - optimum) <= 2
    assert tableau["ecart"].iloc[0] == 0 and (tableau["ecart"] >= 0).all()
    assert len(tirages) == len(tableau)
    probas = undercut.probabilite_par_nb_arrets(tableau, tirages)
    assert sum(probas.values()) == pytest.approx(1.0) and probas[1] > 0.9


def test_voiture_de_securite_rend_l_arret_moins_cher(course, modele, cout):
    sans, _ = undercut.evaluer_strategies(course, modele, cout, "P00", depuis=10)
    avec, _ = undercut.evaluer_strategies(course, modele, cout, "P00", depuis=10,
                                          sc={"tour": 14, "type": "SC"})
    plan = [(14, "HARD")]  # 26 tours en durs : dans la limite d'âge observée
    temps_sans = sans.loc[sans["arrets"].map(lambda a: a == plan), "temps"].iloc[0]
    temps_avec = avec.loc[avec["arrets"].map(lambda a: a == plan), "temps"].iloc[0]
    assert temps_sans - temps_avec == pytest.approx(cout.moyen * (1 - cout.facteurs["SC"]), abs=1e-6)


def test_strategie_reelle_et_description(course):
    assert undercut.strategie_reelle(course, "P00") == [(14, "HARD")]
    assert undercut.strategie_reelle(course, "P00", depuis=20) == []
    assert undercut.decrire([(14, "HARD"), (30, "MEDIUM")]) == "tour 14 → HARD, tour 30 → MEDIUM"
    assert undercut.decrire([]) == "aucun arrêt"


def test_composes_recommandables(course, modele):
    assert undercut.composes_recommandables(modele) == ["MEDIUM", "HARD"]
    # Un seul relais en médium : trop peu pour le recommander
    tours = course.tours.copy()
    tours.loc[(tours["compose"] == "MEDIUM") & (tours["pilote"] != "P00"), "propre"] = False
    peu = ajuster_usure(tours, nb_bootstrap=0)
    assert undercut.composes_recommandables(peu) == ["HARD"]
