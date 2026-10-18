"""Tests du modèle d'usure sur une course synthétique aux paramètres connus (sans réseau)."""

import numpy as np
import pandas as pd
import pytest

import usure

NB_TOURS = 50
CARBURANT = -0.05  # s/tour
ECART_MEDIUM = -0.40  # médium neuf plus rapide que le dur neuf
USURE = {"MEDIUM": 0.08, "HARD": 0.03}  # s par tour d'âge
USURE2 = {"MEDIUM": 0.002, "HARD": 0.0}  # utilisé seulement pour la forme quadratique
ECART_ECURIE = {"Rouge": {"HARD": 0.03}}  # Rouge use davantage ses durs


def course_synthetique(graine=1, bruit=0.15, aberrants=0, quadratique=False, ecuries=None):
    """
    10 pilotes (5 écuries), un arrêt chacun ; la moitié part en médium, l'autre en dur.
    Le temps au tour suit exactement le modèle, plus un bruit gaussien.
    """
    ecuries = ecuries or ["Rouge", "Bleu", "Vert", "Jaune", "Gris"]
    rng = np.random.default_rng(graine)
    lignes = []
    for i in range(10):
        equipe, pilote = ecuries[i // 2], f"P{i:02d}"
        niveau = 90 + 0.3 * i
        arret = 18 + 2 * i
        ordre = ["MEDIUM", "HARD"] if i % 2 == 0 else ["HARD", "MEDIUM"]
        for tour in range(1, NB_TOURS + 1):
            relais = 1 if tour <= arret else 2
            compose = ordre[relais - 1]
            age = tour - 1 if relais == 1 else tour - arret - 1
            temps = (niveau + CARBURANT * tour + (ECART_MEDIUM if compose == "MEDIUM" else 0.0)
                     + (USURE[compose] + ECART_ECURIE.get(equipe, {}).get(compose, 0.0)) * age
                     + (USURE2[compose] * age ** 2 if quadratique else 0.0)
                     + rng.normal(0, bruit))
            lignes.append({"pilote": pilote, "equipe": equipe, "tour": tour, "temps": temps,
                           "compose": compose, "relais": relais, "age_pneu": float(age), "propre": True})
    tours = pd.DataFrame(lignes)
    if aberrants:  # tours très lents (trafic) que le filtre des 107 % n'aurait pas vus
        indices = rng.choice(len(tours), aberrants, replace=False)
        tours.loc[indices, "temps"] += 4.0
    return tours


@pytest.fixture(scope="module")
def modele():
    return usure.ajuster_usure(course_synthetique(), nb_bootstrap=100)


def test_retrouve_les_parametres(modele):
    assert modele.reference == "HARD"
    assert modele.coefficient("carburant") == pytest.approx(CARBURANT, abs=0.005)
    assert modele.coefficient("compose:MEDIUM") == pytest.approx(ECART_MEDIUM, abs=0.08)
    assert modele.pente("MEDIUM") == pytest.approx(USURE["MEDIUM"], abs=0.01)
    assert modele.pente("HARD") == pytest.approx(USURE["HARD"], abs=0.01)


def test_intervalles_contiennent_les_vraies_valeurs(modele):
    for nom, vraie in [("carburant", CARBURANT), ("compose:MEDIUM", ECART_MEDIUM),
                       ("usure:MEDIUM", USURE["MEDIUM"])]:
        bas, haut = modele.intervalle(nom)
        assert bas < vraie < haut, nom
        assert haut - bas < 0.5  # assez précis pour servir


def test_ecart_par_ecurie_rappele_vers_la_moyenne(modele):
    rouge, autres = modele.pente("HARD", "Rouge"), modele.pente("HARD", "Bleu")
    assert rouge > autres + 0.01  # Rouge use bien plus ses durs
    # Le rappel vers 0 tire l'écart vers la moyenne : il est sous-estimé, pas exagéré
    assert rouge - autres < ECART_ECURIE["Rouge"]["HARD"] + 0.005


def test_sans_ecart_par_ecurie():
    m = usure.ajuster_usure(course_synthetique(), par_ecurie=False, nb_bootstrap=0)
    assert not any(c.startswith("usure_ecurie:") for c in m.colonnes)
    assert m.pente("HARD", "Rouge") == m.pente("HARD")


def test_robuste_aux_tours_aberrants():
    tours = course_synthetique(aberrants=40)
    robuste = usure.ajuster_usure(tours, nb_bootstrap=0)
    assert robuste.pente("MEDIUM") == pytest.approx(USURE["MEDIUM"], abs=0.015)
    assert robuste.coefficient("carburant") == pytest.approx(CARBURANT, abs=0.01)


def test_forme_quadratique():
    tours = course_synthetique(quadratique=True)
    m = usure.ajuster_usure(tours, forme="quadratique", nb_bootstrap=0)
    assert m.coefficient("usure2:MEDIUM") == pytest.approx(USURE2["MEDIUM"], abs=0.001)
    # Perte de temps après 20 tours en médium : 0,08 × 20 + 0,002 × 400 = 2,4 s
    assert m.usure("MEDIUM", 20) == pytest.approx(2.4, abs=0.2)
    with pytest.raises(ValueError, match="forme inconnue"):
        usure.ajuster_usure(tours, forme="cubique")


def test_compose_trop_peu_roule_ecarte():
    tours = course_synthetique()
    extra = tours[tours["pilote"] == "P00"].head(5).assign(compose="SOFT")
    m = usure.ajuster_usure(pd.concat([tours, extra], ignore_index=True), nb_bootstrap=0)
    assert m.ecartes == {"SOFT": "seulement 5 tours propres"}
    assert "SOFT" not in m.composes


def test_compose_non_relie_ecarte():
    # Avant les arrêts, chaque pilote n'a roulé qu'un composé : l'écart médium / dur neuf ne
    # peut pas être séparé du rythme des pilotes. On garde le composé le plus roulé.
    tours = course_synthetique()
    avant_arrets = tours[tours["tour"] <= 15]
    m = usure.ajuster_usure(avant_arrets, nb_bootstrap=0)
    assert m.composes == ["HARD"] or m.composes == ["MEDIUM"]
    ecarte = ({"MEDIUM", "HARD"} - set(m.composes)).pop()
    assert m.ecartes == {ecarte: "aucun pilote ne l'a roulé avec un autre composé"}


def test_tours_non_propres_et_pluie_ignores():
    tours = course_synthetique()
    tours.loc[tours["tour"] > 40, "propre"] = False
    tours.loc[tours["tour"] == 5, "compose"] = "INTERMEDIATE"
    m = usure.ajuster_usure(tours, nb_bootstrap=0)
    assert m.donnees["tour"].max() == 40
    assert (m.donnees["compose"] != "INTERMEDIATE").all()
    with pytest.raises(ValueError, match="Pas assez"):
        usure.ajuster_usure(tours.assign(propre=False), nb_bootstrap=0)


def test_predire(modele):
    tour = pd.DataFrame([{"pilote": "P02", "equipe": "Bleu", "tour": 10, "compose": "MEDIUM",
                          "age_pneu": 9.0}])
    attendu = 90 + 0.3 * 2 + CARBURANT * 10 + ECART_MEDIUM + USURE["MEDIUM"] * 9
    assert modele.predire(tour)[0] == pytest.approx(attendu, abs=0.2)


def test_controle_bat_le_rythme_constant_quand_l_usure_domine():
    # Usure forte et peu de bruit : un rythme constant ne peut pas suivre la dégradation
    controle = usure.controler(course_synthetique(bruit=0.05))
    assert controle["tours"] > 0 and controle["relais"] > 0
    assert controle["erreur_modele"] < controle["erreur_naive"]
    assert abs(controle["biais_modele"]) < 0.1


def test_regression_robuste_avec_rappel():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 2))
    y = x @ np.array([1.0, 2.0]) + rng.normal(0, 0.1, 200)
    libre = usure.regression_robuste(x, y)
    rappele = usure.regression_robuste(x, y, penalites=np.array([0.0, 1e5]))
    assert libre == pytest.approx([1.0, 2.0], abs=0.05)
    assert abs(rappele[1]) < 0.1 and rappele[0] == pytest.approx(1.0, abs=0.2)


def test_tableaux(modele):
    tableau = usure.tableau_usure(modele)
    assert tableau["compose"].tolist() == ["MEDIUM", "HARD"]
    assert tableau.loc[tableau["compose"] == "HARD", "ecart_neuf"].iloc[0] == 0.0
    ecuries = usure.tableau_ecuries(modele)
    assert set(ecuries.columns) == {"MEDIUM", "HARD"} and len(ecuries) == 5
    assert ecuries.index[-1] == "Rouge"  # trié par usure du dur : Rouge en dernier
