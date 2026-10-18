"""Tests du point d'entrée unique : chaque commande part vers le bon module, avec les bons arguments."""

import importlib

import pytest

import strategie


@pytest.fixture
def appels(monkeypatch):
    """Remplace main() de chaque module par un enregistreur."""
    enregistres = []
    for nom in set(strategie.COMMANDES.values()):
        module = importlib.import_module(nom)
        monkeypatch.setattr(module, "main", lambda argv, nom=nom: enregistres.append((nom, argv)))
    return enregistres


@pytest.mark.parametrize("ligne, attendu", [
    (["course", "2024", "Monza"], ("course", ["2024", "Monza"])),
    (["usure", "2024", "Monza", "--no-show"], ("usure", ["2024", "Monza", "--no-show"])),
    (["duel", "2024", "Monza", "13", "NOR", "LEC"],
     ("undercut", ["2024", "Monza", "duel", "13", "NOR", "LEC"])),
    (["fenetre", "2023", "Singapore", "SAI", "--sc", "20"],
     ("undercut", ["2023", "Singapore", "fenetre", "SAI", "--sc", "20"])),
    (["validation", "2023", "2025"], ("validation", ["2023", "2025"])),
    (["duel", "--help"], ("undercut", ["0", "-", "duel", "--help"])),
])
def test_aiguillage(appels, ligne, attendu):
    assert strategie.main(ligne) == 0
    assert appels == [attendu]


def test_commande_inconnue_ou_absente(appels, capsys):
    assert strategie.main(["inconnue"]) == 2
    assert strategie.main([]) == 2
    assert strategie.main(["--help"]) == 0
    assert "python strategie.py duel" in capsys.readouterr().out
    assert appels == []
