"""
Données de course pour l'outil de stratégie : un tableau propre, une ligne par pilote et par tour.

Chaque étape est une fonction qui prend les réponses brutes d'OpenF1 (listes de dicts),
ce qui permet de la tester sans réseau. `charger_course` enchaîne le tout.

Utilisation :
    python course.py 2024 Monza            # résumé : tours propres, exclusions, stratégies
    python course.py 2024 Monza --csv      # exporte aussi le tableau tour par tour
"""

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from sources import _circuit_multiviewer, _openf1, _trouver_session

COMPOSES_PLUIE = {"INTERMEDIATE", "WET"}
SEUIL_TOUR_LENT = 1.07  # un tour à plus de 107 % de la médiane du pilote est écarté
GRAVITE = {"VERT": 0, "VSC": 1, "SC": 2, "ROUGE": 3}  # pour garder l'état le plus grave d'un tour
TOLERANCE_ECART = pd.Timedelta(30, unit="s")  # les écarts sont publiés toutes les ~4 s


@dataclass
class Course:
    titre: str  # ex : "Italian Grand Prix 2024 - Race"
    annee: int
    session_key: int
    circuit_key: int
    perte_stand: dict  # temps perdu pour un arrêt (s) : {"normal", "sc", "vsc"} ; vide si inconnu
    neutralisations: pd.DataFrame  # colonnes type (SC / VSC / ROUGE), debut, fin (UTC)
    tours: pd.DataFrame  # une ligne par pilote et par tour, voir charger_course


def _dates(valeurs):
    return pd.to_datetime(pd.Series(valeurs), utc=True, format="ISO8601")


# --- Construction du tableau ---------------------------------------------------------

def construire_tours(laps, pilotes):
    """Tableau de base : pilote, tour, temps (s), début et fin du tour (UTC)."""
    df = pd.DataFrame(laps).reindex(
        columns=["driver_number", "lap_number", "lap_duration", "date_start", "is_pit_out_lap"])
    infos = {p["driver_number"]: p for p in pilotes}
    tours = pd.DataFrame({
        "numero": df["driver_number"].astype(int),
        "pilote": df["driver_number"].map(lambda n: infos.get(n, {}).get("name_acronym", str(n))),
        "equipe": df["driver_number"].map(lambda n: infos.get(n, {}).get("team_name")),
        "tour": df["lap_number"].astype(int),
        "temps": pd.to_numeric(df["lap_duration"], errors="coerce"),
        "debut": _dates(df["date_start"]),
        "sortie_api": df["is_pit_out_lap"].fillna(False).astype(bool),
    }).sort_values(["numero", "tour"]).reset_index(drop=True)

    # Fin du tour = début + durée ; à défaut, début du tour suivant du même pilote.
    # Début manquant (fréquent au 1er tour) = fin - durée.
    debut_suivant = tours.groupby("numero")["debut"].shift(-1)
    tours["fin"] = (tours["debut"] + pd.to_timedelta(tours["temps"], unit="s")).fillna(debut_suivant)
    tours["debut"] = tours["debut"].fillna(tours["fin"] - pd.to_timedelta(tours["temps"], unit="s"))
    # Tour jamais bouclé : tour de décélération après le drapeau à damier, ou abandon en piste
    return tours.dropna(subset=["fin"]).reset_index(drop=True)


def ajouter_pneus(tours, stints):
    """Composé, numéro de relais et âge du pneu (en tours) pour chaque tour."""
    tours = tours.assign(compose=pd.NA, relais=pd.NA, age_pneu=np.nan)
    # Les relais se chevauchent parfois d'un tour (tour d'entrée aux stands) : ce tour se fait
    # encore sur l'ancien pneu, donc on ne remplit que les tours pas encore attribués
    for s in sorted(stints, key=lambda s: (s["driver_number"], s["stint_number"])):
        fin = s.get("lap_end") or 10_000
        cible = ((tours["numero"] == s["driver_number"]) & tours["tour"].between(s["lap_start"], fin)
                 & tours["compose"].isna())
        if not cible.any():
            continue
        age_depart = s.get("tyre_age_at_start") or 0
        tours.loc[cible, "compose"] = s.get("compound")
        tours.loc[cible, "relais"] = s["stint_number"]
        tours.loc[cible, "age_pneu"] = (age_depart + tours.loc[cible, "tour"] - s["lap_start"]).astype(float)
    return tours


def ajouter_stands(tours, pits):
    """Tours d'entrée et de sortie des stands, temps passé dans la voie des stands.

    Dans OpenF1, le `lap_number` d'un arrêt est le tour d'entrée aux stands. Certaines
    courses n'ont aucun arrêt enregistré (Bahreïn 2023) alors que les tours de sortie sont
    signalés : le tour qui précède un tour de sortie est donc aussi un tour d'entrée.
    """
    duree = {(p["driver_number"], p["lap_number"]): p.get("pit_duration") for p in pits}
    sorties_api = set(zip(tours.loc[tours["sortie_api"], "numero"], tours.loc[tours["sortie_api"], "tour"],
                          strict=True))
    cle = list(zip(tours["numero"], tours["tour"], strict=True))
    cle_precedent = list(zip(tours["numero"], tours["tour"] - 1, strict=True))
    cle_suivant = list(zip(tours["numero"], tours["tour"] + 1, strict=True))
    tours["entree_stand"] = [c in duree or s in sorties_api for c, s in zip(cle, cle_suivant, strict=True)]
    tours["sortie_stand"] = tours["sortie_api"] | pd.Series([c in duree for c in cle_precedent])
    tours["duree_stand"] = pd.to_numeric(pd.Series([duree.get(c) for c in cle]), errors="coerce")
    return tours.drop(columns="sortie_api")


def _valeur_en_fin_de_tour(tours, mesures, colonne, tolerance=None):
    """Dernière valeur publiée avant la fin de chaque tour, pour chaque pilote."""
    if not mesures:
        return pd.Series(np.nan, index=tours.index)
    m = pd.DataFrame(mesures)
    m = pd.DataFrame({"numero": m["driver_number"].astype(int), "date": _dates(m["date"]),
                      "valeur": pd.to_numeric(m[colonne], errors="coerce")})  # "+1 LAP" -> NaN
    gauche = tours[["numero", "fin"]].reset_index().dropna(subset=["fin"]).sort_values("fin")
    fusion = pd.merge_asof(gauche, m.sort_values("date"), left_on="fin", right_on="date",
                           by="numero", direction="backward", tolerance=tolerance)
    return fusion.set_index("index")["valeur"].reindex(tours.index)


def ajouter_position_et_ecart(tours, positions, intervalles):
    tours["position"] = _valeur_en_fin_de_tour(tours, positions, "position")
    # Écart avec la voiture devant (vide pour le leader et pour une voiture prise d'un tour)
    ecart = _valeur_en_fin_de_tour(tours, intervalles, "interval", TOLERANCE_ECART)
    # OpenF1 donne parfois 0 au lieu de vide pour le leader
    tours["ecart_devant"] = ecart.where(tours["position"] != 1)
    return tours


# --- Neutralisations, drapeaux, pluie ------------------------------------------------

def periodes_neutralisees(messages, tours):
    """
    Périodes de voiture de sécurité (SC), voiture de sécurité virtuelle (VSC) et drapeau rouge.

    - VSC : de « VIRTUAL SAFETY CAR DEPLOYED » à « VIRTUAL SAFETY CAR ENDING » ;
    - SC : de « SAFETY CAR DEPLOYED » à la fin du tour annoncé par « SAFETY CAR IN THIS LAP »
      (pas de message de fin : on prend le 1er passage sur la ligne à la fin de ce tour,
      celui du leader) ;
    - drapeau rouge : de « RED FLAG » à la reprise (« SESSION STARTED »).
    """
    periodes, ouvertes = [], {}
    fin_course = tours["fin"].max()

    def fermer(type_, date):
        if type_ in ouvertes:
            periodes.append({"type": type_, "debut": ouvertes.pop(type_), "fin": date})

    for m in sorted(messages, key=lambda m: m["date"]):
        texte, date = (m.get("message") or "").upper(), _dates([m["date"]]).iloc[0]
        if "VIRTUAL SAFETY CAR DEPLOYED" in texte:
            ouvertes.setdefault("VSC", date)
        elif "VIRTUAL SAFETY CAR ENDING" in texte:
            fermer("VSC", date)
        elif "SAFETY CAR DEPLOYED" in texte:
            ouvertes.setdefault("SC", date)
        elif "SAFETY CAR IN THIS LAP" in texte:
            passages = tours.loc[tours["tour"] == m.get("lap_number"), "fin"]
            passages = passages[passages > date]
            fermer("SC", passages.min() if not passages.empty else date + pd.Timedelta(2, unit="min"))
        elif m.get("flag") == "RED":
            fermer("SC", date)
            fermer("VSC", date)
            ouvertes.setdefault("ROUGE", date)
        elif "SESSION STARTED" in texte or m.get("flag") == "CHEQUERED":
            fermer("ROUGE", date)
            if m.get("flag") == "CHEQUERED":
                fermer("SC", date)
                fermer("VSC", date)
    for type_ in list(ouvertes):  # course finie sous neutralisation
        fermer(type_, fin_course)
    return pd.DataFrame(periodes, columns=["type", "debut", "fin"])


def periodes_double_jaune(messages):
    """Double drapeau jaune par secteur, jusqu'au « CLEAR » du même secteur.

    Les simples drapeaux jaunes ne sont pas retenus : ils ralentissent peu, et le filtre
    des 107 % écarte déjà les tours vraiment perturbés.
    """
    periodes, ouverts = [], {}
    for m in sorted(messages, key=lambda m: m["date"]):
        secteur, date = m.get("sector"), _dates([m["date"]]).iloc[0]
        if m.get("flag") == "DOUBLE YELLOW":
            ouverts.setdefault(secteur, date)
        elif m.get("flag") in ("CLEAR", "GREEN") and (secteur in ouverts or m.get("scope") == "Track"):
            for s in [secteur] if m.get("scope") != "Track" else list(ouverts):
                if s in ouverts:
                    periodes.append({"debut": ouverts.pop(s), "fin": date})
    return pd.DataFrame(periodes, columns=["debut", "fin"])


def _chevauche(tours, debut, fin):
    return (tours["debut"] < fin) & (tours["fin"] > debut)


def ajouter_etat_piste(tours, neutralisations, double_jaunes):
    """État le plus grave pendant le tour (VERT, VSC, SC, ROUGE) et relance qui suit."""
    gravite = pd.Series(0, index=tours.index)
    for p in neutralisations.itertuples():
        gravite[_chevauche(tours, p.debut, p.fin)] = np.maximum(gravite, GRAVITE[p.type])
    noms = {v: k for k, v in GRAVITE.items()}
    tours["etat_piste"] = gravite.map(noms)
    # Le 1er tour en vert après une neutralisation est une relance : pneus froids, peloton groupé
    precedent = tours.groupby("numero")["etat_piste"].shift(1).fillna("VERT")
    tours["apres_neutralisation"] = (tours["etat_piste"] == "VERT") & (precedent != "VERT")
    tours["double_jaune"] = False
    for p in double_jaunes.itertuples():
        tours.loc[_chevauche(tours, p.debut, p.fin), "double_jaune"] = True
    return tours


def ajouter_pluie(tours, meteo):
    """Pluie relevée pendant le tour.

    Les relevés météo tombent environ toutes les minutes : un relevé de pluie couvre la
    minute avant et la minute après (prudent, pour ne pas garder un tour humide).
    """
    tours["pluie"] = False
    if not meteo:
        return tours
    m = pd.DataFrame(meteo)
    dates_pluie = _dates(m.loc[pd.to_numeric(m["rainfall"], errors="coerce") > 0, "date"])
    for date in dates_pluie:
        minute = pd.Timedelta(1, unit="min")
        tours.loc[_chevauche(tours, date - minute, date + minute), "pluie"] = True
    return tours


# --- Tours propres -------------------------------------------------------------------

def marquer_tours_propres(tours):
    """
    Colonne `exclusion` : vide pour un tour propre, sinon la 1re raison de l'écarter.
    Seuls les tours propres servent à mesurer l'usure des pneus.
    """
    regles = [
        ("1er tour", tours["tour"] == 1),
        ("pas de temps", tours["temps"].isna()),
        ("entrée aux stands", tours["entree_stand"]),
        ("sortie des stands", tours["sortie_stand"]),
        ("neutralisation", tours["etat_piste"] != "VERT"),
        ("relance après neutralisation", tours["apres_neutralisation"]),
        ("double drapeau jaune", tours["double_jaune"]),
        ("pneu inconnu", tours["compose"].isna()),
        ("pneus pluie", tours["compose"].isin(COMPOSES_PLUIE)),
        ("pluie", tours["pluie"]),
    ]
    exclusion = pd.Series("", index=tours.index)
    for raison, masque in regles:
        exclusion[(exclusion == "") & masque.fillna(False).astype(bool)] = raison

    # Tours anormalement lents (trafic, erreur, dégât) parmi ceux qui restent
    restants = exclusion == ""
    mediane = tours[restants].groupby("numero")["temps"].transform("median")
    lent = restants & (tours["temps"] > SEUIL_TOUR_LENT * mediane.reindex(tours.index))
    exclusion[lent] = "lent (> 107 %)"

    tours["exclusion"] = exclusion
    tours["propre"] = exclusion == ""
    return tours


# --- Chargement complet --------------------------------------------------------------

def charger_course(annee, grand_prix):
    """
    Course complète depuis OpenF1. Colonnes du tableau `tours` :
    numero, pilote, equipe, tour, temps (s), debut, fin (UTC), compose, relais, age_pneu,
    entree_stand, sortie_stand, duree_stand (s), position, ecart_devant (s), etat_piste,
    apres_neutralisation, double_jaune, pluie, exclusion, propre.
    """
    meeting, session = _trouver_session(annee, grand_prix, "R")
    return charger_session(meeting, session, annee)


def courses_de_la_saison(annee):
    """Courses (hors sprints) disputées d'une saison, dans l'ordre : [(meeting, session)]."""
    meetings = {m["meeting_key"]: m for m in _openf1("meetings", year=annee)}
    maintenant = pd.Timestamp.now(tz="UTC")
    sessions = [s for s in _openf1("sessions", year=annee, session_name="Race")
                if not s.get("is_cancelled") and _dates([s["date_start"]]).iloc[0] < maintenant
                and s["meeting_key"] in meetings]
    return [(meetings[s["meeting_key"]], s) for s in sorted(sessions, key=lambda s: s["date_start"])]


def charger_session(meeting, session, annee):
    """Course complète à partir d'un meeting et d'une session OpenF1 déjà trouvés."""
    k = session["session_key"]
    print(f"OpenF1 : {meeting['meeting_name']} {annee} - {session['session_name']}")

    tours = construire_tours(_openf1("laps", session_key=k), _openf1("drivers", session_key=k))
    tours = ajouter_pneus(tours, _openf1("stints", session_key=k))
    tours = ajouter_stands(tours, _openf1("pit", session_key=k))
    tours = ajouter_position_et_ecart(tours, _openf1("position", session_key=k),
                                      _openf1("intervals", session_key=k))
    messages = _openf1("race_control", session_key=k)
    neutralisations = periodes_neutralisees(messages, tours)
    tours = ajouter_etat_piste(tours, neutralisations, periodes_double_jaune(messages))
    tours = ajouter_pluie(tours, _openf1("weather", session_key=k))
    tours = marquer_tours_propres(tours)

    circuit = _circuit_multiviewer(session["circuit_key"], annee)
    perte_stand = {cle: float(v) for cle, v in (circuit.get("pitLoss") or {}).items() if v}
    titre = f"{meeting['meeting_name']} {annee} - {session['session_name']}"
    return Course(titre, annee, k, session["circuit_key"], perte_stand, neutralisations, tours)


# --- Résumé en ligne de commande -----------------------------------------------------

def strategies(tours):
    """Stratégie de chaque pilote, dans l'ordre d'arrivée : « MEDIUM 1-15 → HARD 16-53 »."""
    lignes = []
    for (pilote, relais), groupe in tours.dropna(subset=["relais"]).groupby(["pilote", "relais"]):
        lignes.append({"pilote": pilote, "relais": relais, "compose": groupe["compose"].iloc[0],
                       "premier": groupe["tour"].min(), "dernier": groupe["tour"].max()})
    if not lignes:
        return {}
    relais = pd.DataFrame(lignes).sort_values(["pilote", "relais"])
    ordre = tours.sort_values("tour").groupby("pilote")["position"].last().sort_values()
    return {p: " → ".join(f"{r.compose} {r.premier}-{r.dernier}"
                          for r in relais[relais["pilote"] == p].itertuples())
            for p in ordre.index}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tableau tour par tour d'une course, nettoyé.")
    parser.add_argument("annee", type=int)
    parser.add_argument("grand_prix")
    parser.add_argument("--csv", action="store_true", help="exporte le tableau tour par tour")
    args = parser.parse_args(argv)

    course = charger_course(args.annee, args.grand_prix)
    tours = course.tours
    propres = tours["propre"].sum()
    print(f"{len(tours)} tours, dont {propres} propres ({propres / len(tours):.0%})")
    print("Exclusions :", ", ".join(f"{raison} {n}" for raison, n in
                                    tours.loc[~tours["propre"], "exclusion"].value_counts().items()))
    for p in course.neutralisations.itertuples():
        print(f"  {p.type} de {p.debut:%H:%M:%S} à {p.fin:%H:%M:%S} UTC")
    if course.perte_stand:
        print("Temps perdu par arrêt (s) :", course.perte_stand)
    print("\nStratégies (ordre d'arrivée) :")
    for pilote, texte in strategies(tours).items():
        print(f"  {pilote}  {texte}")

    if args.csv:
        fichier = f"course_{args.annee}_{args.grand_prix.replace(' ', '_')}.csv"
        tours.to_csv(fichier, index=False, encoding="utf-8-sig")
        print(f"\nTableau enregistré : {fichier}")


if __name__ == "__main__":
    main()
