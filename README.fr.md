# Comparaison de télémétrie F1

[English](README.md) · **Français**

Compare le meilleur tour de deux pilotes de Formule 1 et explique **où et pourquoi** l'un est plus rapide que l'autre, virage par virage.

À partir des données publiques de chronométrage, le script produit :

- un **graphique de télémétrie** : vitesse, écart cumulé, accélérateur, frein et rapport, en fonction de la distance ;
- une **carte du circuit** colorée selon le pilote le plus rapide dans chaque zone ;
- un **tableau par zone** (temps gagné, vitesses mini et maxi, point de freinage), exporté en CSV ;
- une **conclusion rédigée automatiquement**.

![Carte de Monza 2024 : pilote le plus rapide par zone](docs/images/monza_2024_q_lec_nor_carte.png)

---

## Exemple : Monza 2024, qualifications, Leclerc contre Norris

Norris signe la pole en **1:19.327**. Leclerc se qualifie 4e en **1:19.461**, à **0,134 s**. Les deux tours sont faits en pneus tendres.

![Télémétrie de Monza 2024 : LEC contre NOR](docs/images/monza_2024_q_lec_nor_telemetrie.png)

| Zone | Plus rapide | Gain (s) | Vmin LEC / NOR (km/h) | Vmax LEC / NOR (km/h) | Freinage LEC / NOR (m) |
|---|---|---|---|---|---|
| Départ → T1-T2 (Rettifilo) | LEC | 0,061 | 322 / 321 | 351 / 347 | — |
| T1-T2 (Rettifilo) | NOR | 0,006 | 75 / 73 | 303 / 299 | 743 / 766 |
| T3 (Curva Grande) | LEC | 0,049 | 303 / 302 | 330 / 325 | — |
| T4-T5 (Roggia) | NOR | 0,035 | 111 / 113 | 263 / 263 | 1986 / 1979 |
| T6 (Lesmo 1) | NOR | 0,068 | 204 / 207 | 268 / 269 | 2409 / 2410 |
| T7 (Lesmo 2) | NOR | 0,061 | 187 / 190 | 342 / 338 | 2748 / 2758 |
| T8-T10 (Ascari) | NOR | 0,055 | 191 / 194 | 334 / 329 | 3785 / 3814 |
| T11 (Parabolica) | NOR | 0,018 | 209 / 215 | 321 / 317 | 4986 / 5005 |

*Vmin : vitesse au point le plus lent de la zone. Vmax : vitesse en fin de ligne droite après le virage. Freinage : distance depuis la ligne de départ.*

### Ce qu'on en tire

- **Leclerc va plus vite en ligne droite.** Il est 4 à 5 km/h plus rapide en fin de ligne droite dans 6 zones sur 8 (351 contre 347 km/h avant le Rettifilo). Il gagne ainsi **0,110 s**, uniquement dans les deux zones les plus rapides du circuit : la ligne droite de départ et la Curva Grande.
- **Norris va plus vite en courbe.** Il passe 3 à 6 km/h plus vite au point le plus lent des virages moyens et rapides (Lesmo, Ascari, Parabolica). Il freine aussi plus de 10 m plus tard dans 4 des 6 freinages : 24 m au Rettifilo, 10 m à Lesmo 2, 29 m à Ascari, 18 m à la Parabolica. Il gagne ainsi **0,243 s** sur six zones.
- **Bilan** : ce que Norris gagne en courbe vaut plus que ce que Leclerc gagne en ligne droite, d'où 0,134 s à l'arrivée (0,243 − 0,110, aux arrondis près). Ce profil correspond à une Ferrari réglée avec moins d'appui (plus rapide en ligne droite, moins en courbe) face à une McLaren plus à l'aise dans les virages. C'est une hypothèse : un seul tour par pilote ne permet pas de séparer l'effet de la voiture de celui du pilote.

> La conclusion produite par le script : « NOR devance LEC de 0.134 s. NOR est plus rapide dans 6 zone(s) sur 8, surtout T6 (+0.068 s) : il passe 3 km/h plus vite au point le plus lent. LEC est plus rapide dans 2 zone(s) sur 8, surtout Départ → T1-T2 (+0.061 s) : il atteint 4 km/h de plus en fin de ligne droite. »

### Autre exemple : Monaco 2024, Leclerc contre Piastri

![Carte de Monaco 2024 : pilote le plus rapide par zone](docs/images/monaco_2024_q_lec_pia_carte.png)

---

## Installation

Il faut Python 3.11 ou plus récent.

```bash
git clone <url-du-depot>
cd <dossier-du-depot>
pip install -r requirements.txt
```

## Utilisation

```bash
# Valeurs par défaut : Monza 2024, qualifications, LEC contre NOR
python comparaison_telemetrie.py

# Année, Grand Prix, session, pilote 1, pilote 2
python comparaison_telemetrie.py 2024 Monaco Q LEC PIA
python comparaison_telemetrie.py 2024 "Abu Dhabi" R LEC SAI

# Enregistrer les images sans ouvrir de fenêtre
python comparaison_telemetrie.py 2024 Monza Q LEC NOR --no-show

# Toutes les options
python comparaison_telemetrie.py --help
```

| Argument | Valeurs | Défaut |
|---|---|---|
| `annee` | 2023 et après avec OpenF1 | `2024` |
| `grand_prix` | nom du lieu, du circuit ou du Grand Prix (`Monza`, `Silverstone`, `"Abu Dhabi"`…) | `Monza` |
| `session` | `FP1`, `FP2`, `FP3`, `Q`, `SQ` (qualifs sprint), `S` (sprint), `R` (course) | `Q` |
| `pilote_1`, `pilote_2` | code à 3 lettres (`LEC`, `NOR`, `VER`…) | `LEC`, `NOR` |
| `--source` | `openf1` ou `fastf1` | `openf1` |
| `--no-show` | enregistre les images sans les afficher | désactivé |

Le script affiche le tableau et la conclusion dans le terminal et crée trois fichiers : `telemetrie_<…>.png`, `carte_<…>.png` et `analyse_<…>.csv`. Les données téléchargées sont gardées en cache (`cache_openf1/`), donc un deuxième lancement est instantané.

---

## Comment ça marche

```
sources.py           Chargement des données (OpenF1 ou FastF1), même format en sortie
   │
analyse.py           Écart cumulé, découpage en zones, bilan et conclusion
   │
comparaison_telemetrie.py   Ligne de commande, graphiques et carte
```

- **Meilleur tour** : le tour le plus rapide de chaque pilote, sans les sorties des stands ni les tours supprimés par la direction de course (limites de piste), retrouvés dans ses messages.
- **Distance** : obtenue en intégrant la vitesse. Le tour du pilote 2 est ramené à la longueur de celui du pilote 1 pour comparer les points de freinage au même endroit.
- **Recalage** : OpenF1 ne donne qu'une heure de début de tour approximative, ce qui peut décaler les deux tours de 10 à 20 m. Au freinage, ce décalage crée de faux écarts de plusieurs dixièmes. Le script le corrige en cherchant le décalage qui superpose le mieux les deux courbes de vitesse.
- **Écart cumulé** : les chronos des deux pilotes sont comparés à distance égale, sur une grille commune. Chaque tour est fixé à 0 s au départ et à son temps officiel à l'arrivée, donc la courbe finit exactement sur l'écart officiel.
- **Zones** : une par virage, ou par groupe de virages à moins de 110 m (chicanes). Une zone va de la vitesse maximale avant le virage jusqu'à celle avant le virage suivant : freinage, virage, sortie et ligne droite. Les gains de toutes les zones s'additionnent donc exactement à l'écart total.
- **Point de freinage** : premier passage « pas de frein → frein » dans les 400 m avant le virage, avec au moins 0,5 s sans frein juste avant. Le signal de frein « clignote » parfois pendant un freinage, et ce critère évite de compter ces coupures.
- **Carte** : la trajectoire réelle du pilote 1, orientée comme le plan officiel du circuit, colorée zone par zone.

### Limites

- La télémétrie publique n'a qu'**environ 4 points par seconde**. Un point de freinage n'est donc connu qu'à une dizaine de mètres près, et la conclusion ignore les écarts de moins de 10 m ou de 2 km/h.
- **Un seul tour par pilote** : le trafic, le vent ou un tour imparfait pèsent sur le résultat.
- OpenF1 ne couvre que les saisons **2023 et après**.

---

## Données

- **[OpenF1](https://openf1.org)** (source par défaut) : API publique et gratuite, à partir de 2023. Elle fournit les tours, la télémétrie, les positions, les pneus et les messages de la direction de course.
- **[MultiViewer](https://multiviewer.app)** : position des virages et orientation des circuits.
- **[FastF1](https://github.com/theOehrly/Fast-F1)** (`--source fastf1`) : elle remonte avant 2023, mais depuis le 15 octobre 2026 l'archive F1 qu'elle utilise exige une authentification ([Fast-F1#978](https://github.com/theOehrly/Fast-F1/issues/978)). OpenF1 est donc la source par défaut en attendant un correctif.

## Tests et qualité

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

Les 27 tests tournent **sans réseau**, en moins d'une seconde :

- l'analyse est vérifiée sur des tours synthétiques dont le résultat est connu d'avance ;
- la source OpenF1 est testée avec des réponses d'API simulées : tours supprimés, cache, nouvelles tentatives après une erreur 429 (trop de requêtes), recalage.

Le workflow GitHub Actions ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)) lance ruff et les tests à chaque push, sur Python 3.11, 3.12 et 3.13.

## Structure

```
├── comparaison_telemetrie.py   Point d'entrée : arguments, graphiques, carte
├── analyse.py                  Écart cumulé, zones, bilan, conclusion
├── sources.py                  Chargement OpenF1 / FastF1
├── tests/                      Tests pytest (sans réseau)
├── docs/images/                Images du README
├── requirements.txt            Dépendances
├── requirements-dev.txt        Outils de test et de lint
└── pyproject.toml              Configuration de ruff et pytest
```

---

Projet personnel de Rayan Bouyacoub, étudiant en cybersécurité, qui s'intéresse aux métiers du développement et de la data en Formule 1.

*Projet non officiel, sans lien avec la Formule 1, la FIA ou les écuries. F1 et FORMULA 1 sont des marques de Formula One Licensing B.V.*
