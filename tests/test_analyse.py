import numpy as np
import pandas as pd
import pytest
from conftest import LONGUEUR, VIRAGES, tour_synthetique

from analyse import (
    LONGUEUR_ZONE_MIN_M,
    analyser_zones,
    calcul_ecart,
    chrono_par_distance,
    conclusion,
    decouper_zones,
    harmoniser_distances,
    point_de_freinage,
    regrouper_virages,
    tableau_lisible,
)


def gain_signe(bilan, pilote_2):
    """Somme des gains, comptés positivement quand le pilote 2 gagne du temps."""
    return (bilan["gain"] * np.where(bilan["plus_rapide"] == pilote_2, 1, -1)).sum()


# --- Chrono et écart -----------------------------------------------------------

def test_chrono_borne_par_la_ligne():
    tour = tour_synthetique("AAA")
    distance, temps = chrono_par_distance(tour)
    assert (distance[0], temps[0]) == (0.0, 0.0)
    assert temps[-1] == pytest.approx(tour.temps)
    assert distance[-1] == pytest.approx(LONGUEUR, rel=0.01)
    assert np.all(np.diff(distance) > 0)


def test_ecart_nul_pour_deux_tours_identiques():
    _, ecart = calcul_ecart(tour_synthetique("AAA"), tour_synthetique("BBB"))
    assert np.abs(ecart).max() < 1e-3


def test_ecart_finit_sur_l_ecart_officiel():
    rapide = tour_synthetique("AAA")
    lent = tour_synthetique("BBB", vmin_virages={2: 80}, decalage_t=0.2)
    _, ecart = calcul_ecart(rapide, lent)
    assert ecart[0] == pytest.approx(0.0, abs=1e-6)
    assert ecart[-1] == pytest.approx(lent.temps - rapide.temps, abs=1e-6)
    # Le pilote 2 ne perd du temps qu'au virage 2 : écart ~nul jusqu'au virage 1
    distance, _ = calcul_ecart(rapide, lent)
    assert np.abs(ecart[distance < VIRAGES[1]]).max() < 0.005


def test_harmoniser_ramene_a_la_meme_longueur():
    tour_1 = tour_synthetique("AAA")
    tour_2 = tour_synthetique("BBB")
    tour_2.tel["Distance"] *= 1.01  # distance intégrée 1 % trop longue
    tour_2 = harmoniser_distances(tour_1, tour_2)
    # À quelques cm près : le dernier bout (après le dernier point) est extrapolé
    assert chrono_par_distance(tour_2)[0][-1] == pytest.approx(chrono_par_distance(tour_1)[0][-1], abs=0.5)


# --- Découpage en zones -----------------------------------------------------------

def test_regrouper_virages_rapproches():
    virages = pd.DataFrame({"Number": [3, 1, 2, 9], "Letter": ["", "", "", "a"],
                            "Distance": [600.0, 100.0, 150.0, 2000.0]})
    assert regrouper_virages(virages) == [("T1-T2", 100.0, 150.0), ("T3", 600.0, 600.0),
                                          ("T9a", 2000.0, 2000.0)]


def test_zones_couvrent_tout_le_tour(virages):
    tour_1, tour_2 = tour_synthetique("AAA"), tour_synthetique("BBB")
    zones = decouper_zones(tour_1, tour_2, virages)
    assert [z[0] for z in zones] == ["Départ → T1", "T1", "T2"]
    assert zones[0][1] == 0.0
    assert zones[-1][2] == pytest.approx(chrono_par_distance(tour_1)[0][-1])
    for precedente, suivante in zip(zones, zones[1:], strict=False):
        assert precedente[2] == suivante[1]  # pas de trou entre deux zones
    # Chaque zone commence avant son virage, au pic de vitesse
    assert zones[1][1] < VIRAGES[1] < zones[1][2]


def test_zone_trop_courte_fusionnee_avec_la_suivante():
    # Virage 1 à 30 m de la ligne : la zone « départ » disparaît, son temps va au virage 1
    tour_1, tour_2 = tour_synthetique("AAA"), tour_synthetique("BBB", vmin_virages={2: 80})
    virages = pd.DataFrame({"Number": [1, 2], "Letter": "", "Distance": [30.0, 2500.0]})
    zones = decouper_zones(tour_1, tour_2, virages)
    assert zones[0][1] == 0.0
    assert all(fin - debut >= LONGUEUR_ZONE_MIN_M for _, debut, fin, _ in zones)


# --- Bilan par zone ---------------------------------------------------------------

def test_gains_par_zone_retombent_sur_l_ecart(virages):
    rapide = tour_synthetique("AAA")
    lent = tour_synthetique("BBB", vmin_virages={2: 80})
    bilan = analyser_zones(rapide, lent, virages)
    assert gain_signe(bilan, "BBB") == pytest.approx(rapide.temps - lent.temps, abs=1e-6)
    pire = bilan.loc[bilan["gain"].idxmax()]
    assert (pire["zone"], pire["plus_rapide"]) == ("T2", "AAA")
    assert (pire["vmin_1"], pire["vmin_2"]) == pytest.approx((100, 80), abs=3)


def test_point_de_freinage_dans_le_bilan(virages):
    tardif = tour_synthetique("AAA", freinage_avant=100)
    precoce = tour_synthetique("BBB", freinage_avant=150)
    bilan = analyser_zones(tardif, precoce, virages).set_index("zone")
    assert bilan.loc["T1", "freinage_1"] == pytest.approx(VIRAGES[1] - 100, abs=15)
    assert bilan.loc["T1", "freinage_2"] == pytest.approx(VIRAGES[1] - 150, abs=15)
    assert pd.isna(bilan.loc["Départ → T1", "freinage_1"])


def test_freinage_ignore_frein_deja_enfonce_et_clignotement():
    #                    0      1      2     3      4      5     6     7
    frein = pd.Series([True, False, False, True, False, True, True, True])
    tel = pd.DataFrame({"Distance": np.arange(8) * 10.0, "Brake": frein})
    # Le frein enfoncé au point 0 (fin du virage précédent) et le « trou » d'un seul
    # point au 4 ne comptent pas : le freinage commence entre les points 2 et 3
    assert point_de_freinage(tel, 0, 80) == 25.0
    assert point_de_freinage(tel, 35, 80) is None  # trou d'un seul point : pas un nouveau freinage
    assert point_de_freinage(pd.DataFrame({"Distance": [0.0, 10.0], "Brake": [False, False]}),
                             0, 20) is None


def test_freinage_limite_aux_400_m_avant_le_virage():
    tel = pd.DataFrame({"Distance": np.arange(0, 1000, 10.0), "Brake": False})
    tel.loc[tel["Distance"].between(200, 250), "Brake"] = True  # 800 m avant le virage
    assert point_de_freinage(tel, 0, 1000) is None


def test_tableau_lisible_affiche_un_tiret_sans_freinage(virages):
    bilan = analyser_zones(tour_synthetique("AAA"), tour_synthetique("BBB"), virages)
    tableau = tableau_lisible(bilan, "AAA", "BBB")
    assert list(tableau.columns[:4]) == ["Zone", "Début (m)", "Plus rapide", "Gain (s)"]
    assert tableau.loc[0, "Frein AAA (m)"] == "—"


def test_conclusion_explique_le_gain(virages):
    rapide = tour_synthetique("AAA", vmin_virages={2: 110}, freinage_avant=100)
    lent = tour_synthetique("BBB", vmin_virages={2: 90}, freinage_avant=150)
    texte = conclusion(analyser_zones(rapide, lent, virages), rapide, lent)
    assert texte.startswith(f"AAA devance BBB de {lent.temps - rapide.temps:.3f} s.")
    assert "surtout T2" in texte
    assert "freine" in texte and "m plus tard" in texte
    assert "km/h plus vite au point le plus lent" in texte
