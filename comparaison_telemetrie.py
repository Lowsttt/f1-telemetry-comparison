"""
Projet F1 n°1 : comparer la télémétrie de deux pilotes sur leur meilleur tour.

Installation :
    pip install -r requirements.txt

Utilisation :
    python comparaison_telemetrie.py                      # valeurs par défaut
    python comparaison_telemetrie.py 2024 Monza Q LEC NOR # année, GP, session, pilote 1, pilote 2
    python comparaison_telemetrie.py --source fastf1      # source de données
    python comparaison_telemetrie.py --help

Sessions possibles : FP1, FP2, FP3, Q (qualifs), SQ (qualifs sprint), S (sprint), R (course)
"""

import argparse

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import patheffects
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D

from analyse import analyser_zones, calcul_ecart, conclusion, harmoniser_distances, tableau_lisible
from sources import SOURCES


def lire_arguments():
    parser = argparse.ArgumentParser(
        description="Compare la télémétrie de deux pilotes sur leur meilleur tour."
    )
    parser.add_argument("annee", nargs="?", type=int, default=2024, help="ex : 2024")
    parser.add_argument("grand_prix", nargs="?", default="Monza", help='ex : Monza, "Abu Dhabi"')
    parser.add_argument(
        "session", nargs="?", default="Q",
        choices=["FP1", "FP2", "FP3", "Q", "SQ", "S", "R"], help="type de session",
    )
    parser.add_argument("pilote_1", nargs="?", default="LEC", help="code à 3 lettres, ex : LEC")
    parser.add_argument("pilote_2", nargs="?", default="NOR", help="code à 3 lettres, ex : NOR")
    # OpenF1 par défaut : l'archive utilisée par FastF1 exige une authentification
    # depuis le 15/10/2026 (https://github.com/theOehrly/Fast-F1/issues/978)
    parser.add_argument(
        "--source", choices=sorted(SOURCES), default="openf1",
        help="origine des données (défaut : openf1, à partir de 2023)",
    )
    parser.add_argument(
        "--no-show", action="store_true",
        help="enregistre le PNG sans ouvrir la fenêtre du graphique",
    )
    args = parser.parse_args()
    args.pilote_1 = args.pilote_1.upper()
    args.pilote_2 = args.pilote_2.upper()
    return args


def format_temps(secondes):
    return f"{int(secondes // 60)}:{secondes % 60:06.3f}"


def tracer_virages(axes, virages):
    """Lignes verticales à chaque virage, avec le numéro au-dessus du graphe de vitesse."""
    precedent, decale = -np.inf, False
    for _, v in virages.sort_values("Distance").iterrows():
        for ax in axes:
            ax.axvline(v["Distance"], color="grey", linestyle=":", linewidth=0.8, alpha=0.6)
        # Virages rapprochés (chicanes) : on alterne la hauteur des étiquettes
        decale = not decale and v["Distance"] - precedent < 120
        precedent = v["Distance"]
        axes[0].text(
            v["Distance"], 1.06 if decale else 1.01, f"T{v['Number']}{v['Letter']}",
            transform=axes[0].get_xaxis_transform(),
            ha="center", va="bottom", fontsize=8, color="grey",
        )


def couleurs_pilotes(comparaison):
    tour_1, tour_2 = comparaison.tours
    c1 = tour_1.couleur or "tab:red"
    c2 = tour_2.couleur or "tab:orange"
    if c1.lower() == c2.lower():  # coéquipiers : même couleur d'écurie
        c2 = "white"
    return c1, c2


def tracer(comparaison):
    tour_1, tour_2 = comparaison.tours
    p1, p2 = tour_1.pilote, tour_2.pilote
    c1, c2 = couleurs_pilotes(comparaison)

    plt.style.use("dark_background")
    fig, axes = plt.subplots(
        5, 1, figsize=(13, 12), sharex=True,
        gridspec_kw={"height_ratios": [3, 1.5, 1.5, 1, 1.2]},
    )
    ax_vitesse, ax_ecart, ax_accel, ax_frein, ax_rapport = axes

    canaux = [
        (ax_vitesse, "Speed", "Vitesse (km/h)"),
        (ax_accel, "Throttle", "Accélérateur (%)"),
        (ax_frein, "Brake", "Frein"),
        (ax_rapport, "nGear", "Rapport"),
    ]
    for ax, canal, label in canaux:
        ax.plot(tour_1.tel["Distance"], tour_1.tel[canal].astype(float), color=c1, label=p1)
        ax.plot(tour_2.tel["Distance"], tour_2.tel[canal].astype(float), color=c2, label=p2)
        ax.set_ylabel(label)

    # Courbe d'écart : la zone colorée indique qui est devant à cet endroit du tour
    distance, ecart = calcul_ecart(tour_1, tour_2)
    ax_ecart.plot(distance, ecart, color="white", linewidth=1)
    ax_ecart.axhline(0, color="grey", linewidth=0.8)
    ax_ecart.fill_between(distance, ecart, 0, where=ecart >= 0, color=c1, alpha=0.4,
                          label=f"{p1} devant")
    ax_ecart.fill_between(distance, ecart, 0, where=ecart < 0, color=c2, alpha=0.4,
                          label=f"{p2} devant")
    ax_ecart.set_ylabel(f"Écart {p2}\nvs {p1} (s)")
    ax_ecart.legend(loc="upper left", fontsize=8)

    for ax in axes:
        ax.grid(alpha=0.3)
    ax_frein.set_yticks([0, 1], ["off", "on"])
    ax_vitesse.legend(loc="lower right")
    axes[-1].set_xlabel("Distance (m)")

    tracer_virages(axes, comparaison.virages)

    gap = tour_2.temps - tour_1.temps
    fig.suptitle(
        f"{comparaison.titre}\n"
        f"{p1} {format_temps(tour_1.temps)} ({tour_1.pneu})  vs  "
        f"{p2} {format_temps(tour_2.temps)} ({tour_2.pneu})  ({gap:+.3f} s)"
    )
    fig.tight_layout()
    return fig


def tourner(x, y, angle_deg):
    a = np.radians(angle_deg)
    return x * np.cos(a) - y * np.sin(a), x * np.sin(a) + y * np.cos(a)


def tracer_carte(comparaison, bilan):
    """Carte du circuit : chaque zone prend la couleur du pilote le plus rapide."""
    tour_1, tour_2 = comparaison.tours
    couleurs = dict(zip((tour_1.pilote, tour_2.pilote), couleurs_pilotes(comparaison), strict=True))

    # Trajectoire du pilote 1, refermée sur la ligne et rééchantillonnée finement
    trace = comparaison.trace.sort_values("Distance")
    x, y = tourner(trace["X"].to_numpy(), trace["Y"].to_numpy(), comparaison.rotation)
    d = trace["Distance"].to_numpy()
    longueur = bilan["fin"].iloc[-1]
    # Circuit en boucle : on prolonge d'un point de chaque côté de la ligne pour que
    # l'interpolation soit continue entre 0 m et le 1er point de position
    x = np.concatenate([[x[-1]], x, [x[0]]])
    y = np.concatenate([[y[-1]], y, [y[0]]])
    d = np.concatenate([[d[-1] - longueur], d, [d[0] + longueur]])
    grille = np.linspace(0, longueur, 3000)
    xs, ys = np.interp(grille, d, x), np.interp(grille, d, y)

    # Un segment par pas de la grille, coloré selon la zone qui le contient
    milieux = (grille[:-1] + grille[1:]) / 2
    zone = np.clip(np.searchsorted(bilan["fin"].to_numpy(), milieux), 0, len(bilan) - 1)
    segments = np.stack([np.column_stack([xs[:-1], ys[:-1]]),
                         np.column_stack([xs[1:], ys[1:]])], axis=1)
    couleur_segments = bilan["plus_rapide"].map(couleurs).to_numpy()[zone]

    # Figure aux proportions du circuit (place en bas pour la légende)
    largeur, hauteur = np.ptp(xs), np.ptp(ys)
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(12, float(np.clip(12 * hauteur / largeur, 5, 12)) + 2))
    ax.plot(xs, ys, color="#333333", linewidth=12, solid_capstyle="round", zorder=1)
    ax.add_collection(LineCollection(segments, colors=couleur_segments, linewidth=6,
                                     capstyle="round", zorder=2))

    # Étiquettes : de part et d'autre de la piste (normale au tracé), on garde le côté
    # le plus éloigné du reste du circuit pour ne pas écrire sur un autre bout de piste
    taille = max(largeur, hauteur)
    points = np.column_stack([xs, ys])
    dx, dy = np.gradient(xs), np.gradient(ys)
    normale = np.column_stack([dy, -dx]) / np.hypot(dx, dy)[:, None]
    positions_etiquettes = []

    def position_libre(distance, decalage=0.05):
        """Position proche de la piste mais dégagée : on essaie les deux côtés, puis
        un décalage plus grand, et on garde le 1er candidat assez loin de la piste."""
        i = min(np.searchsorted(grille, distance), len(grille) - 1)
        candidats = [points[i] + signe * normale[i] * k * decalage * taille
                     for k in (1, 1.7) for signe in (1, -1)]
        degagement = [np.hypot(*(points - p).T).min() for p in candidats]
        # Dégagement ~ décalage : aucun autre bout de piste n'est plus proche que le nôtre
        assez_loin = [p for p, deg in zip(candidats, degagement, strict=True)
                      if deg >= 0.9 * decalage * taille]
        meilleur = assez_loin[0] if assez_loin else candidats[int(np.argmax(degagement))]
        positions_etiquettes.append(meilleur)
        return meilleur

    for _, z in bilan.iterrows():
        ax.text(*position_libre(z["repere"]), f"{z['zone']}\n+{z['gain']:.3f}",
                ha="center", va="center", fontsize=8, linespacing=1.1,
                color=couleurs[z["plus_rapide"]], fontweight="bold", zorder=3,
                # Contour noir : reste lisible même si l'étiquette touche la piste
                path_effects=[patheffects.withStroke(linewidth=3, foreground="black")])

    # Ligne de départ / arrivée
    ax.plot(xs[0], ys[0], marker="s", markersize=8, color="white", zorder=4)
    ax.annotate("Départ", (xs[0], ys[0]), xytext=position_libre(0, 0.03), color="white",
                fontsize=9, ha="center", va="center", zorder=4)

    # Cadre élargi pour que les étiquettes ne soient pas coupées
    tous = np.vstack([points, positions_etiquettes])
    marge = 0.04 * taille
    ax.set_xlim(tous[:, 0].min() - marge, tous[:, 0].max() + marge)
    ax.set_ylim(tous[:, 1].min() - marge, tous[:, 1].max() + marge)
    ax.set_aspect("equal")
    ax.axis("off")

    legende = []
    for tour in (tour_1, tour_2):
        zones = bilan[bilan["plus_rapide"] == tour.pilote]
        legende.append(Line2D([0], [0], color=couleurs[tour.pilote], linewidth=6,
                              label=f"{tour.pilote} plus rapide : {len(zones)} zone(s), "
                                    f"{zones['gain'].sum():.3f} s gagnées"))
    fig.legend(handles=legende, loc="lower center", ncol=2, fontsize=10, frameon=False)

    gap = tour_2.temps - tour_1.temps
    fig.suptitle(f"{comparaison.titre}\n{tour_1.pilote} vs {tour_2.pilote} ({gap:+.3f} s) : "
                 f"pilote le plus rapide par zone", fontsize=13)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return fig


def main():
    args = lire_arguments()
    charger = SOURCES[args.source]
    comparaison = charger(args.annee, args.grand_prix, args.session, [args.pilote_1, args.pilote_2])

    tour_1, tour_2 = comparaison.tours
    tour_2 = harmoniser_distances(tour_1, tour_2)
    comparaison.tours = [tour_1, tour_2]
    print(f"{tour_1.pilote} : {format_temps(tour_1.temps)} ({tour_1.pneu})")
    print(f"{tour_2.pilote} : {format_temps(tour_2.temps)} ({tour_2.pneu})"
          f"  ({tour_2.temps - tour_1.temps:+.3f} s)")

    gp = args.grand_prix.replace(" ", "_")
    nom = f"{args.annee}_{gp}_{args.session}_{args.pilote_1}_vs_{args.pilote_2}"
    if comparaison.virages.empty:
        print("Pas de repères de virages : analyse par zone impossible.")
    else:
        bilan = analyser_zones(tour_1, tour_2, comparaison.virages)
        print()
        print(tableau_lisible(bilan, tour_1.pilote, tour_2.pilote).to_string(index=False))
        print()
        print(conclusion(bilan, tour_1, tour_2))
        # utf-8-sig (avec BOM) : sinon Excel affiche mal les accents
        bilan.round(3).to_csv(f"analyse_{nom}.csv", index=False, encoding="utf-8-sig")
        print(f"\nTableau enregistré : analyse_{nom}.csv")

        if comparaison.trace.empty:
            print("Pas de trajectoire disponible : pas de carte du circuit.")
        else:
            tracer_carte(comparaison, bilan).savefig(f"carte_{nom}.png", dpi=150)
            print(f"Carte enregistrée : carte_{nom}.png")

    fig = tracer(comparaison)
    fichier = f"telemetrie_{nom}.png"
    fig.savefig(fichier, dpi=150)
    print(f"Graphique enregistré : {fichier}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
