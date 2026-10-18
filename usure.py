"""
Modèle d'usure des pneus : combien chaque composé fait perdre par tour, avec une marge d'erreur.

Pour une course, sur les tours propres en pneus secs :

    temps = rythme du pilote + carburant × tour + écart du composé à neuf
            + (usure du composé + écart de l'écurie) × âge du pneu

- « rythme du pilote » : un niveau par pilote (voiture + pilote), pour comparer les composés
  à voiture égale ;
- « carburant » : la voiture s'allège au fil de la course (le terme absorbe aussi
  l'amélioration de la piste, les deux sont indissociables avec ces données) ;
- « écart du composé à neuf » : par rapport au composé de référence (le plus dur utilisé) ;
- « usure » : perte de temps par tour d'âge du pneu, propre à chaque composé ;
- « écart de l'écurie » : chaque voiture use ses pneus à sa façon. Cet écart est rappelé
  vers 0 (pénalité « ridge ») : une écurie avec peu de tours garde l'usure moyenne.
  Gain modeste : il améliore la prédiction sur 8 courses sur 8 là où le rappel a été réglé,
  et sur 5 sur 8 sur des courses jamais vues (0,403 s d'erreur contre 0,410 s sans).
- En forme « quadratique », on ajoute un terme en âge² pour capter une chute en fin de vie.

L'ajustement est une régression robuste (Huber) : les tours encore un peu perturbés
pèsent moins. La marge d'erreur vient d'un bootstrap « sauvage » par relais.

Utilisation :
    python usure.py 2024 Monza
    python usure.py 2024 Monza --forme quadratique --no-show
"""

import argparse
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

COMPOSES_SECS = ["SOFT", "MEDIUM", "HARD"]  # du plus tendre au plus dur
COULEURS_COMPOSES = {"SOFT": "#DA291C", "MEDIUM": "#FFD12E", "HARD": "#F0F0EC"}
TOURS_MIN_COMPOSE = 15  # en dessous, un composé est trop peu roulé pour être estimé
NB_BOOTSTRAP = 200
FORMES = ("lineaire", "quadratique")
# Force du rappel des écarts d'usure par écurie vers 0. Réglée sur 8 courses 2023-2025
# (contrôle de prédiction des fins de relais), vérifiée sur 8 autres jamais vues.
RAPPEL_ECURIE = 2000.0
# Effet carburant rappelé faiblement vers une valeur typique (s/tour). Avant les arrêts,
# l'âge du pneu avance exactement comme le numéro du tour : carburant et usure se confondent
# et le calcul pouvait donner +14 s/tour d'un côté et -14 s/tour de l'autre. Avec des
# arrêts (âges remis à zéro), les données l'emportent largement sur ce rappel.
CARBURANT_TYPIQUE = -0.06
RAPPEL_CARBURANT = 2000.0


@dataclass
class ModeleUsure:
    forme: str
    reference: str  # composé de référence (écart à neuf = 0)
    composes: list
    pilotes: list
    colonnes: list  # nom de chaque coefficient
    coefficients: np.ndarray
    tirages: np.ndarray  # coefficients de chaque rééchantillonnage (nb tirages x nb coefficients)
    donnees: pd.DataFrame  # tours utilisés pour l'ajustement
    ecartes: dict = field(default_factory=dict)  # composé écarté -> raison

    def coefficient(self, nom, tirages=False):
        """Valeur d'un coefficient ; 0 s'il n'existe pas (écurie sans ce composé, par exemple)."""
        if nom not in self.colonnes:
            return np.zeros(len(self.tirages)) if tirages else 0.0
        i = self.colonnes.index(nom)
        return self.tirages[:, i] if tirages else self.coefficients[i]

    def intervalle(self, nom, niveau=0.95):
        """Intervalle de confiance (bootstrap) d'un coefficient."""
        a = (1 - niveau) / 2 * 100
        return tuple(np.percentile(self.coefficient(nom, tirages=True), [a, 100 - a]))

    def predire(self, tours, coefficients=None):
        """Temps au tour prédit (colonnes pilote, equipe, tour, compose, age_pneu)."""
        coefficients = self.coefficients if coefficients is None else coefficients
        return matrice(tours, self.colonnes) @ coefficients

    def ecart_type_residuel(self):
        """Dispersion robuste des temps au tour autour du modèle (s) : l'aléa d'un tour."""
        residus = self.donnees["temps"].to_numpy(dtype=float) - self.predire(self.donnees)
        return float(1.4826 * np.median(np.abs(residus - np.median(residus))))

    def pente(self, compose, equipe=None, tirages=False):
        """Usure (s par tour d'âge) d'un composé, pour une écurie si elle est donnée."""
        pente = self.coefficient(f"usure:{compose}", tirages)
        if equipe is not None:
            pente = pente + self.coefficient(f"usure_ecurie:{equipe}:{compose}", tirages)
        return pente

    def usure(self, compose, age, equipe=None):
        """Temps perdu (s) par rapport au même pneu neuf, après `age` tours (estimation centrale)."""
        perte = self.pente(compose, equipe) * age
        if self.forme == "quadratique":
            perte = perte + self.coefficient(f"usure2:{compose}") * age ** 2
        return perte


# --- Matrice du modèle et ajustement ---------------------------------------------------

def colonnes_modele(donnees, composes, reference, forme, par_ecurie=True):
    colonnes = [f"pilote:{p}" for p in sorted(donnees["pilote"].unique())] + ["carburant"]
    colonnes += [f"compose:{c}" for c in composes if c != reference]
    colonnes += [f"usure:{c}" for c in composes]
    if forme == "quadratique":
        colonnes += [f"usure2:{c}" for c in composes]
    if par_ecurie:
        paires = donnees[["equipe", "compose"]].dropna().drop_duplicates()
        colonnes += [f"usure_ecurie:{e}:{c}" for e, c in sorted(paires.itertuples(index=False))]
    return colonnes


def matrice(tours, colonnes):
    """Une ligne par tour, une colonne par coefficient (voir colonnes_modele)."""
    x = np.zeros((len(tours), len(colonnes)))
    pilote = tours["pilote"].to_numpy()
    compose = tours["compose"].to_numpy()
    equipe = tours["equipe"].to_numpy() if "equipe" in tours else np.full(len(tours), None)
    age = tours["age_pneu"].to_numpy(dtype=float)
    for j, nom in enumerate(colonnes):
        genre, _, valeur = nom.partition(":")
        if genre == "pilote":
            x[:, j] = pilote == valeur
        elif genre == "carburant":
            x[:, j] = tours["tour"].to_numpy(dtype=float)
        elif genre == "compose":
            x[:, j] = compose == valeur
        elif genre == "usure":
            x[:, j] = (compose == valeur) * age
        elif genre == "usure2":
            x[:, j] = (compose == valeur) * age ** 2
        elif genre == "usure_ecurie":
            nom_equipe, _, nom_compose = valeur.rpartition(":")
            x[:, j] = ((equipe == nom_equipe) & (compose == nom_compose)) * age
    return x


def regression_robuste(x, y, penalites=None, cibles=None, iterations=50, k=1.345):
    """
    Moindres carrés pondérés itératifs avec la fonction de Huber.

    Les résidus au-delà de k écarts-types robustes reçoivent un poids réduit (k / |résidu|).
    `penalites` : force du rappel de chaque coefficient vers sa cible (ridge), 0 = aucun rappel.
    `cibles` : valeur vers laquelle chaque coefficient est rappelé (0 par défaut).
    """
    lignes_rappel = np.zeros((0, x.shape[1]))
    valeurs_rappel = np.zeros(0)
    if penalites is not None and np.any(penalites > 0):
        rappel = np.flatnonzero(penalites > 0)
        racines = np.sqrt(penalites[rappel])
        lignes_rappel = np.zeros((len(rappel), x.shape[1]))
        lignes_rappel[np.arange(len(rappel)), rappel] = racines
        valeurs_rappel = racines * (np.zeros(x.shape[1]) if cibles is None else cibles)[rappel]

    def resoudre(racine_poids):
        a = np.vstack([x * racine_poids[:, None], lignes_rappel])
        return np.linalg.lstsq(a, np.concatenate([y * racine_poids, valeurs_rappel]), rcond=None)[0]

    beta = resoudre(np.ones(len(y)))
    for _ in range(iterations):
        residus = y - x @ beta
        echelle = 1.4826 * np.median(np.abs(residus - np.median(residus)))
        if echelle < 1e-9:
            break
        u = np.abs(residus) / (k * echelle)
        nouveau = resoudre(np.sqrt(np.minimum(1.0, 1.0 / np.maximum(u, 1e-12))))
        converge = np.max(np.abs(nouveau - beta)) < 1e-6
        beta = nouveau
        if converge:
            break
    return beta


def preparer_donnees(tours):
    """
    Tours propres en pneus secs. Écarte (avec la raison) :
    - les composés trop peu roulés ;
    - les composés « non reliés » : l'écart entre deux composés neufs ne se mesure que grâce
      aux pilotes qui ont roulé les deux. Sans aucun pilote commun (en début de course,
      personne n'a encore changé de pneus), il se confond avec le rythme propre des pilotes
      et le calcul le répartirait au hasard. On garde le groupe de composés relié au plus roulé.
    """
    donnees = tours[tours["propre"] & tours["compose"].isin(COMPOSES_SECS)].copy()
    comptes = donnees["compose"].value_counts()
    ecartes = {c: f"seulement {n} tours propres" for c, n in comptes.items() if n < TOURS_MIN_COMPOSE}
    donnees = donnees[~donnees["compose"].isin(ecartes)]
    if donnees["compose"].nunique() == 0:
        raise ValueError("Pas assez de tours propres en pneus secs pour estimer l'usure.")

    composes_par_pilote = donnees.groupby("pilote")["compose"].agg(set)
    relies = {donnees["compose"].value_counts().idxmax()}
    while True:  # on étend le groupe tant qu'un pilote relie un nouveau composé
        nouveaux = set().union(*[s for s in composes_par_pilote if s & relies]) - relies
        if not nouveaux:
            break
        relies |= nouveaux
    for c in set(donnees["compose"]) - relies:
        ecartes[c] = "aucun pilote ne l'a roulé avec un autre composé"
    donnees = donnees[donnees["compose"].isin(relies)]
    return donnees.reset_index(drop=True), ecartes


def ajuster_usure(tours, forme="lineaire", par_ecurie=True, nb_bootstrap=NB_BOOTSTRAP, graine=0):
    """Ajuste le modèle sur les tours propres d'une course (tableau de course.charger_course)."""
    if forme not in FORMES:
        raise ValueError(f"forme inconnue : {forme}")
    donnees, ecartes = preparer_donnees(tours)
    composes = [c for c in COMPOSES_SECS if c in set(donnees["compose"])]
    reference = composes[-1]  # le plus dur utilisé
    colonnes = colonnes_modele(donnees, composes, reference, forme, par_ecurie)
    penalites = np.array([RAPPEL_ECURIE if c.startswith("usure_ecurie:")
                          else RAPPEL_CARBURANT if c == "carburant" else 0.0 for c in colonnes])
    cibles = np.array([CARBURANT_TYPIQUE if c == "carburant" else 0.0 for c in colonnes])

    x, y = matrice(donnees, colonnes), donnees["temps"].to_numpy(dtype=float)
    coefficients = regression_robuste(x, y, penalites, cibles)

    # Bootstrap « sauvage » par relais : on garde tous les tours et on recombine les résidus,
    # chaque relais recevant un signe tiré au hasard. Tous les pilotes et composés restent
    # présents dans chaque tirage (un rééchantillonnage des relais pouvait faire disparaître
    # un pilote et rendre ses coefficients absurdes), et l'aléa commun aux tours d'un même
    # relais (pneu un peu meilleur, trafic prolongé) est conservé.
    rng = np.random.default_rng(graine)
    relais = donnees.groupby(["pilote", "relais"]).ngroup().to_numpy()
    ajuste = x @ coefficients
    residus = y - ajuste
    tirages = np.empty((nb_bootstrap, len(colonnes)))
    for b in range(nb_bootstrap):
        signes = rng.choice([-1.0, 1.0], size=relais.max() + 1)[relais]
        tirages[b] = regression_robuste(x, ajuste + signes * residus, penalites, cibles)

    pilotes = sorted(donnees["pilote"].unique())
    return ModeleUsure(forme, reference, composes, pilotes, colonnes, coefficients, tirages,
                       donnees, ecartes)


# --- Contrôle : prédire la fin des relais ------------------------------------------------

def controler(tours, forme="lineaire", par_ecurie=True, part_cachee=0.3, tours_min_relais=8):
    """
    Erreur de prédiction du modèle sur des tours qu'il n'a pas vus.

    Pour chaque relais d'au moins `tours_min_relais` tours propres, on cache les derniers
    `part_cachee` (au moins 3 tours), on ajuste sur le reste, puis on prédit les tours cachés.
    Comparaison avec un modèle naïf : le rythme moyen du début du même relais, constant.
    """
    donnees, _ = preparer_donnees(tours)
    donnees = donnees.sort_values(["pilote", "relais", "tour"])
    cache = pd.Series(False, index=donnees.index)
    for _, groupe in donnees.groupby(["pilote", "relais"]):
        if len(groupe) >= tours_min_relais:
            n = max(3, round(part_cachee * len(groupe)))
            cache[groupe.index[-n:]] = True

    entrainement = donnees[~cache]
    modele = ajuster_usure(entrainement, forme=forme, par_ecurie=par_ecurie, nb_bootstrap=0)
    test = donnees[cache & donnees["compose"].isin(modele.composes) & donnees["pilote"].isin(modele.pilotes)]

    erreur_modele = test["temps"] - modele.predire(test)
    rythme_debut = entrainement.groupby(["pilote", "relais"])["temps"].mean()
    naif = [rythme_debut.get((p, r), np.nan) for p, r in zip(test["pilote"], test["relais"], strict=True)]
    erreur_naive = test["temps"] - np.array(naif)
    return {
        "relais": test.groupby(["pilote", "relais"]).ngroups,
        "tours": len(test),
        "erreur_modele": float(np.mean(np.abs(erreur_modele))),
        "biais_modele": float(np.mean(erreur_modele)),
        "erreur_naive": float(np.nanmean(np.abs(erreur_naive))),
    }


# --- Restitution ------------------------------------------------------------------------

def tableau_usure(modele):
    """Un composé par ligne : tours, relais, écart à neuf et usure moyenne, intervalles à 95 %."""
    lignes = []
    for c in modele.composes:
        d = modele.donnees[modele.donnees["compose"] == c]
        ligne = {"compose": c, "tours": len(d), "relais": d.groupby(["pilote", "relais"]).ngroups}
        ligne["ecart_neuf"] = modele.coefficient(f"compose:{c}")
        ligne["ecart_bas"], ligne["ecart_haut"] = modele.intervalle(f"compose:{c}")
        ligne["usure"] = modele.coefficient(f"usure:{c}")
        ligne["usure_bas"], ligne["usure_haut"] = modele.intervalle(f"usure:{c}")
        if modele.forme == "quadratique":
            ligne["usure2"] = modele.coefficient(f"usure2:{c}")
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def tableau_ecuries(modele):
    """Usure (s/tour) de chaque écurie sur chaque composé : moyenne + écart de l'écurie."""
    equipes = sorted(modele.donnees["equipe"].dropna().unique())
    tableau = pd.DataFrame(index=equipes, columns=modele.composes, dtype=float)
    for e in equipes:
        for c in modele.composes:
            if f"usure_ecurie:{e}:{c}" in modele.colonnes:
                tableau.loc[e, c] = modele.pente(c, e)
    return tableau.sort_values(modele.reference)


def tracer_usure(modele, titre):
    """Temps au tour corrigé (pilote, carburant et écurie retirés) en fonction de l'âge du pneu."""
    d = modele.donnees
    niveau_pilote = pd.Series({p: modele.coefficient(f"pilote:{p}") for p in modele.pilotes})
    ecart_ecurie = np.array([modele.coefficient(f"usure_ecurie:{e}:{c}")
                             for e, c in zip(d["equipe"], d["compose"], strict=True)])
    corrige = (d["temps"] - d["pilote"].map(niveau_pilote) - modele.coefficient("carburant") * d["tour"]
               - ecart_ecurie * d["age_pneu"])

    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for c in modele.composes:
        masque = d["compose"] == c
        ax.scatter(d.loc[masque, "age_pneu"], corrige[masque], s=8, alpha=0.35, color=COULEURS_COMPOSES[c])
        ages = np.linspace(0, d.loc[masque, "age_pneu"].max(), 100)
        courbes = (modele.coefficient(f"compose:{c}", True)[:, None]
                   + modele.coefficient(f"usure:{c}", True)[:, None] * ages)
        if modele.forme == "quadratique":
            courbes = courbes + modele.coefficient(f"usure2:{c}", True)[:, None] * ages ** 2
        ax.fill_between(ages, *np.percentile(courbes, [2.5, 97.5], axis=0),
                        color=COULEURS_COMPOSES[c], alpha=0.2)
        ax.plot(ages, modele.coefficient(f"compose:{c}") + modele.usure(c, ages),
                color=COULEURS_COMPOSES[c], linewidth=2.5,
                label=f"{c} : {modele.coefficient(f'usure:{c}'):+.3f} s/tour")

    # Échelle utile : on coupe les points extrêmes pour voir les courbes
    bas, haut = np.percentile(corrige, [1, 99])
    ax.set_ylim(bas - 0.3, haut + 0.3)
    ax.set_xlabel("Âge du pneu (tours)")
    ax.set_ylabel(f"Temps au tour corrigé (s, relatif au {modele.reference} neuf)")
    ax.set_title(f"{titre}\nUsure moyenne des pneus (pilote, carburant et écurie retirés, "
                 f"bande = intervalle à 95 %)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    return fig


def _intervalle(bas, haut, fmt="+.3f"):
    return f"[{bas:{fmt}} ; {haut:{fmt}}]"


def main():
    from course import charger_course  # import local : usure.py reste testable sans réseau

    parser = argparse.ArgumentParser(description="Modèle d'usure des pneus pour une course.")
    parser.add_argument("annee", type=int)
    parser.add_argument("grand_prix")
    parser.add_argument("--forme", choices=FORMES, default="lineaire")
    parser.add_argument("--no-show", action="store_true", help="enregistre le graphique sans l'afficher")
    args = parser.parse_args()

    course = charger_course(args.annee, args.grand_prix)
    modele = ajuster_usure(course.tours, forme=args.forme)
    d = modele.donnees
    print(f"\nModèle d'usure ({args.forme}) : {len(d)} tours propres en pneus secs, "
          f"{d['pilote'].nunique()} pilotes")
    for c, raison in modele.ecartes.items():
        print(f"  {c} écarté : {raison}")
    carburant = modele.coefficient("carburant")
    print(f"Carburant + piste : {carburant:+.3f} s/tour {_intervalle(*modele.intervalle('carburant'))}")

    print(f"\n{'Composé':<8} {'Tours':>5} {'Relais':>6}   {'Écart à neuf (s)':<28} Usure moyenne (s/tour)")
    for r in tableau_usure(modele).itertuples():
        ecart = ("0 (référence)" if r.compose == modele.reference
                 else f"{r.ecart_neuf:+.2f} {_intervalle(r.ecart_bas, r.ecart_haut, '+.2f')}")
        print(f"{r.compose:<8} {r.tours:>5} {r.relais:>6}   {ecart:<28} "
              f"{r.usure:+.3f} {_intervalle(r.usure_bas, r.usure_haut)}")

    print("\nUsure par écurie (s/tour) :")
    print(tableau_ecuries(modele).map(lambda v: "" if pd.isna(v) else f"{v:+.3f}").to_string())

    controle = controler(course.tours, forme=args.forme)
    print(f"\nContrôle : prédiction de la fin de {controle['relais']} relais "
          f"({controle['tours']} tours cachés)")
    print(f"  modèle          : erreur moyenne {controle['erreur_modele']:.3f} s "
          f"(biais {controle['biais_modele']:+.3f} s : le modèle prédit trop vite si positif)")
    print(f"  rythme constant : erreur moyenne {controle['erreur_naive']:.3f} s")

    nom = f"usure_{args.annee}_{args.grand_prix.replace(' ', '_')}.png"
    tracer_usure(modele, course.titre).savefig(nom, dpi=150)
    print(f"\nGraphique enregistré : {nom}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
