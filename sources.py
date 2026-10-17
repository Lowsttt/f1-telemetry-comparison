"""
Sources de données pour la comparaison de télémétrie.

Chaque source renvoie un objet `Comparaison` identique, pour que l'analyse et le
graphique ne dépendent pas de l'origine des données :
    - openf1 : API publique OpenF1 (https://openf1.org), données depuis 2023
    - fastf1 : bibliothèque FastF1 (archive officielle F1 live timing)
"""

import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

COLONNES_TELEMETRIE = ["Distance", "Time", "Speed", "Throttle", "Brake", "nGear"]


@dataclass
class TourPilote:
    pilote: str
    temps: float  # durée du tour en secondes
    pneu: str
    couleur: str
    tel: pd.DataFrame  # COLONNES_TELEMETRIE : Distance (m), Time (s depuis le début du tour)


@dataclass
class Comparaison:
    titre: str  # ex : "Italian Grand Prix 2024 - Qualifying"
    tours: list  # [TourPilote, TourPilote]
    virages: pd.DataFrame  # colonnes Number, Letter, Distance (m) ; peut être vide
    # Trajectoire du pilote 1 pour la carte : colonnes Distance (m), X, Y ; peut être vide
    trace: pd.DataFrame = field(default_factory=pd.DataFrame)
    rotation: float = 0.0  # angle (degrés) pour orienter la carte comme le plan officiel


# --- OpenF1 ------------------------------------------------------------------

URL_OPENF1 = "https://api.openf1.org/v1"
URL_MULTIVIEWER = "https://api.multiviewer.app/api/v1/circuits/{circuit}/{annee}"
DOSSIER_CACHE_OPENF1 = "cache_openf1"
NOMS_SESSIONS_OPENF1 = {
    "FP1": ["Practice 1"],
    "FP2": ["Practice 2"],
    "FP3": ["Practice 3"],
    "Q": ["Qualifying"],
    "SQ": ["Sprint Qualifying", "Sprint Shootout"],  # "Sprint Shootout" en 2023
    "S": ["Sprint"],
    "R": ["Race"],
}
# ex : "CAR 44 (HAM) LAP DELETED - TRACK LIMITS AT TURN 2 LAP 4 16:10:08"
REGEX_TOUR_SUPPRIME = re.compile(r"CAR (\d+) .*DELETED.*\bLAP (\d+)")


def _get_json(url):
    """GET avec cache disque : chaque URL n'est téléchargée qu'une seule fois."""
    os.makedirs(DOSSIER_CACHE_OPENF1, exist_ok=True)
    chemin = os.path.join(DOSSIER_CACHE_OPENF1, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)

    requete = urllib.request.Request(url, headers={"User-Agent": "comparaison-telemetrie"})
    for essai in range(6):
        try:
            with urllib.request.urlopen(requete, timeout=60) as reponse:
                donnees = json.load(reponse)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:  # OpenF1 répond 404 quand la requête ne trouve rien
                return []
            if e.code == 429 and essai < 5:  # trop de requêtes : on attend de plus en plus
                time.sleep(2 ** essai)
                continue
            raise

    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f)
    return donnees


def _openf1(endpoint, filtres=(), **params):
    """Requête OpenF1. `filtres` : conditions déjà écrites, ex : "date>=2024-08-31T15:03:09"."""
    morceaux = [f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()]
    morceaux += list(filtres)
    return _get_json(f"{URL_OPENF1}/{endpoint}?{'&'.join(morceaux)}")


def _date(texte):
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


def _filtres_dates(debut, fin):
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    return [f"date>={debut.strftime(fmt)}", f"date<={fin.strftime(fmt)}"]


def _sans_accents(texte):
    """« São Paulo » -> « sao paulo » : la recherche ne dépend ni des accents ni de la casse."""
    decompose = unicodedata.normalize("NFKD", str(texte))
    return "".join(c for c in decompose if not unicodedata.combining(c)).lower()


def _trouver_session(annee, grand_prix, type_session):
    cible = _sans_accents(grand_prix)
    champs = ("meeting_name", "meeting_official_name", "location", "country_name", "circuit_short_name")
    candidats = [
        m for m in _openf1("meetings", year=annee)
        if any(cible in _sans_accents(m.get(c, "")) for c in champs)
    ]
    # Un nom de pays peut aussi désigner les essais hivernaux : on préfère le Grand Prix
    candidats.sort(key=lambda m: "grand prix" not in m["meeting_name"].lower())
    if not candidats:
        raise SystemExit(f"Aucun Grand Prix « {grand_prix} » trouvé sur OpenF1 en {annee}.")
    meeting = candidats[0]
    autres = [m["meeting_name"] for m in candidats[1:] if "grand prix" in m["meeting_name"].lower()]
    if autres:  # ex : « Italy » désigne Imola et Monza
        print(f"« {grand_prix} » correspond aussi à : {', '.join(autres)}. "
              f"{meeting['meeting_name']} retenu, précise le lieu pour en choisir un autre.")

    noms = NOMS_SESSIONS_OPENF1[type_session]
    sessions = [s for s in _openf1("sessions", meeting_key=meeting["meeting_key"])
                if s["session_name"] in noms]
    if not sessions:
        raise SystemExit(f"Pas de session {type_session} pour {meeting['meeting_name']} {annee}.")
    return meeting, sessions[0]


def _tours_supprimes(session_key):
    """Tours annulés par la direction de course (limites de piste...) : {(numéro, tour)}."""
    supprimes = set()
    for m in _openf1("race_control", session_key=session_key):
        trouve = REGEX_TOUR_SUPPRIME.search(m.get("message") or "")
        if trouve:
            supprimes.add((int(trouve.group(1)), int(trouve.group(2))))
    return supprimes


def _telemetrie_openf1(session_key, numero, debut, fin):
    car = _openf1("car_data", _filtres_dates(debut, fin),
                  session_key=session_key, driver_number=numero)
    if not car:
        raise SystemExit(f"Pas de télémétrie OpenF1 pour la voiture n°{numero}.")
    df = pd.DataFrame(car)
    temps = (pd.to_datetime(df["date"], format="ISO8601") - debut).dt.total_seconds().to_numpy()
    vitesse = df["speed"].to_numpy() / 3.6  # m/s

    # Distance = intégrale de la vitesse (méthode des trapèzes), en partant du début du tour
    distance = np.concatenate([[0.0], np.cumsum((vitesse[1:] + vitesse[:-1]) / 2 * np.diff(temps))])
    distance += vitesse[0] * temps[0]

    return pd.DataFrame({
        "Distance": distance,
        "Time": temps,
        "Speed": df["speed"],
        "Throttle": df["throttle"],
        "Brake": df["brake"] > 0,
        "nGear": df["n_gear"],
    })


def _decalage_spatial(tel_ref, tel, max_m=150):
    """Décalage (m) à ajouter à `tel` pour superposer au mieux sa vitesse à celle de `tel_ref`."""
    grille = np.arange(max_m, tel_ref["Distance"].iloc[-1] - max_m, 2.0)
    v_ref = np.interp(grille, tel_ref["Distance"], tel_ref["Speed"])
    decalages = np.arange(-max_m, max_m + 1, 1.0)
    erreurs = [np.mean((np.interp(grille - s, tel["Distance"], tel["Speed"]) - v_ref) ** 2)
               for s in decalages]
    return decalages[np.argmin(erreurs)]


def _aligner_sur(tel_ref, tel, duree):
    """
    Recale un tour sur le tour de référence.

    OpenF1 ne donne qu'une heure de début de tour approximative : la fenêtre de
    télémétrie peut commencer quelques dizaines de mètres avant ou après la ligne.
    Au freinage, ce décalage crée de faux écarts de plusieurs dixièmes. On estime le
    décalage en superposant les courbes de vitesse, on corrige distance et temps,
    puis on retire les points qui tombent hors du tour.
    """
    decalage = _decalage_spatial(tel_ref, tel)
    tel = tel.copy()
    tel["Distance"] += decalage
    tel["Time"] += decalage / (tel["Speed"].iloc[0] / 3.6)
    tel = tel[(tel["Time"] > 0) & (tel["Time"] < duree)].reset_index(drop=True)
    return tel, decalage


def _circuit_multiviewer(circuit_key, annee):
    """Infos circuit de MultiViewer (virages, rotation de la carte), ou {} si indisponible."""
    try:
        return _get_json(URL_MULTIVIEWER.format(circuit=circuit_key, annee=annee))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"Infos circuit indisponibles ({e}) : pas de repères de virages.")
        return {}


def _trace_openf1(session_key, numero, debut, fin, tel):
    """Trajectoire (x, y) du tour, avec la distance parcourue tirée de la télémétrie."""
    positions = pd.DataFrame(_openf1("location", _filtres_dates(debut, fin),
                                     session_key=session_key, driver_number=numero))
    if positions.empty:
        return pd.DataFrame(columns=["Distance", "X", "Y"])
    t_pos = (pd.to_datetime(positions["date"], format="ISO8601") - debut).dt.total_seconds()
    return pd.DataFrame({
        "Distance": np.interp(t_pos, tel["Time"], tel["Distance"]),
        "X": positions["x"].astype(float),
        "Y": positions["y"].astype(float),
    })


def _virages_openf1(circuit, trace):
    """
    Position des virages en mètres depuis la ligne.

    MultiViewer donne les coordonnées (x, y) de chaque virage ; on cherche le point le
    plus proche sur la trajectoire du pilote 1, dont on connaît la distance parcourue.
    """
    if trace.empty or not circuit.get("corners"):
        return pd.DataFrame(columns=["Number", "Letter", "Distance"])

    xy = trace[["X", "Y"]].to_numpy()
    virages = []
    for v in circuit["corners"]:
        cible = np.array([v["trackPosition"]["x"], v["trackPosition"]["y"]])
        plus_proche = np.argmin(np.hypot(*(xy - cible).T))
        virages.append({"Number": v["number"], "Letter": v.get("letter") or "",
                        "Distance": trace["Distance"].iloc[plus_proche]})
    return pd.DataFrame(virages)


def charger_openf1(annee, grand_prix, type_session, pilotes):
    if annee < 2023:
        raise SystemExit("OpenF1 ne couvre que 2023 et après : utilise --source fastf1.")
    meeting, session = _trouver_session(annee, grand_prix, type_session)
    print(f"OpenF1 : {meeting['meeting_name']} {annee} - {session['session_name']}")

    session_key = session["session_key"]
    pilotes_session = {d["name_acronym"]: d for d in _openf1("drivers", session_key=session_key)}
    supprimes = _tours_supprimes(session_key)

    tours = []
    for pilote in pilotes:
        if pilote not in pilotes_session:
            raise SystemExit(f"{pilote} absent de la session. Pilotes : {', '.join(sorted(pilotes_session))}")
        infos = pilotes_session[pilote]
        numero = infos["driver_number"]

        laps = [
            tour for tour in _openf1("laps", session_key=session_key, driver_number=numero)
            if tour.get("lap_duration") and tour.get("date_start") and not tour.get("is_pit_out_lap")
            and (numero, tour["lap_number"]) not in supprimes
        ]
        if not laps:
            raise SystemExit(f"Aucun tour valide trouvé pour {pilote} dans cette session.")
        meilleur = min(laps, key=lambda tour: tour["lap_duration"])

        debut = _date(meilleur["date_start"])
        fin = debut + timedelta(seconds=meilleur["lap_duration"])
        tel = _telemetrie_openf1(session_key, numero, debut, fin)

        pneu = next(
            (s["compound"] for s in _openf1("stints", session_key=session_key, driver_number=numero)
             if s["lap_start"] <= meilleur["lap_number"] <= (s["lap_end"] or 999)),
            "?",
        )
        couleur = f"#{infos['team_colour']}" if infos.get("team_colour") else None
        if not tours:  # les virages sont repérés sur la trajectoire du pilote 1
            tour_reference = (numero, debut, fin, tel)
        else:
            tel, decalage = _aligner_sur(tours[0].tel, tel, meilleur["lap_duration"])
            print(f"Recalage de {pilote} sur {tours[0].pilote} : {decalage:+.0f} m")
        tours.append(TourPilote(pilote, meilleur["lap_duration"], pneu, couleur, tel))

    circuit = _circuit_multiviewer(session["circuit_key"], annee)
    trace = _trace_openf1(session_key, *tour_reference)
    virages = _virages_openf1(circuit, trace)
    titre = f"{meeting['meeting_name']} {annee} - {session['session_name']}"
    return Comparaison(titre, tours, virages, trace, circuit.get("rotation", 0.0))


# --- FastF1 ------------------------------------------------------------------

DOSSIER_CACHE_FASTF1 = "cache_f1"


def charger_fastf1(annee, grand_prix, type_session, pilotes):
    import fastf1  # import local : la source OpenF1 fonctionne sans FastF1
    import fastf1.plotting

    os.makedirs(DOSSIER_CACHE_FASTF1, exist_ok=True)
    fastf1.Cache.enable_cache(DOSSIER_CACHE_FASTF1)
    print(f"FastF1 : chargement {grand_prix} {annee} - {type_session} ...")
    session = fastf1.get_session(annee, grand_prix, type_session)
    # Les messages de direction de course servent à exclure les tours supprimés
    session.load(weather=False)
    try:
        session.laps  # noqa: B018 -- lève DataNotLoadedError si rien n'a été téléchargé
    except fastf1.exceptions.DataNotLoadedError as e:
        raise SystemExit(
            "FastF1 n'a pu télécharger aucune donnée. Depuis le 15/10/2026, l'archive F1 "
            "exige une authentification (https://github.com/theOehrly/Fast-F1/issues/978) : "
            "utilise --source openf1 ou mets FastF1 à jour."
        ) from e

    tours = []
    for pilote in pilotes:
        tour = session.laps.pick_drivers(pilote).pick_fastest()
        # Selon la version de FastF1, l'absence de tour donne None ou un tour vide
        if tour is None or tour.empty:
            raise SystemExit(f"Aucun tour trouvé pour {pilote} dans cette session.")
        tel = tour.get_car_data().add_distance()
        tel["Time"] = tel["Time"].dt.total_seconds()
        try:
            couleur = fastf1.plotting.get_driver_color(pilote, session=session)
        except Exception:
            couleur = None
        if not tours:  # trajectoire du pilote 1 (télémétrie fusionnée voiture + position)
            trace = tour.get_telemetry()[["Distance", "X", "Y"]]
        tours.append(TourPilote(pilote, tour["LapTime"].total_seconds(), str(tour["Compound"]),
                                couleur, tel[COLONNES_TELEMETRIE]))

    infos_circuit = session.get_circuit_info()
    virages = infos_circuit.corners[["Number", "Letter", "Distance"]]
    titre = f"{session.event['EventName']} {annee} - {session.name}"
    return Comparaison(titre, tours, virages, trace, infos_circuit.rotation)


SOURCES = {"openf1": charger_openf1, "fastf1": charger_fastf1}
