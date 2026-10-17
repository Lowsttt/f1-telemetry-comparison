"""Tours synthétiques pour tester l'analyse sans réseau ni vraies données."""

import numpy as np
import pandas as pd
import pytest

from sources import TourPilote

LONGUEUR = 4000.0  # m
VIRAGES = {1: 1000.0, 2: 2500.0}  # numéro -> distance (m)


def tour_synthetique(pilote, vmin_virages=None, freinage_avant=150.0, frequence=4.0, decalage_t=0.1):
    """
    Tour de LONGUEUR m : 300 km/h en ligne droite, ralentissement en cloche à chaque virage.

    `vmin_virages` : {numéro: vitesse mini (km/h)}, 100 km/h par défaut.
    `freinage_avant` : distance (m) avant le virage où le pilote commence à freiner.
    La télémétrie est échantillonnée à `frequence` Hz, comme les vraies données.
    """
    vmin_virages = {n: 100.0 for n in VIRAGES} | (vmin_virages or {})
    d = np.linspace(0, LONGUEUR, 40001)
    v = np.full_like(d, 300.0)
    for numero, position in VIRAGES.items():
        creux = (300.0 - vmin_virages[numero]) * np.exp(-((d - position) / 120.0) ** 2)
        v = np.minimum(v, 300.0 - creux)

    # Temps de passage à chaque distance, puis échantillonnage régulier dans le temps
    t = np.concatenate([[0.0], np.cumsum(np.diff(d) / (v[1:] / 3.6))])
    duree = t[-1]
    echantillons = np.arange(decalage_t, duree, 1 / frequence)
    distance = np.interp(echantillons, t, d)
    vitesse = np.interp(echantillons, t, v)

    frein = np.zeros_like(distance, dtype=bool)
    for position in VIRAGES.values():
        frein |= (distance >= position - freinage_avant) & (distance < position)

    tel = pd.DataFrame({
        "Distance": distance,
        "Time": echantillons,
        "Speed": vitesse,
        "Throttle": np.where(frein, 0.0, 100.0),
        "Brake": frein,
        "nGear": np.clip((vitesse // 40).astype(int), 1, 8),
    })
    return TourPilote(pilote, duree, "SOFT", None, tel)


@pytest.fixture
def virages():
    return pd.DataFrame({"Number": list(VIRAGES), "Letter": "", "Distance": list(VIRAGES.values())})
