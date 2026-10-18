"""
Aide à la stratégie des arrêts aux stands en F1 : point d'entrée unique.

    python strategie.py course     2024 Monza               tableau tour par tour nettoyé
    python strategie.py usure      2024 Monza               usure des pneus par composé et par écurie
    python strategie.py duel       2024 Monza 13 NOR LEC    undercut de NOR sur LEC, décision au tour 13
    python strategie.py fenetre    2024 Monza LEC           meilleure stratégie d'arrêts de LEC
    python strategie.py fenetre    2023 Singapore SAI --depuis 19 --sc 20
                                                            faut-il s'arrêter sous voiture de sécurité ?
    python strategie.py validation 2023 2025                vérification sur les undercuts réels

Chaque commande accepte --help pour ses options.
"""

import importlib
import sys

# commande -> module qui la traite
COMMANDES = {"course": "course", "usure": "usure", "duel": "undercut", "fenetre": "undercut",
             "validation": "validation"}


def arguments_du_module(commande, reste):
    """Arguments à passer au module : undercut.py attend « annee grand_prix duel|fenetre ... »."""
    if COMMANDES[commande] != "undercut":
        return reste
    if any(a in ("-h", "--help") for a in reste) and len(reste) < 2:
        return ["0", "-", commande, "--help"]  # aide de la sous-commande seule
    return reste[:2] + [commande] + reste[2:]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] not in COMMANDES:
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    commande, reste = argv[0], argv[1:]
    module = importlib.import_module(COMMANDES[commande])
    module.main(arguments_du_module(commande, reste))
    return 0


if __name__ == "__main__":
    sys.exit(main())
