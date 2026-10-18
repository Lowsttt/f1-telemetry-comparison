# F1 Telemetry & Strategy

**English** · [Français](README.fr.md)

[![Tests](https://github.com/Lowsttt/f1-telemetry-comparison/actions/workflows/tests.yml/badge.svg)](https://github.com/Lowsttt/f1-telemetry-comparison/actions/workflows/tests.yml)

Two tools built on public Formula 1 timing data:

1. **[Telemetry comparison](#part-1--telemetry-comparison)**: where and why one driver is faster than another, corner by corner.
2. **[Pit-stop strategy](#part-2--pit-stop-strategy)**: tyre degradation model, undercut calculator and pit window, **checked against 167 real undercuts from 2023 to 2025**: 73 % correct, better than every simple rule tested.

![Monza 2024 track map: faster driver by section](docs/images/monza_2024_q_lec_nor_carte.png)

*Console output and chart labels are in French (e.g. "Vitesse" = speed, "Écart" = delta, "Frein" = brake, "Rapport" = gear, "Usure" = tyre degradation, "Fenêtre d'arrêt" = pit window).*

---

## Part 1 — Telemetry comparison

Compares the fastest laps of two drivers and produces:

- a **telemetry chart**: speed, cumulative time delta, throttle, brake and gear, plotted against distance;
- a **track map** coloured by the faster driver in each section;
- a **per-section table** (time gained, minimum and maximum speeds, braking point), exported to CSV;
- an **automatically written summary**.

### Example: Monza 2024, qualifying, Leclerc vs Norris

Norris took pole with a **1:19.327**. Leclerc qualified 4th with a **1:19.461**, **0.134 s** behind. Both laps were set on soft tyres.

![Monza 2024 telemetry: LEC vs NOR](docs/images/monza_2024_q_lec_nor_telemetrie.png)

| Section | Faster | Gain (s) | Min speed LEC / NOR (km/h) | Max speed LEC / NOR (km/h) | Braking LEC / NOR (m) |
|---|---|---|---|---|---|
| Start → T1-T2 (Rettifilo) | LEC | 0.061 | 322 / 321 | 351 / 347 | — |
| T1-T2 (Rettifilo) | NOR | 0.006 | 75 / 73 | 303 / 299 | 743 / 766 |
| T3 (Curva Grande) | LEC | 0.049 | 303 / 302 | 330 / 325 | — |
| T4-T5 (Roggia) | NOR | 0.035 | 111 / 113 | 263 / 263 | 1986 / 1979 |
| T6 (Lesmo 1) | NOR | 0.068 | 204 / 207 | 268 / 269 | 2409 / 2410 |
| T7 (Lesmo 2) | NOR | 0.061 | 187 / 190 | 342 / 338 | 2748 / 2758 |
| T8-T10 (Ascari) | NOR | 0.055 | 191 / 194 | 334 / 329 | 3785 / 3814 |
| T11 (Parabolica) | NOR | 0.018 | 209 / 215 | 321 / 317 | 4986 / 5005 |

*Min speed: speed at the slowest point of the section. Max speed: speed at the end of the straight after the corner. Braking: distance from the start/finish line.*

- **Leclerc is faster on the straights.** He is 4 to 5 km/h quicker at the end of the straight in 6 sections out of 8 (351 vs 347 km/h before the Rettifilo). That earns him **0.110 s**, all of it in the two fastest sections of the track: the start/finish straight and the Curva Grande.
- **Norris is faster through the corners.** He carries 3 to 6 km/h more speed at the slowest point of the medium and high-speed corners (Lesmo, Ascari, Parabolica). He also brakes more than 10 m later in 4 of the 6 braking zones: 24 m at the Rettifilo, 10 m at Lesmo 2, 29 m at Ascari and 18 m at the Parabolica. That earns him **0.243 s** over six sections.
- **Overall**: what Norris gains in the corners outweighs what Leclerc gains on the straights, hence the 0.134 s gap at the line (0.243 − 0.110, within rounding). This pattern is consistent with a lower-downforce Ferrari against a McLaren that is stronger through the turns. That is a hypothesis: a single lap per driver cannot separate the car's contribution from the driver's.

> Summary written by the script (in French): « NOR devance LEC de 0.134 s. NOR est plus rapide dans 6 zone(s) sur 8, surtout T6 (+0.068 s) : il passe 3 km/h plus vite au point le plus lent. LEC est plus rapide dans 2 zone(s) sur 8, surtout Départ → T1-T2 (+0.061 s) : il atteint 4 km/h de plus en fin de ligne droite. »
>
> *Translation: NOR beats LEC by 0.134 s. NOR is faster in 6 sections out of 8, especially T6 (+0.068 s): he is 3 km/h faster at the slowest point. LEC is faster in 2 sections out of 8, especially Start → T1-T2 (+0.061 s): he reaches 4 km/h more at the end of the straight.*

**Another example: Monaco 2024, Leclerc vs Piastri**

![Monaco 2024 track map: faster driver by section](docs/images/monaco_2024_q_lec_pia_carte.png)

### How it works

- **Fastest lap**: each driver's quickest lap, excluding pit-out laps and laps deleted by race control (track limits), which are read from the race control messages.
- **Distance**: obtained by integrating speed. Driver 2's lap is scaled to the length of driver 1's, so braking points are compared at the same place on track.
- **Alignment**: OpenF1 only gives an approximate lap start time, which can shift the two laps 10 to 20 m apart. Under braking, that shift creates fake gaps of several tenths. The script corrects it by finding the offset that best overlays the two speed traces.
- **Cumulative delta**: the two drivers' elapsed times are compared at equal distance on a common grid. Each lap is pinned to 0 s at the start and to its official lap time at the finish, so the curve ends exactly on the official gap.
- **Sections**: one per corner, or per group of corners less than 110 m apart (chicanes). A section runs from the top speed before the corner to the top speed before the next one. The section gains therefore add up exactly to the total gap.
- **Braking point**: the first "brake off → brake on" transition within 400 m before the corner, with at least 0.5 s off the brake just before. The brake signal sometimes flickers during a braking phase, and this rule stops those dropouts from counting.
- **Track map**: driver 1's actual racing line, rotated to match the official circuit map and coloured section by section.

**Limitations**: public telemetry only has about 4 samples per second, so a braking point is only known to within about ten metres (the summary ignores differences below 10 m or 2 km/h). One lap per driver: traffic, wind or an imperfect lap all affect the result.

---

## Part 2 — Pit-stop strategy

Four modules, one entry point (`strategie.py`):

| Module | Role |
|---|---|
| `course.py` | Clean lap-by-lap table of a race: lap time, compound, tyre age, pit stops, gap to the car ahead, track status (safety car, virtual safety car, red flag, double yellow), rain. An `exclusion` column says why a lap must not be used to measure tyre wear. |
| `usure.py` | **Tyre degradation model**, with uncertainty. |
| `undercut.py` | Real cost of a pit stop, **undercut / overcut calculator**, **pit window**, "should we pit under the safety car?" |
| `validation.py` | **Check on history**: does the calculator predict real undercuts? |

### Case study: Monza 2024, Leclerc's one-stop win

Leclerc won with a single stop (medium, then 38 laps on hards). Both McLarens stopped twice: Piastri finished 2.6 s behind, Norris 3rd.

**1. Tyre degradation.** Lap time = driver pace + fuel effect × lap + fresh-tyre offset of the compound + (compound wear + team deviation) × tyre age.

![Monza 2024 tyre degradation](docs/images/monza_2024_usure.png)

- Fuel effect (the car gets lighter): **−0.061 s/lap** [−0.064 ; −0.059].
- Wear: **+0.047 s/lap on the hard** [+0.042 ; +0.054], **+0.037 s/lap on the medium** [+0.018 ; +0.059].
- Per team on the hard: Mercedes +0.023, McLaren +0.040, Ferrari +0.055 s/lap. Ferrari wore its hards faster than McLaren, yet one stop was still the better choice: a stop costs 26 s, more than the wear it saves.

**2. The undercuts of laps 13 to 15.**

| Attempt (decision lap) | Gap | Predicted chance | Predicted final gap | What happened |
|---|---|---|---|---|
| NOR on LEC (lap 13), LEC reacts 1 lap later | 0.80 s | 49 % | +0.0 s [−3.3 ; +3.3] | **Worked**: NOR 2.3 s ahead |
| LEC on PIA (lap 14), PIA reacts 1 lap later | 3.56 s | 8 % | +2.8 s [−0.5 ; +6.1] | **Failed**: PIA 3.4 s ahead |

The failed one is clearly predicted. The successful one is a coin flip for the model, which underestimated the gain (0.8 s predicted, 3 s in reality).

**3. Pit window.**

![Monza 2024 pit window for Leclerc](docs/images/monza_2024_fenetre_LEC.png)

- **One stop beats two in 100 % of the model's draws.**
- McLaren's two-stop strategy cost **about 13.5 s** against their own optimum (13.3 s for Norris, 13.8 s for Piastri). Leclerc's one-stop cost 5.1 s: **8 to 9 s of strategic advantage**, for a 2.6 s winning margin.
- The optimum sits at lap 21 because no medium tyre lasted more than 21 laps in that race: the tool never extrapolates tyre life beyond what was actually observed. Leclerc stopped earlier (lap 15) to answer Norris's undercut; the pit window, which models a race without opponents, does not see that.

### Validation on 167 real undercuts (2023–2025)

`validation.py` finds every real undercut attempt (A less than 3 s behind B, A pits, B reacts 1 to 5 laps later, all under green flag), then predicts each one **using only the laps known at decision time**. A compound not yet run in that race gets a prior learned from **previous races only**. A test checks that changing every lap after the decision leaves the prediction strictly unchanged.

| Method | Correct predictions | Brier score (0 = perfect, 0.25 = coin flip) |
|---|---|---|
| **Model** | **73 %** | **0.206** |
| "Gap < 1.2 s" (best threshold, **chosen after seeing the results**) | 67 % | 0.329 |
| "Gap < 2 s" | 63 % | 0.371 |
| "The undercut always works" | 48 % | 0.521 |

![Calibration on 167 real undercuts](docs/images/validation_undercuts_2023_2025.png)

- By season: 79 % correct in 2023 (Brier 0.18), 73 % in 2024 (0.19), **65 % in 2025 (0.26)**.
- Median error on the final gap: **2.1 s** (mean 4.4 s, inflated by a few incidents such as a penalty served at the stop).
- **The model is overconfident at the extremes**: when it says 7 %, the undercut works 26 % of the time; when it says 92 %, 80 %. Adding an "unmodelled noise" term learned on past seasons barely helps (Brier on 2025: 0.261 → 0.258), so it was not added: the 2025 weakness comes from the central prediction, not from the width of the probabilities. That is the main thing to improve next.

### Method and safeguards

- **Robust regression** (Huber): laps that are still slightly disturbed weigh less.
- **Uncertainty** from a "wild" bootstrap by stint: residuals are recombined stint by stint with random signs, so every driver and compound stays present in every draw.
- **Team deviation** and **fuel effect** pulled gently towards a typical value (ridge). Before the first stops, tyre age rises exactly like the lap number, so fuel and wear cannot be told apart from the data alone.
- **Pit-stop cost measured** on the race's green-flag stops (Monza: 26.0 s, MultiViewer: 25.4 s).
- **Against the optimizer's curse**: the pit window only proposes compounds run on at least 5 clean stints, and no stint longer than the longest one observed in the race. Without this, it recommended 30 laps on softs in Singapore 2023, based on 3 stints.
- **Data quality**: laps behind the safety car, restarts, double yellows, rain, pit laps and abnormally slow laps are excluded. Bahrain 2023 has no pit stops in OpenF1: in-laps are inferred from out-laps.

**Limitations**: the calculator models a race without opponents: no traffic when rejoining, no overtaking difficulty, no value for track position. Linear degradation, so no tyre "cliff". OpenF1 data from 2023 onwards.

---

## Installation

Requires Python 3.11 or newer.

```bash
git clone https://github.com/Lowsttt/f1-telemetry-comparison.git
cd f1-telemetry-comparison
pip install -r requirements.txt
```

## Usage

**Telemetry comparison**

```bash
python comparaison_telemetrie.py                            # Monza 2024, qualifying, LEC vs NOR
python comparaison_telemetrie.py 2024 Monaco Q LEC PIA      # year, Grand Prix, session, driver 1, driver 2
python comparaison_telemetrie.py --help
```

| Argument | Values | Default |
|---|---|---|
| `annee` (year) | 2023 onwards with OpenF1 | `2024` |
| `grand_prix` | location, circuit or Grand Prix name (`Monza`, `Silverstone`, `"Abu Dhabi"`…) | `Monza` |
| `session` | `FP1`, `FP2`, `FP3`, `Q`, `SQ` (sprint qualifying), `S` (sprint), `R` (race) | `Q` |
| `pilote_1`, `pilote_2` (drivers) | three-letter code (`LEC`, `NOR`, `VER`…) | `LEC`, `NOR` |
| `--source` | `openf1` or `fastf1` | `openf1` |
| `--no-show` | save the images without displaying them | off |

**Pit-stop strategy**

```bash
python strategie.py course     2024 Monza               # clean lap-by-lap table
python strategie.py usure      2024 Monza               # tyre degradation by compound and team
python strategie.py duel       2024 Monza 13 NOR LEC    # NOR's undercut on LEC, decision at lap 13
python strategie.py fenetre    2024 Monza LEC           # best pit strategy for LEC
python strategie.py fenetre    2023 Singapore SAI --depuis 19 --sc 20   # pit under the safety car?
python strategie.py validation 2023 2025                # check on real undercuts (~15 min the first time)
```

Every command accepts `--help`. Downloaded data is cached in `cache_openf1/`; requests are spaced out to respect OpenF1's free rate limit, so the first download of a whole season takes a few minutes.

---

## Data

- **[OpenF1](https://openf1.org)** (default source): free public API, from 2023 onwards: laps, telemetry, positions, tyres, pit stops, gaps, race control messages, weather.
- **[MultiViewer](https://multiviewer.app)**: corner positions, circuit map orientation, reference pit-stop time loss.
- **[FastF1](https://github.com/theOehrly/Fast-F1)** (`--source fastf1`, telemetry comparison only): goes back before 2023, but since 15 October 2026 the F1 archive it relies on requires authentication ([Fast-F1#978](https://github.com/theOehrly/Fast-F1/issues/978)).

## Tests and code quality

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

The 91 tests run **without network access** in about a second. Synthetic races whose parameters are known in advance check that the models recover them (wear, fuel effect, pit-stop cost, optimal pit lap); simulated API responses test the data loading (deleted laps, caching, rate limiting, missing pit data); a dedicated test checks that the validation never uses future data. GitHub Actions runs ruff and the tests on every push, on Python 3.11, 3.12 and 3.13.

## Project structure

```
├── comparaison_telemetrie.py   Telemetry comparison: arguments, charts, track map
├── analyse.py                  Cumulative delta, sections, table, summary
├── sources.py                  OpenF1 / FastF1 loading, cache, rate limiting
├── strategie.py                Strategy tool entry point
├── course.py                   Clean lap-by-lap race table
├── usure.py                    Tyre degradation model
├── undercut.py                 Pit-stop cost, undercut calculator, pit window
├── validation.py               Check on real undercuts 2023-2025
├── tests/                      pytest tests (no network)
├── docs/images/                README images
├── requirements.txt            Dependencies
├── requirements-dev.txt        Test and lint tools
└── pyproject.toml              ruff and pytest configuration
```

## License

[MIT](LICENSE): free to use, modify and share, as long as the copyright notice is kept.

---

Personal project by Rayan Bouyacoub, a cybersecurity student interested in software and data roles in Formula 1.

*Unofficial project, not associated with Formula 1, the FIA or any team. F1 and FORMULA 1 are trademarks of Formula One Licensing B.V.*
