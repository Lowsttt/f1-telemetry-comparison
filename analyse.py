"""
Analyse de la comparaison : écart cumulé et bilan virage par virage.

Le tour est découpé en zones, une par virage (ou groupe de virages rapprochés comme
une chicane). Une zone commence au point de vitesse maximale avant le virage, donc
juste avant le freinage, et s'arrête au même point avant le virage suivant : elle
contient freinage, virage, sortie et la ligne droite qui suit.
"""

from dataclasses import replace

import numpy as np
import pandas as pd

NB_POINTS_ECART = 2000  # résolution de la grille commune pour la courbe d'écart
SEUIL_REGROUPEMENT_M = 110  # virages plus proches que ça = une seule zone (chicanes)
LONGUEUR_ZONE_MIN_M = 50
# En dessous de ces seuils, la différence n'est pas significative : à ~4 points par
# seconde, un point de freinage n'est connu qu'à une dizaine de mètres près
SEUIL_FREINAGE_M = 10
SEUIL_VITESSE_KMH = 2
DISTANCE_FREINAGE_MAX_M = 400  # aucun freinage de F1 ne commence plus loin du virage


def chrono_par_distance(tour):
    """
    Couples (distance, temps) du tour, bornés par la ligne de départ et d'arrivée.

    La télémétrie n'a que ~4 points par seconde : le premier et le dernier point
    tombent jusqu'à 0,25 s après le départ / avant l'arrivée. On ajoute donc le point
    (0 m, 0 s) et le point d'arrivée (temps officiel du tour), pour que l'écart
    cumulé finisse exactement sur l'écart officiel.
    """
    tel = tour.tel
    reste = tour.temps - tel["Time"].iloc[-1]
    distance_finale = tel["Distance"].iloc[-1] + tel["Speed"].iloc[-1] / 3.6 * reste
    distance = np.concatenate([[0.0], tel["Distance"], [distance_finale]])
    temps = np.concatenate([[0.0], tel["Time"], [tour.temps]])
    return distance, temps


def harmoniser_distances(tour_1, tour_2):
    """
    Ramène le tour 2 sur la longueur du tour 1.

    Les deux tours n'ont pas exactement la même longueur (trajectoire différente,
    distance obtenue en intégrant la vitesse) : sans ça, un écart de 30 m à
    l'arrivée fausserait la comparaison des points de freinage en fin de tour.
    """
    longueur_1 = chrono_par_distance(tour_1)[0][-1]
    longueur_2 = chrono_par_distance(tour_2)[0][-1]
    tel = tour_2.tel.copy()
    tel["Distance"] *= longueur_1 / longueur_2
    return replace(tour_2, tel=tel)


def calcul_ecart(tour_1, tour_2):
    """
    Écart cumulé du pilote 2 par rapport au pilote 1, en fonction de la distance.

    On interpole le chrono de chacun sur une grille de distance commune.
    Résultat > 0 : le pilote 2 est derrière (le pilote 1 a gagné du temps jusque-là).
    """
    d1, t1 = chrono_par_distance(tour_1)
    d2, t2 = chrono_par_distance(tour_2)
    longueur = d1[-1]
    d2 = d2 / d2[-1] * longueur

    grille = np.linspace(0, longueur, NB_POINTS_ECART)
    ecart = np.interp(grille, d2, t2) - np.interp(grille, d1, t1)
    return grille, ecart


def regrouper_virages(virages):
    """Groupes de virages rapprochés : [(nom, distance du 1er, distance du dernier)]."""
    groupes = []
    for _, v in virages.sort_values("Distance").iterrows():
        nom = f"T{v['Number']}{v['Letter']}"
        if groupes and v["Distance"] - groupes[-1][2] < SEUIL_REGROUPEMENT_M:
            premier = groupes[-1][0].split("-")[0]
            groupes[-1] = (f"{premier}-{nom}", groupes[-1][1], v["Distance"])
        else:
            groupes.append((nom, v["Distance"], v["Distance"]))
    return groupes


def decouper_zones(tour_1, tour_2, virages):
    """
    Zones du tour, coupées aux pics de vitesse : [(nom, début, fin, approche)].

    `approche` est l'intervalle entre le virage précédent et le 1er virage de la zone :
    c'est là qu'on cherche le freinage (le pic de vitesse moyen peut tomber quelques
    mètres après le début du freinage d'un des pilotes). None pour la zone « départ ».
    """
    longueur = chrono_par_distance(tour_1)[0][-1]
    grille = np.arange(0, longueur, 1.0)
    vitesse = (np.interp(grille, tour_1.tel["Distance"], tour_1.tel["Speed"])
               + np.interp(grille, tour_2.tel["Distance"], tour_2.tel["Speed"])) / 2

    groupes = regrouper_virages(virages)
    coupures, approches, fin_precedente = [], [], 0.0
    for _, debut_groupe, fin_groupe in groupes:
        entre = (grille >= fin_precedente) & (grille <= debut_groupe)
        if entre.any():
            # Si la vitesse plafonne (vitesse maxi tenue, valeurs arrondies au km/h), on
            # coupe au dernier point du plateau, c'est-à-dire juste avant le freinage
            v_entre = vitesse[entre]
            coupures.append(grille[entre][len(v_entre) - 1 - np.argmax(v_entre[::-1])])
        else:
            coupures.append(debut_groupe)
        approches.append((fin_precedente, debut_groupe))
        fin_precedente = fin_groupe

    noms = [f"Départ → {groupes[0][0]}"] + [g[0] for g in groupes]
    bornes = [0.0] + coupures + [longueur]
    zones = list(zip(noms, bornes[:-1], bornes[1:], [None] + approches, strict=True))

    # Zone trop courte (ligne juste avant le 1er virage, virages enchaînés) : on la
    # fusionne avec la suivante plutôt que de la supprimer, pour ne perdre aucun temps
    fusionnees = []
    for nom, debut, fin, approche in zones:
        if fusionnees and fusionnees[-1][2] - fusionnees[-1][1] < LONGUEUR_ZONE_MIN_M:
            nom_court, debut, _, approche_courte = fusionnees.pop()
            if approche_courte is not None:  # la zone « départ » disparaît sans laisser de nom
                nom = f"{nom_court.split('-')[0]}-{nom.split('-')[-1]}"
                approche = approche_courte
        fusionnees.append((nom, debut, fin, approche))
    if len(fusionnees) > 1 and fusionnees[-1][2] - fusionnees[-1][1] < LONGUEUR_ZONE_MIN_M:
        dernier = fusionnees.pop()
        fusionnees[-1] = fusionnees[-1][:2] + (dernier[2],) + fusionnees[-1][3:]
    return fusionnees


def point_de_freinage(tel, debut, fin):
    """
    Début du freinage à l'approche d'un virage situé à `fin`, ou None sans freinage.

    On cherche le 1er passage « pas de frein -> frein » entre `debut` et `fin`, dans les
    DISTANCE_FREINAGE_MAX_M avant le virage : un frein encore enfoncé en sortie du
    virage précédent n'est pas un nouveau freinage. Il faut au moins 2 points sans
    frein juste avant (0,5 s) : le signal « clignote » parfois pendant un freinage.
    Le point retenu est le milieu entre le dernier point sans frein et le premier avec.
    """
    frein = tel["Brake"].astype(bool).to_numpy()
    distance = tel["Distance"].to_numpy()
    debuts = np.flatnonzero(frein[2:] & ~frein[1:-1] & ~frein[:-2]) + 2
    debut = max(debut, fin - DISTANCE_FREINAGE_MAX_M)
    debuts = debuts[(distance[debuts] >= debut) & (distance[debuts] < fin)]
    if len(debuts) == 0:
        return None
    i = debuts[0]
    return (distance[i - 1] + distance[i]) / 2


def analyser_zones(tour_1, tour_2, virages):
    """Bilan par zone : temps gagné, vitesses mini/maxi et points de freinage."""
    distance, ecart = calcul_ecart(tour_1, tour_2)
    lignes = []
    for nom, debut, fin, approche in decouper_zones(tour_1, tour_2, virages):
        # L'écart augmente dans la zone -> le pilote 2 y perd du temps
        gain_1 = np.interp(fin, distance, ecart) - np.interp(debut, distance, ecart)
        ligne = {
            "zone": nom, "debut": debut, "fin": fin,
            # Position du 1er virage de la zone (milieu de zone pour la zone « départ »)
            "repere": approche[1] if approche else (debut + fin) / 2,
            "plus_rapide": tour_1.pilote if gain_1 > 0 else tour_2.pilote,
            "gain": abs(gain_1),
        }
        for i, tour in ((1, tour_1), (2, tour_2)):
            vitesse = tour.tel["Speed"][(tour.tel["Distance"] >= debut)
                                        & (tour.tel["Distance"] < fin)].reset_index(drop=True)
            ligne[f"vmin_{i}"] = vitesse.min()
            # Vitesse max après le point le plus lent : sortie de virage + ligne droite.
            # Avant, c'est la vitesse de la ligne droite précédente, déjà comptée.
            ligne[f"vmax_{i}"] = vitesse[vitesse.idxmin():].max()
            ligne[f"freinage_{i}"] = point_de_freinage(tour.tel, *approche) if approche else None
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def tableau_lisible(bilan, p1, p2):
    """Version du bilan pour l'affichage : arrondis et noms de colonnes en clair."""
    def arrondi(x):
        return "—" if x is None or pd.isna(x) else f"{x:.0f}"

    return pd.DataFrame({
        "Zone": bilan["zone"],
        "Début (m)": bilan["debut"].map(arrondi),
        "Plus rapide": bilan["plus_rapide"],
        "Gain (s)": bilan["gain"].map(lambda g: f"{g:.3f}"),
        f"Vmin {p1}": bilan["vmin_1"].map(arrondi),
        f"Vmin {p2}": bilan["vmin_2"].map(arrondi),
        f"Vmax {p1}": bilan["vmax_1"].map(arrondi),
        f"Vmax {p2}": bilan["vmax_2"].map(arrondi),
        f"Frein {p1} (m)": bilan["freinage_1"].map(arrondi),
        f"Frein {p2} (m)": bilan["freinage_2"].map(arrondi),
    })


def _explications(ligne, i):
    """Raisons chiffrées du gain du pilote i (1 ou 2) dans une zone."""
    autre = 3 - i
    raisons = []
    frein, frein_autre = ligne[f"freinage_{i}"], ligne[f"freinage_{autre}"]
    if pd.notna(frein) and pd.notna(frein_autre) and frein - frein_autre >= SEUIL_FREINAGE_M:
        raisons.append(f"freine {frein - frein_autre:.0f} m plus tard")
    ecart_vmin = ligne[f"vmin_{i}"] - ligne[f"vmin_{autre}"]
    if ecart_vmin >= SEUIL_VITESSE_KMH:
        raisons.append(f"passe {ecart_vmin:.0f} km/h plus vite au point le plus lent")
    ecart_vmax = ligne[f"vmax_{i}"] - ligne[f"vmax_{autre}"]
    if ecart_vmax >= SEUIL_VITESSE_KMH:
        raisons.append(f"atteint {ecart_vmax:.0f} km/h de plus en fin de ligne droite")
    return raisons


def conclusion(bilan, tour_1, tour_2):
    """Résumé en quelques phrases : qui est devant, et où se fait la différence."""
    gap = tour_2.temps - tour_1.temps
    devant, derriere = (tour_2, tour_1) if gap < 0 else (tour_1, tour_2)
    phrases = [f"{devant.pilote} devance {derriere.pilote} de {abs(gap):.3f} s."]

    for tour in (devant, derriere):
        zones = bilan[bilan["plus_rapide"] == tour.pilote]
        if zones.empty:
            continue
        meilleure = zones.loc[zones["gain"].idxmax()]
        i = 1 if tour is tour_1 else 2
        raisons = _explications(meilleure, i)
        detail = f" : il {', '.join(raisons)}" if raisons else ""
        phrases.append(
            f"{tour.pilote} est plus rapide dans {len(zones)} zone(s) sur {len(bilan)}, "
            f"surtout {meilleure['zone']} (+{meilleure['gain']:.3f} s){detail}."
        )
    return " ".join(phrases)
