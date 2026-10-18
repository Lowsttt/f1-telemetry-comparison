"""
Vérification du calculateur d'undercut sur l'historique : prédit-il les vrais undercuts ?

1. Détection des vrais undercuts : à la fin du tour N, A est juste derrière B (même tour,
   moins de ECART_MAX_S). A s'arrête au tour N+1, B dans les REACTION_MAX tours suivants,
   tout se passe sous drapeau vert. Résultat réel : A est-il devant une fois les deux
   arrêts faits (fin du tour de sortie de B) ?
2. Prédiction sans données du futur : le modèle d'usure et le coût d'arrêt ne voient que
   les tours 1 à N. Un composé encore jamais roulé reçoit un « a priori » calculé sur les
   courses précédentes uniquement (ordre chronologique).
3. Mesures : taux de bonnes prédictions, score de Brier (qualité des probabilités : 0 =
   parfait, 0,25 = pile ou face), calibration, comparées à des règles simples.

Utilisation :
    python validation.py 2023 2025
    python validation.py 2024 2024 --max-courses 5 --no-show
"""

import argparse
import contextlib
import io
from dataclasses import dataclass, field, replace

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from undercut import mesurer_cout_arret, simuler_duel
from usure import COMPOSES_SECS, ajuster_usure

ECART_MAX_S = 3.0  # au-delà, ce n'est plus une tentative d'undercut sur la voiture de devant
REACTION_MAX = 5  # B s'arrête au plus 5 tours après A
TOUR_DECISION_MIN = 8  # avant, trop peu de tours pour estimer l'usure
NB_BOOTSTRAP_VALIDATION = 100
SEUIL_REGLE_S = 2.0  # règle simple : « l'undercut marche si l'écart est inférieur à 2 s »
CLASSES_CALIBRATION = [0, 0.2, 0.4, 0.6, 0.8, 1.0]


# --- 1. Détection des vrais undercuts ----------------------------------------------------

def detecter_undercuts(course):
    """Tentatives d'undercut réelles d'une course (voir la docstring du module)."""
    t = course.tours
    fin = t.set_index(["pilote", "tour"])["fin"]
    par_cle = t.set_index(["pilote", "tour"])
    arrets = t[t["entree_stand"]]
    cas = []
    for a in arrets.itertuples():
        tour_a, decision = int(a.tour), int(a.tour) - 1
        if decision < TOUR_DECISION_MIN or (a.pilote, decision) not in fin.index:
            continue
        # Voiture juste devant A sur la route, dans le même tour, à la fin du tour de décision
        fin_a = fin[(a.pilote, decision)]
        devant = t[(t["tour"] == decision) & (t["fin"] < fin_a)].sort_values("fin")
        if devant.empty:
            continue
        b = devant.iloc[-1]["pilote"]
        ecart = (fin_a - fin[(b, decision)]).total_seconds()
        if ecart > ECART_MAX_S:
            continue
        arrets_b = arrets.loc[(arrets["pilote"] == b) & (arrets["tour"] > decision), "tour"]
        if arrets_b.empty:
            continue
        tour_b = int(arrets_b.min())
        reaction = tour_b - tour_a
        if not 1 <= reaction <= REACTION_MAX:
            continue
        fin_cycle = tour_b + 1
        # A ne doit pas repasser par les stands avant la fin du cycle
        repasse = (arrets["pilote"] == a.pilote) & arrets["tour"].between(tour_a + 1, fin_cycle)
        if repasse.any():
            continue
        cles = [(p, n) for p in (a.pilote, b) for n in range(decision + 1, fin_cycle + 1)]
        if any(c not in par_cle.index for c in cles):
            continue  # abandon pendant le cycle
        cycle = par_cle.loc[cles]
        if (cycle["etat_piste"] != "VERT").any():
            continue
        nouveau_a = par_cle.loc[(a.pilote, tour_a + 1), "compose"]
        nouveau_b = par_cle.loc[(b, tour_b + 1), "compose"]
        if nouveau_a not in COMPOSES_SECS or nouveau_b not in COMPOSES_SECS:
            continue
        ecart_final = (fin[(a.pilote, fin_cycle)] - fin[(b, fin_cycle)]).total_seconds()
        cas.append({"course": course.titre, "annee": course.annee, "poursuivant": a.pilote, "devant": b,
                    "tour": decision, "arret_poursuivant": tour_a, "arret_devant": tour_b,
                    "reaction": reaction, "nouveau_poursuivant": nouveau_a, "nouveau_devant": nouveau_b,
                    "ecart_depart": ecart, "ecart_reel": ecart_final, "reussi": ecart_final < 0})
    return pd.DataFrame(cas)


# --- 2. Prédiction sans données du futur --------------------------------------------------

@dataclass
class APriori:
    """Écart à neuf et usure des composés, appris sur les courses précédentes."""
    ecarts: dict = field(default_factory=dict)  # (composé, référence) -> liste d'écarts à neuf
    usures: dict = field(default_factory=dict)  # composé -> liste d'usures (s/tour)

    def ajouter(self, modele):
        """Enregistre les valeurs d'une course terminée (modèle ajusté sur toute la course)."""
        for c in modele.composes:
            self.usures.setdefault(c, []).append(modele.pente(c))
            for r in modele.composes:
                if r != c:
                    ecart = modele.coefficient(f"compose:{c}") - modele.coefficient(f"compose:{r}")
                    self.ecarts.setdefault((c, r), []).append(ecart)

    def valeurs(self, compose, reference):
        """(écart à neuf par rapport à la référence, usure, dispersions) ou None si inconnu."""
        ecarts, usures = self.ecarts.get((compose, reference), []), self.usures.get(compose, [])
        if len(ecarts) < 2 or len(usures) < 2:
            return None
        return (float(np.median(ecarts)), float(np.median(usures)),
                float(np.std(ecarts)), float(np.std(usures)))


def completer_avec_a_priori(modele, a_priori, composes_requis, graine=0):
    """
    Ajoute au modèle les composés requis mais encore jamais roulés proprement, avec les
    valeurs de l'a priori. Leurs tirages reprennent la dispersion d'une course à l'autre,
    pour que l'incertitude se retrouve dans la probabilité finale.
    Retourne (modèle complété, liste des composés ajoutés) ; ValueError si pas d'a priori.
    """
    manquants = [c for c in COMPOSES_SECS if c in composes_requis and c not in modele.composes]
    if not manquants:
        return modele, []
    rng = np.random.default_rng(graine)
    colonnes, coefficients, tirages = list(modele.colonnes), list(modele.coefficients), [modele.tirages]
    for c in manquants:
        valeurs = a_priori.valeurs(c, modele.reference)
        if valeurs is None:
            raise ValueError(f"pas d'a priori pour {c} face à {modele.reference}")
        ecart, usure, dispersion_ecart, dispersion_usure = valeurs
        colonnes += [f"compose:{c}", f"usure:{c}"]
        coefficients += [ecart, usure]
        n = len(modele.tirages)
        tirages.append(np.column_stack([ecart + rng.normal(0, dispersion_ecart, n),
                                        usure + rng.normal(0, dispersion_usure, n)]))
    composes = [c for c in COMPOSES_SECS if c in set(modele.composes) | set(manquants)]
    return replace(modele, composes=composes, colonnes=colonnes, coefficients=np.array(coefficients),
                   tirages=np.hstack(tirages)), manquants


def course_tronquee(course, tour):
    """La course telle qu'on la connaît à la fin du tour `tour`."""
    return replace(course, tours=course.tours[course.tours["tour"] <= tour].copy())


def predire_undercuts(course, cas, a_priori, nb_bootstrap=NB_BOOTSTRAP_VALIDATION):
    """Probabilité prédite pour chaque tentative, avec uniquement les tours connus au moment de décider."""
    modeles = {}
    lignes = []
    for c in cas.itertuples():
        ligne = c._asdict()
        ligne.pop("Index")
        try:
            if c.tour not in modeles:
                connue = course_tronquee(course, c.tour)
                modele = ajuster_usure(connue.tours, nb_bootstrap=nb_bootstrap)
                modeles[c.tour] = (connue, modele, mesurer_cout_arret(connue, modele))
            connue, modele, cout = modeles[c.tour]
            if c.poursuivant not in modele.pilotes or c.devant not in modele.pilotes:
                raise ValueError("pilote sans tour propre avant la décision")
            requis = {c.nouveau_poursuivant, c.nouveau_devant}
            requis |= set(connue.tours.loc[connue.tours["tour"] == c.tour, "compose"].dropna())
            complet, ajoutes = completer_avec_a_priori(modele, a_priori, requis)
            r = simuler_duel(connue, complet, cout, c.poursuivant, c.devant, c.tour, c.arret_poursuivant,
                             c.arret_devant, c.nouveau_poursuivant, c.nouveau_devant)
            ligne.update(probabilite=r.probabilite_devant, ecart_predit=r.ecart_final,
                         dispersion=float(np.std(r.echantillons)),
                         a_priori=", ".join(ajoutes), cout_source=cout.source, ignore="")
        except ValueError as e:
            ligne.update(probabilite=np.nan, ecart_predit=np.nan, dispersion=np.nan, a_priori="",
                         cout_source="", ignore=str(e))
        lignes.append(ligne)
    return pd.DataFrame(lignes)


# --- 3. Mesures -------------------------------------------------------------------------

def brier(probabilites, reussi):
    return float(np.mean((np.asarray(probabilites, dtype=float) - np.asarray(reussi, dtype=float)) ** 2))


def meilleur_seuil_a_posteriori(resultats):
    """Seuil d'écart qui aurait le mieux classé les cas, choisi en connaissant les résultats."""
    seuils = np.arange(0.1, ECART_MAX_S + 0.05, 0.1)
    justes = [np.mean((resultats["ecart_depart"] < s) == resultats["reussi"]) for s in seuils]
    return float(seuils[int(np.argmax(justes))])


def mesurer(resultats):
    """Tableau comparatif : modèle contre règles simples (sur les cas prédits)."""
    r = resultats.dropna(subset=["probabilite"])
    reussi = r["reussi"].astype(float)
    seuil = meilleur_seuil_a_posteriori(r)
    candidats = {
        "modèle": r["probabilite"],
        "toujours réussi": pd.Series(1.0, index=r.index),
        "pile ou face": pd.Series(0.5, index=r.index),
        f"écart < {SEUIL_REGLE_S:.0f} s": (r["ecart_depart"] < SEUIL_REGLE_S).astype(float),
        f"écart < {seuil:.1f} s (seuil choisi a posteriori)": (r["ecart_depart"] < seuil).astype(float),
    }
    lignes = []
    for nom, p in candidats.items():
        justes = ((p > 0.5) == reussi.astype(bool)) if nom != "pile ou face" else pd.Series(np.nan)
        lignes.append({"méthode": nom, "bonnes prédictions": float(justes.mean()), "Brier": brier(p, reussi)})
    return pd.DataFrame(lignes)


def calibration(resultats):
    """Par classe de probabilité prédite : probabilité moyenne annoncée et fréquence réelle."""
    r = resultats.dropna(subset=["probabilite"])
    classes = pd.cut(r["probabilite"], CLASSES_CALIBRATION, include_lowest=True)
    return r.groupby(classes, observed=True).agg(cas=("reussi", "size"), annonce=("probabilite", "mean"),
                                                 observe=("reussi", "mean")).reset_index()


def tracer_calibration(resultats, titre):
    cal = calibration(resultats)
    plt.style.use("dark_background")
    fig, (ax, ax_hist) = plt.subplots(1, 2, figsize=(12, 5.5), gridspec_kw={"width_ratios": [1.2, 1]})
    ax.plot([0, 1], [0, 1], color="grey", linestyle="--", label="calibration parfaite")
    ax.plot(cal["annonce"], cal["observe"], marker="o", color="#4FC3F7", linewidth=2, label="modèle")
    for ligne in cal.itertuples():
        a_droite = ligne.annonce > 0.85  # près du bord : étiquette à gauche du point
        ax.annotate(f"{ligne.cas} cas", (ligne.annonce, ligne.observe), textcoords="offset points",
                    xytext=(-6 if a_droite else 6, -12), ha="right" if a_droite else "left",
                    fontsize=8, color="grey")
    ax.set_xlabel("Probabilité annoncée de réussite de l'undercut")
    ax.set_ylabel("Fréquence réelle de réussite")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    ax.set_title("Calibration")
    r = resultats.dropna(subset=["probabilite"])
    ax_hist.hist([r.loc[r["reussi"], "probabilite"], r.loc[~r["reussi"], "probabilite"]],
                 bins=np.linspace(0, 1, 11), stacked=True, color=["#66BB6A", "#EF5350"],
                 label=["undercut réussi", "undercut raté"])
    ax_hist.set_xlabel("Probabilité annoncée")
    ax_hist.set_ylabel("Nombre de cas")
    ax_hist.legend()
    ax_hist.set_title("Répartition des prédictions")
    fig.suptitle(titre)
    fig.tight_layout()
    return fig


def par_groupe(predits, colonne):
    """Cas, part de réussites, bonnes prédictions et Brier par valeur d'une colonne."""
    return predits.groupby(colonne).apply(
        lambda d: pd.Series({"cas": len(d), "réussis": d["reussi"].mean(),
                             "bonnes prédictions": ((d["probabilite"] > 0.5) == d["reussi"]).mean(),
                             "Brier": brier(d["probabilite"], d["reussi"])}), include_groups=False)


# --- Ligne de commande -------------------------------------------------------------------

def valider(annees, max_courses=None, journal=print):
    """Détecte et prédit les undercuts des saisons demandées, dans l'ordre chronologique."""
    from course import charger_session, courses_de_la_saison

    a_priori, tous = APriori(), []
    liste = [(annee, m, s) for annee in annees for m, s in courses_de_la_saison(annee)]
    for annee, meeting, session in liste[:max_courses]:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                course = charger_session(meeting, session, annee)
            modele_complet = ajuster_usure(course.tours, nb_bootstrap=0)
        except (ValueError, KeyError) as e:
            journal(f"  {meeting['meeting_name']} {annee} ignorée : {e}")
            continue
        cas = detecter_undercuts(course)
        if not cas.empty:
            resultats = predire_undercuts(course, cas, a_priori)
            tous.append(resultats)
            predits = resultats["probabilite"].notna().sum()
            journal(f"  {course.titre} : {len(cas)} undercut(s), {predits} prédit(s)")
        a_priori.ajouter(modele_complet)  # la course ne sert d'a priori qu'aux suivantes
    return pd.concat(tous, ignore_index=True) if tous else pd.DataFrame()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vérification du calculateur d'undercut sur l'historique.")
    parser.add_argument("debut", type=int, help="première saison, ex : 2023")
    parser.add_argument("fin", type=int, help="dernière saison, ex : 2025")
    parser.add_argument("--max-courses", type=int, help="limite le nombre de courses (essai rapide)")
    parser.add_argument("--csv", help="refait le rapport à partir de résultats déjà enregistrés")
    parser.add_argument("--no-show", action="store_true")
    args = parser.parse_args(argv)

    if args.csv:
        resultats = pd.read_csv(args.csv)
        resultats[["a_priori", "ignore"]] = resultats[["a_priori", "ignore"]].fillna("")
    else:
        resultats = valider(range(args.debut, args.fin + 1), args.max_courses)
    if resultats.empty:
        print("Aucun undercut détecté.")
        return
    predits = resultats.dropna(subset=["probabilite"])
    print(f"\n{len(resultats)} undercuts détectés, {len(predits)} prédits "
          f"({predits['reussi'].mean():.0%} réussis en vrai)")
    for raison, n in resultats.loc[resultats["probabilite"].isna(), "ignore"].value_counts().items():
        print(f"  non prédits : {n} × {raison}")
    print(f"  dont {(predits['a_priori'] != '').sum()} avec un composé tiré de l'a priori")

    print("\n" + mesurer(resultats).to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\nErreur moyenne sur l'écart final : "
          f"{np.mean(np.abs(predits['ecart_predit'] - predits['ecart_reel'])):.2f} s "
          f"(biais {np.mean(predits['ecart_predit'] - predits['ecart_reel']):+.2f} s)")
    print(f"Erreur médiane : {np.median(np.abs(predits['ecart_predit'] - predits['ecart_reel'])):.2f} s "
          f"(la moyenne est gonflée par quelques incidents : pénalité purgée à l'arrêt, arrêt raté)")
    print("\nCalibration :")
    print(calibration(resultats).to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print("\nPar délai de réaction du pilote devant :")
    print(par_groupe(predits, "reaction").to_string(float_format=lambda v: f"{v:.2f}"))
    print("\nPar saison :")
    print(par_groupe(predits, "annee").to_string(float_format=lambda v: f"{v:.2f}"))

    nom = f"validation_undercuts_{args.debut}_{args.fin}"
    resultats.to_csv(f"{nom}.csv", index=False, encoding="utf-8-sig")
    titre = f"Calculateur d'undercut : {len(predits)} undercuts réels, saisons {args.debut}-{args.fin}"
    tracer_calibration(resultats, titre).savefig(f"{nom}.png", dpi=150)
    print(f"\nRésultats enregistrés : {nom}.csv, {nom}.png")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
