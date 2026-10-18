"""
Aide à la stratégie des arrêts aux stands, à partir du modèle d'usure (usure.py).

- coût réel d'un arrêt : temps perdu sur le tour d'entrée + le tour de sortie, mesuré sur
  les arrêts de la course (voie des stands, arrêt, pneus froids) ;
- duel undercut / overcut : « au tour N, si A s'arrête au tour X et B au tour Y, qui est
  devant une fois les deux arrêts faits ? », avec une probabilité (Monte-Carlo sur les
  marges d'erreur du modèle, l'aléa de chaque tour et celui de l'arrêt) ;
- fenêtre d'arrêt : temps de course de chaque stratégie à 1 ou 2 arrêts pour un pilote,
  la meilleure et les tours à moins d'une seconde d'elle ; option voiture de sécurité.

Limites de cette version : pas de trafic à la sortie des stands, pas de difficulté de
dépassement, pas d'écart de rythme lié au groupe (aspiration, air sale).

Utilisation :
    python undercut.py 2024 Monza duel 14 NOR LEC           # NOR (derrière) tente l'undercut
    python undercut.py 2024 Monza fenetre LEC
    python undercut.py 2024 Monza fenetre LEC --depuis 20 --sc 20
"""

import argparse
import itertools
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from usure import COMPOSES_SECS, COULEURS_COMPOSES, ajuster_usure, matrice

ARRETS_MIN_MESURE = 5  # en dessous, on garde le temps perdu donné par MultiViewer
RELAIS_MIN = 5  # tours minimum entre deux arrêts dans la fenêtre d'arrêt
RELAIS_MIN_COMPOSE = 5  # relais propres minimum pour qu'un composé soit recommandé
MARGE_FENETRE_S = 1.0  # stratégies « équivalentes » : à moins d'une seconde de la meilleure
NB_BRUIT = 20  # tirages d'aléa par jeu de coefficients du modèle


# --- Coût d'un arrêt ---------------------------------------------------------------------

@dataclass
class CoutArret:
    moyen: float  # temps perdu (s) sur tour d'entrée + tour de sortie, sous drapeau vert
    ecart_type: float  # dispersion d'un arrêt à l'autre (arrêt raté, trafic dans la voie...)
    source: str  # "mesuré sur N arrêts" ou "MultiViewer"
    mesures: pd.DataFrame  # un arrêt par ligne (vide si source MultiViewer)
    facteurs: dict  # coût relatif sous neutralisation : {"SC": 0.63, "VSC": 0.72}


def mesurer_cout_arret(course, modele):
    """
    Temps perdu par arrêt, mesuré sur les arrêts sous drapeau vert de la course.

    Pour chaque arrêt : (tour d'entrée + tour de sortie) réels moins ce que le modèle prévoit
    pour ces deux tours sur pneus usés puis neufs. Médiane sur les arrêts (robuste aux arrêts
    ratés). Avec trop peu d'arrêts mesurables, on prend la valeur de MultiViewer.
    """
    tours = course.tours
    lignes = []
    for t in tours[tours["entree_stand"]].itertuples():
        paire = tours[(tours["pilote"] == t.pilote) & tours["tour"].isin([t.tour, t.tour + 1])]
        if len(paire) < 2:
            continue  # abandon dans les stands
        if (paire["etat_piste"] != "VERT").any() or paire["temps"].isna().any() \
                or not paire["compose"].isin(modele.composes).all() or t.pilote not in modele.pilotes:
            continue
        prevu = modele.predire(paire)
        lignes.append({"pilote": t.pilote, "tour": t.tour, "duree_stand": t.duree_stand,
                       "cout": float(paire["temps"].sum() - prevu.sum())})
    mesures = pd.DataFrame(lignes, columns=["pilote", "tour", "duree_stand", "cout"])

    normal = course.perte_stand.get("normal")
    facteurs = {cle.upper(): course.perte_stand[cle] / normal
                for cle in ("sc", "vsc") if normal and course.perte_stand.get(cle)}
    if len(mesures) >= ARRETS_MIN_MESURE:
        couts = mesures["cout"].to_numpy()
        ecart = 1.4826 * np.median(np.abs(couts - np.median(couts)))
        return CoutArret(float(np.median(couts)), float(ecart), f"mesuré sur {len(mesures)} arrêts",
                         mesures, facteurs)
    if normal is None:
        raise ValueError("Ni arrêts mesurables ni temps perdu MultiViewer pour cette course.")
    return CoutArret(normal, 1.0, "MultiViewer", mesures, facteurs)


# --- Prédictions sur des tours hypothétiques ------------------------------------------

def etat_pilote(course, pilote, tour):
    """Pneu (composé, âge) et heure de passage d'un pilote à la fin d'un tour."""
    ligne = course.tours[(course.tours["pilote"] == pilote) & (course.tours["tour"] == tour)]
    if ligne.empty:
        raise ValueError(f"{pilote} n'a pas bouclé le tour {tour}.")
    ligne = ligne.iloc[0]
    return {"pilote": pilote, "equipe": ligne["equipe"], "compose": ligne["compose"],
            "age": float(ligne["age_pneu"]), "fin": ligne["fin"]}


def tours_hypothetiques(etat, depuis, jusqua, arret=None, nouveau=None):
    """
    Tours depuis+1 à jusqua d'un pilote : pneu actuel qui vieillit, puis, s'il s'arrête
    (tour d'entrée `arret`), pneu `nouveau` neuf à partir du tour de sortie.
    """
    lignes = []
    for tour in range(depuis + 1, jusqua + 1):
        if arret is not None and tour > arret:
            compose, age = nouveau, float(tour - arret - 1)
        else:
            compose, age = etat["compose"], etat["age"] + tour - depuis
        lignes.append({"pilote": etat["pilote"], "equipe": etat["equipe"], "tour": tour,
                       "compose": compose, "age_pneu": age})
    return pd.DataFrame(lignes)


def temps_simules(modele, tours):
    """Temps de chaque tour (lignes) pour chaque jeu de coefficients du bootstrap (colonnes)."""
    inconnus = set(tours["compose"]) - set(modele.composes)
    if inconnus:
        raise ValueError(f"Composé jamais roulé proprement dans cette course : {', '.join(inconnus)}")
    return matrice(tours, modele.colonnes) @ modele.tirages.T


# --- Duel undercut / overcut ---------------------------------------------------------

@dataclass
class ResultatDuel:
    ecart_depart: float  # poursuivant derrière le pilote devant (s, > 0) à la fin du tour de décision
    echantillons: np.ndarray  # écart final simulé (> 0 : le poursuivant est toujours derrière)

    @property
    def probabilite_devant(self):
        return float(np.mean(self.echantillons < 0))

    @property
    def ecart_final(self):
        return float(np.median(self.echantillons))

    def intervalle(self, niveau=0.9):
        a = (1 - niveau) / 2 * 100
        return tuple(np.percentile(self.echantillons, [a, 100 - a]))

    @property
    def gain(self):
        """Temps repris (s) par le poursuivant grâce aux arrêts décalés (médiane)."""
        return self.ecart_depart - self.ecart_final


def simuler_duel(course, modele, cout, poursuivant, devant, tour, arret_poursuivant, arret_devant,
                 nouveau_poursuivant, nouveau_devant, nb_bruit=NB_BRUIT, graine=0):
    """
    Écart entre deux pilotes une fois leurs deux arrêts faits.

    `tour` : tour de décision (on connaît la course jusqu'à la fin de ce tour).
    `arret_*` : tour d'entrée aux stands de chacun (> tour). On compare à la fin du dernier
    des deux tours de sortie, quand les deux pilotes sont repartis sur pneus neufs.
    """
    if min(arret_poursuivant, arret_devant) <= tour:
        raise ValueError("Les arrêts doivent avoir lieu après le tour de décision.")
    etat_p, etat_d = etat_pilote(course, poursuivant, tour), etat_pilote(course, devant, tour)
    ecart_depart = (etat_p["fin"] - etat_d["fin"]).total_seconds()
    fin = max(arret_poursuivant, arret_devant) + 1

    temps_p = temps_simules(modele, tours_hypothetiques(etat_p, tour, fin, arret_poursuivant,
                                                        nouveau_poursuivant)).sum(axis=0)
    temps_d = temps_simules(modele, tours_hypothetiques(etat_d, tour, fin, arret_devant,
                                                        nouveau_devant)).sum(axis=0)
    # Aléa : chaque tour (indépendant d'un tour à l'autre) et chaque arrêt
    rng = np.random.default_rng(graine)
    taille = (nb_bruit, len(temps_p))
    alea_tours = modele.ecart_type_residuel() * np.sqrt(2 * (fin - tour))
    ecart = (ecart_depart + temps_p - temps_d
             + rng.normal(0, alea_tours, taille) + rng.normal(0, cout.ecart_type * np.sqrt(2), taille))
    return ResultatDuel(ecart_depart, ecart.ravel())


def analyser_undercut(course, modele, cout, poursuivant, devant, tour, nouveau_poursuivant=None,
                      nouveau_devant=None, reactions=(1, 2, 3)):
    """
    Le poursuivant s'arrête au tour suivant le tour de décision ; le pilote devant réagit
    1, 2 ou 3 tours plus tard. Par défaut, chacun repart sur le composé le plus dur utilisé
    dans la course (le cas le plus courant en course).
    """
    nouveau_poursuivant = nouveau_poursuivant or modele.reference
    nouveau_devant = nouveau_devant or modele.reference
    return {k: simuler_duel(course, modele, cout, poursuivant, devant, tour, tour + 1, tour + 1 + k,
                            nouveau_poursuivant, nouveau_devant) for k in reactions}


# --- Fenêtre d'arrêt -------------------------------------------------------------------

def _cout_relais(modele, equipe, compose, age_depart, longueur):
    """Temps « pneu » d'un relais (écart du composé + usure), pour chaque tirage du bootstrap.

    Le rythme du pilote et l'effet carburant sont identiques pour toutes les stratégies
    (mêmes tours) : ils n'interviennent pas dans la comparaison.
    """
    ages = age_depart + np.arange(longueur)
    total = longueur * modele.coefficient(f"compose:{compose}", tirages=True)
    total = total + modele.pente(compose, equipe, tirages=True) * ages.sum()
    if modele.forme == "quadratique":
        total = total + modele.coefficient(f"usure2:{compose}", tirages=True) * (ages ** 2).sum()
    return total


def strategies_possibles(depuis, nb_tours, compose_actuel, composes_utilises, composes, max_arrets=2,
                         age_actuel=0.0, ages_max=None):
    """
    Toutes les stratégies restantes : [(tour d'entrée, composé chaussé), ...], avec au
    moins RELAIS_MIN tours par relais et l'obligation d'utiliser deux composés secs.

    `ages_max` : âge maximal qu'un pneu de chaque composé a atteint dans la course. Un relais
    ne peut pas aller au-delà : le modèle d'usure est linéaire et ne voit pas la chute de fin
    de vie d'un pneu, il ne faut donc pas l'utiliser loin des durées réellement observées.
    """
    ages_max = ages_max or {}
    for nb in range(0, max_arrets + 1):
        for tours_arret in itertools.combinations(range(depuis + 1, nb_tours), nb):
            bornes = [depuis, *tours_arret, nb_tours]
            longueurs = np.diff(bornes)
            # Le relais en cours a déjà commencé : seul les suivants doivent faire RELAIS_MIN
            if nb and (longueurs[1:] < RELAIS_MIN).any():
                continue
            limite_actuel = max(ages_max.get(compose_actuel, np.inf), age_actuel)
            if age_actuel + longueurs[0] > limite_actuel:
                continue
            for choix in itertools.product(composes, repeat=nb):
                if len(composes_utilises | {compose_actuel} | set(choix)) < 2:
                    continue
                if any(n - 1 > ages_max.get(c, np.inf) for c, n in zip(choix, longueurs[1:], strict=True)):
                    continue
                yield list(zip(tours_arret, choix, strict=True))


def composes_recommandables(modele):
    """
    Composés assez roulés pour fonder une recommandation (au moins RELAIS_MIN_COMPOSE relais).

    Sinon l'optimiseur se rue sur le composé le moins bien mesuré : avec 3 relais en tendres
    à Singapour 2023, l'avantage du tendre neuf était estimé entre -2,2 et -0,2 s par tour,
    et la « meilleure » stratégie finissait sur 30 tours en tendres.
    """
    relais = modele.donnees.groupby("compose")[["pilote", "relais"]].apply(
        lambda d: len(d.drop_duplicates()))
    return [c for c in modele.composes if relais.get(c, 0) >= RELAIS_MIN_COMPOSE]


def ages_maximaux(course):
    """Âge maximal (tours) atteint par un pneu de chaque composé dans la course."""
    tours = course.tours.dropna(subset=["compose", "age_pneu"])
    return {c: float(a) for c, a in tours.groupby("compose")["age_pneu"].max().items()}


def evaluer_strategies(course, modele, cout, pilote, depuis=0, max_arrets=2, sc=None):
    """
    Temps « pneus + arrêts » de chaque stratégie à partir du tour `depuis` (0 = départ).

    `sc` : tour où une voiture de sécurité (ou VSC) rend un arrêt moins coûteux,
    sous la forme {"tour": N, "type": "SC" | "VSC"}.
    Retourne un tableau trié (meilleure en premier) : arrets, nb_arrets, temps (médiane),
    ecart (à la meilleure), et la matrice des tirages pour les probabilités.
    """
    pilote_tours = course.tours[course.tours["pilote"] == pilote].sort_values("tour")
    nb_tours = int(course.tours["tour"].max())
    if depuis == 0:
        # État « à la fin du tour 0 » : le pneu aura l'âge du 1er tour au tour suivant
        premier = pilote_tours.iloc[0]
        etat = {"equipe": premier["equipe"], "compose": premier["compose"], "age": premier["age_pneu"] - 1}
        utilises = set()
    else:
        etat = etat_pilote(course, pilote, depuis)
        utilises = set(pilote_tours.loc[pilote_tours["tour"] <= depuis, "compose"].dropna())
    composes = composes_recommandables(modele)
    utilises &= set(COMPOSES_SECS)
    ages_max = ages_maximaux(course)

    lignes, tirages = [], []
    for arrets in strategies_possibles(depuis, nb_tours, etat["compose"], utilises, composes, max_arrets,
                                       etat["age"], ages_max):
        bornes = [depuis, *[t for t, _ in arrets], nb_tours]
        total = _cout_relais(modele, etat["equipe"], etat["compose"], etat["age"] + 1, bornes[1] - bornes[0])
        for (tour_arret, compose), fin in zip(arrets, bornes[2:], strict=True):
            total = total + _cout_relais(modele, etat["equipe"], compose, 0, fin - tour_arret)
            facteur = cout.facteurs.get(sc["type"], 1.0) if sc and tour_arret == sc["tour"] else 1.0
            total = total + cout.moyen * facteur
        lignes.append({"arrets": arrets, "nb_arrets": len(arrets), "temps": float(np.median(total))})
        tirages.append(total)
    if not lignes:
        raise ValueError("Aucune stratégie possible avec ces contraintes.")
    tableau = pd.DataFrame(lignes)
    tableau["ecart"] = tableau["temps"] - tableau["temps"].min()
    ordre = tableau["ecart"].sort_values(kind="stable").index
    return tableau.loc[ordre].reset_index(drop=True), np.array(tirages)[ordre]


def probabilite_par_nb_arrets(tableau, tirages):
    """Pour chaque nombre d'arrêts, part des tirages où sa meilleure stratégie gagne."""
    meilleurs = {n: tirages[(tableau["nb_arrets"] == n).to_numpy()].min(axis=0)
                 for n in sorted(tableau["nb_arrets"].unique())}
    gagnant = np.argmin(np.vstack(list(meilleurs.values())), axis=0)
    return {n: float(np.mean(gagnant == i)) for i, n in enumerate(meilleurs)}


def strategie_reelle(course, pilote, depuis=0):
    """Arrêts réellement faits par le pilote après `depuis` : [(tour d'entrée, composé chaussé)]."""
    t = course.tours[course.tours["pilote"] == pilote].set_index("tour")
    return [(int(tour), t.loc[tour + 1, "compose"]) for tour in t.index[t["entree_stand"]]
            if tour > depuis and tour + 1 in t.index]


def decrire(arrets):
    if not arrets:
        return "aucun arrêt"
    return ", ".join(f"tour {t} → {c}" for t, c in arrets)


def tracer_fenetre(tableau, titre, reel=None, sc=None):
    """Écart à la meilleure stratégie selon le tour du (premier) arrêt, par séquence de composés."""
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(11, 6))
    un_arret = tableau[tableau["nb_arrets"] == 1].copy()
    un_arret["tour"] = un_arret["arrets"].map(lambda a: a[0][0])
    un_arret["compose"] = un_arret["arrets"].map(lambda a: a[0][1])
    for compose, groupe in un_arret.groupby("compose"):
        groupe = groupe.sort_values("tour")
        ax.plot(groupe["tour"], groupe["ecart"], color=COULEURS_COMPOSES.get(compose, "grey"),
                linewidth=2.5, label=f"1 arrêt, puis {compose}")
    if (tableau["nb_arrets"] == 2).any():
        deux = tableau[tableau["nb_arrets"] == 2].iloc[0]
        ax.axhline(deux["ecart"], color="#4FC3F7", linestyle="--", linewidth=1.5,
                   label=f"meilleure à 2 arrêts ({decrire(deux['arrets'])})")
    dans_fenetre = un_arret.loc[un_arret["ecart"] < MARGE_FENETRE_S, "tour"]
    if not dans_fenetre.empty:
        ax.axvspan(dans_fenetre.min() - 0.5, dans_fenetre.max() + 0.5, color="#43A047", alpha=0.3,
                   label=f"fenêtre : tours {dans_fenetre.min()} à {dans_fenetre.max()} "
                         f"(< {MARGE_FENETRE_S:.0f} s de la meilleure)")
    if reel:
        ax.axvline(reel[0][0], color="#FF7043", linestyle=":", linewidth=2,
                   label=f"arrêt réel (tour {reel[0][0]})")
    if sc:
        ax.axvline(sc["tour"], color="#FFD54F", linestyle="-.", linewidth=1.5,
                   label=f"{sc['type']} (tour {sc['tour']})")
    ax.set_ylim(-0.5, min(30, un_arret["ecart"].max() + 1) if not un_arret.empty else 10)
    ax.set_xlabel("Tour d'entrée aux stands (stratégie à 1 arrêt)")
    ax.set_ylabel("Temps perdu par rapport à la meilleure stratégie (s)")
    ax.set_title(titre)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper center", fontsize=9)
    fig.tight_layout()
    return fig


# --- Ligne de commande -------------------------------------------------------------------

def _intervalle(bas, haut):
    return f"[{bas:+.1f} ; {haut:+.1f}]"


def afficher_duel(resultats, poursuivant, devant, tour):
    premier = next(iter(resultats.values()))
    print(f"\nUndercut de {poursuivant} sur {devant} : décision à la fin du tour {tour}, "
          f"{poursuivant} à {premier.ecart_depart:.2f} s derrière")
    print(f"{poursuivant} s'arrête au tour {tour + 1}.")
    for k, r in resultats.items():
        print(f"  {devant} réagit {k} tour(s) plus tard : {poursuivant} devant dans "
              f"{r.probabilite_devant:.0%} des cas, écart final {r.ecart_final:+.1f} s "
              f"{_intervalle(*r.intervalle())}, gain de l'undercut {r.gain:+.1f} s")
    r1 = resultats[min(resultats)]
    print(f"→ Si {devant} réagit au tour suivant, l'undercut marche tant que l'écart est "
          f"inférieur à {max(r1.gain, 0):.1f} s environ.")


def main(argv=None):
    from course import charger_course  # import local : undercut.py reste testable sans réseau

    parser = argparse.ArgumentParser(description="Aide à la stratégie des arrêts aux stands.")
    parser.add_argument("annee", type=int)
    parser.add_argument("grand_prix")
    sous = parser.add_subparsers(dest="commande", required=True)
    duel = sous.add_parser(
        "duel", help="undercut d'un pilote sur celui de devant",
        usage="strategie.py duel annee grand_prix [-h] [--pneu PNEU] tour poursuivant devant")
    duel.add_argument("tour", type=int, help="tour de décision")
    duel.add_argument("poursuivant")
    duel.add_argument("devant")
    duel.add_argument("--pneu", help="composé chaussé par les deux pilotes (défaut : le plus dur)")
    fenetre = sous.add_parser(
        "fenetre", help="meilleure stratégie d'arrêts pour un pilote",
        usage="strategie.py fenetre annee grand_prix [-h] [--depuis N] [--sc N] [--vsc] [--no-show] pilote")
    fenetre.add_argument("pilote")
    fenetre.add_argument("--depuis", type=int, default=0, help="tour à partir duquel on décide")
    fenetre.add_argument("--sc", type=int, help="tour d'une voiture de sécurité (arrêt moins coûteux)")
    fenetre.add_argument("--vsc", action="store_true", help="avec --sc : voiture de sécurité virtuelle")
    fenetre.add_argument("--no-show", action="store_true")
    args = parser.parse_args(argv)

    course = charger_course(args.annee, args.grand_prix)
    modele = ajuster_usure(course.tours)
    cout = mesurer_cout_arret(course, modele)
    mv = course.perte_stand.get("normal")
    print(f"Coût d'un arrêt : {cout.moyen:.1f} s ± {cout.ecart_type:.1f} ({cout.source})"
          + (f", MultiViewer : {mv:.1f} s" if mv else ""))

    if args.commande == "duel":
        p, d = args.poursuivant.upper(), args.devant.upper()
        pneu = args.pneu.upper() if args.pneu else None
        afficher_duel(analyser_undercut(course, modele, cout, p, d, args.tour, pneu, pneu), p, d, args.tour)
        return

    pilote = args.pilote.upper()
    sc = {"tour": args.sc, "type": "VSC" if args.vsc else "SC"} if args.sc else None
    tableau, tirages = evaluer_strategies(course, modele, cout, pilote, args.depuis, sc=sc)
    meilleure = tableau.iloc[0]
    moment = "depuis le départ" if args.depuis == 0 else f"à partir du tour {args.depuis}"
    print(f"\nStratégies de {pilote} {moment} ({len(tableau)} évaluées) :")
    recommandables = composes_recommandables(modele)
    limites = ", ".join(f"{c} {a:.0f}" for c, a in ages_maximaux(course).items() if c in recommandables)
    print(f"  relais limités à l'âge maximal observé dans la course (tours) : {limites}")
    ignores = [c for c in modele.composes if c not in recommandables]
    if ignores:
        print(f"  non proposés (moins de {RELAIS_MIN_COMPOSE} relais propres) : {', '.join(ignores)}")
    print(f"  meilleure : {decrire(meilleure['arrets'])}")
    for n, p in probabilite_par_nb_arrets(tableau, tirages).items():
        print(f"  {n} arrêt(s) : meilleure option dans {p:.0%} des tirages du modèle")
    un_arret = tableau[tableau["nb_arrets"] == 1]
    if not un_arret.empty:
        fenetre_tours = sorted({a[0][0] for a in un_arret.loc[un_arret["ecart"] < MARGE_FENETRE_S, "arrets"]})
        if fenetre_tours:
            print(f"  fenêtre à 1 arrêt (< {MARGE_FENETRE_S:.0f} s de la meilleure) : "
                  f"tours {fenetre_tours[0]} à {fenetre_tours[-1]}")
    reel = strategie_reelle(course, pilote, args.depuis)
    ligne_reelle = tableau[tableau["arrets"].map(lambda a: a == reel)]
    if not ligne_reelle.empty:
        print(f"  stratégie réelle ({decrire(reel)}) : {ligne_reelle['ecart'].iloc[0]:+.1f} s "
              f"par rapport à la meilleure")
    if sc:
        sans = evaluer_strategies(course, modele, cout, pilote, args.depuis)[0]["temps"].min()
        avec_arret = tableau[tableau["arrets"].map(lambda a: any(t == sc["tour"] for t, _ in a))]
        if not avec_arret.empty:
            print(f"  s'arrêter sous {sc['type']} au tour {sc['tour']} : "
                  f"{avec_arret['temps'].iloc[0] - sans:+.1f} s par rapport au meilleur plan "
                  f"sans neutralisation")

    nom = f"fenetre_{args.annee}_{args.grand_prix.replace(' ', '_')}_{pilote}.png"
    titre = f"{course.titre} — {pilote} : fenêtre d'arrêt ({moment})"
    tracer_fenetre(tableau, titre, reel, sc).savefig(nom, dpi=150)
    print(f"\nGraphique enregistré : {nom}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
