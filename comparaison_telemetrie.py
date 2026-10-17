"""
Projet F1 n°1 : comparer la télémétrie de deux pilotes sur leur meilleur tour.

Installation :
    pip install -r requirements.txt

Utilisation :
    python comparaison_telemetrie.py                      # valeurs par défaut
    python comparaison_telemetrie.py 2024 Monza Q LEC NOR # année, GP, session, pilote 1, pilote 2
    python comparaison_telemetrie.py --help

Sessions possibles : FP1, FP2, FP3, Q (qualifs), S (sprint), R (course)
"""

import argparse
import os

import fastf1
import fastf1.plotting
import matplotlib.pyplot as plt
import numpy as np

DOSSIER_CACHE = "cache_f1"
NB_POINTS_ECART = 2000  # résolution de la grille commune pour la courbe d'écart


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
    parser.add_argument(
        "--no-show", action="store_true",
        help="enregistre le PNG sans ouvrir la fenêtre du graphique",
    )
    args = parser.parse_args()
    args.pilote_1 = args.pilote_1.upper()
    args.pilote_2 = args.pilote_2.upper()
    return args


def charger_session(annee, grand_prix, type_session):
    """Charge une session FastF1 (avec cache pour ne pas tout retélécharger)."""
    os.makedirs(DOSSIER_CACHE, exist_ok=True)
    fastf1.Cache.enable_cache(DOSSIER_CACHE)
    print(f"Chargement : {grand_prix} {annee} - {type_session} ...")
    session = fastf1.get_session(annee, grand_prix, type_session)
    session.load(weather=False, messages=False)
    return session


def meilleur_tour(session, pilote):
    """Récupère le meilleur tour d'un pilote et sa télémétrie (avec la distance)."""
    tour = session.laps.pick_drivers(pilote).pick_fastest()
    # Selon la version de FastF1, l'absence de tour donne None ou un tour vide
    if tour is None or tour.empty:
        raise SystemExit(f"Aucun tour trouvé pour {pilote} dans cette session.")
    telemetrie = tour.get_car_data().add_distance()
    return tour, telemetrie


def couleur(session, pilote, defaut):
    try:
        return fastf1.plotting.get_driver_color(pilote, session=session)
    except Exception:
        return defaut


def format_temps(td):
    s = td.total_seconds()
    return f"{int(s // 60)}:{s % 60:06.3f}"


def calcul_ecart(tel_1, tel_2):
    """
    Écart cumulé du pilote 2 par rapport au pilote 1, en fonction de la distance.

    Les deux tours n'ont pas exactement la même longueur (trajectoire différente,
    distance obtenue en intégrant la vitesse) : on ramène chaque tour sur la même
    longueur, puis on interpole le chrono de chacun sur une grille de distance commune.
    Résultat > 0 : le pilote 2 est derrière (le pilote 1 a gagné du temps jusque-là).
    """
    longueur = tel_1["Distance"].max()
    d1 = tel_1["Distance"].to_numpy()
    d2 = tel_2["Distance"].to_numpy() / tel_2["Distance"].max() * longueur
    t1 = tel_1["Time"].dt.total_seconds().to_numpy()
    t2 = tel_2["Time"].dt.total_seconds().to_numpy()

    grille = np.linspace(0, longueur, NB_POINTS_ECART)
    ecart = np.interp(grille, d2, t2) - np.interp(grille, d1, t1)
    return grille, ecart


def tracer_virages(axes, virages):
    """Lignes verticales à chaque virage, avec le numéro au-dessus du graphe de vitesse."""
    for _, v in virages.iterrows():
        for ax in axes:
            ax.axvline(v["Distance"], color="grey", linestyle=":", linewidth=0.8, alpha=0.6)
        axes[0].text(
            v["Distance"], 1.01, f"T{v['Number']}{v['Letter']}",
            transform=axes[0].get_xaxis_transform(),
            ha="center", va="bottom", fontsize=8, color="grey",
        )


def tracer(session, args, tour_1, tel_1, tour_2, tel_2):
    p1, p2 = args.pilote_1, args.pilote_2
    c1 = couleur(session, p1, "tab:red")
    c2 = couleur(session, p2, "tab:orange")
    if c1 == c2:  # coéquipiers : même couleur d'écurie
        c2 = "white"

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
        ax.plot(tel_1["Distance"], tel_1[canal].astype(float), color=c1, label=p1)
        ax.plot(tel_2["Distance"], tel_2[canal].astype(float), color=c2, label=p2)
        ax.set_ylabel(label)

    # Courbe d'écart : la zone colorée indique qui est devant à cet endroit du tour
    distance, ecart = calcul_ecart(tel_1, tel_2)
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

    tracer_virages(axes, session.get_circuit_info().corners)

    gap = (tour_2["LapTime"] - tour_1["LapTime"]).total_seconds()
    fig.suptitle(
        f"{session.event['EventName']} {args.annee} - {session.name}\n"
        f"{p1} {format_temps(tour_1['LapTime'])} ({tour_1['Compound']})  vs  "
        f"{p2} {format_temps(tour_2['LapTime'])} ({tour_2['Compound']})  ({gap:+.3f} s)"
    )
    fig.tight_layout()
    return fig


def main():
    args = lire_arguments()
    session = charger_session(args.annee, args.grand_prix, args.session)
    fastf1.plotting.setup_mpl(color_scheme="fastf1")

    tour_1, tel_1 = meilleur_tour(session, args.pilote_1)
    tour_2, tel_2 = meilleur_tour(session, args.pilote_2)

    gap = (tour_2["LapTime"] - tour_1["LapTime"]).total_seconds()
    print(f"{args.pilote_1} : {format_temps(tour_1['LapTime'])} ({tour_1['Compound']})")
    print(f"{args.pilote_2} : {format_temps(tour_2['LapTime'])} ({tour_2['Compound']})"
          f"  ({gap:+.3f} s)")

    fig = tracer(session, args, tour_1, tel_1, tour_2, tel_2)

    gp = args.grand_prix.replace(" ", "_")
    fichier = f"telemetrie_{args.annee}_{gp}_{args.session}_{args.pilote_1}_vs_{args.pilote_2}.png"
    fig.savefig(fichier, dpi=150)
    print(f"Graphique enregistré : {fichier}")
    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
