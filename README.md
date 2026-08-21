# FFXI WSDist Simulator

Desktop application for simulating FFXI weapon skills, combat damage, gear
sets, and LuAshitacast profile workflows.

## Run from source

From this directory:

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-qt.txt
python qt_gui_main.py
```

`qt_gui_main.py` is a small compatibility launcher; the application lives in
`app/qt_gui_main.py`.

## Layout

| Directory | Contents |
| --- | --- |
| `app/` | Qt application and UI orchestration |
| `engine/` | Combat formulas, simulation, and optimizer code |
| `data/` | Gear, enemies, buffs, weapon data, and item catalog |
| `integrations/` | GearSetBuilder and LuAshitacast/profile integration |
| `persistence/` | Simulation cache and result history storage |
| `presentation/` | Plotting and visual output |
| `assets/` | Icons and other bundled resources |
| `tests/` | Unit, regression, and parity tests |
| `docs/` | Detailed design notes, formulas, cache plan, and audits |

## Test

```powershell
python -m unittest discover -s tests
```

The cache is stored in `.cache/` by default. It contains SQLite records keyed
by normalized simulation inputs and a source fingerprint; it is reusable across
jobs whenever the effective simulation inputs match. Cache controls are also
available under **File > Performance and storage**.

## Native FFXI item data

The simulator uses the local POLUtils extraction as its primary base-item
catalog. The default path is the Steam install at
`C:\Program Files (x86)\Steam\steamapps\common\FFXINA`. Refresh the catalog
after an FFXI update with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\update_polutils_data.ps1
```

Use `-FfxiRoot` to point at a different `FINAL FANTASY XI` data directory.
The generated catalog is `data/polutils_models.json`. In-game scanner/profile
stats and manually supplied augment stats remain authoritative; POLUtils
fills the unaugmented base item, followed by curated models and the archived
FFXIAH data as compatibility fallbacks.

## Build

The GitHub Actions workflow in `.github/workflows/build.yml` creates an
executable-only artifact and a full bundle containing `data/` and `assets/`.
The full bundle is the recommended download because it includes the item
catalog and icon resources.

## Documentation

- [Cache design and implementation plan](docs/CACHE.md)
- [Gear audit checklist](docs/GEAR_AUDIT.md)
- [Formula notes](docs/formula.md)
- [Stats notes](docs/stats.md)
- [Augment report](docs/augment_report.md)

For defects or data corrections, open an issue on the project’s GitHub page.
