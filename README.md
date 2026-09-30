# AI Nutrition Assistant

A Windows desktop application for logging what you eat — from a **photo** or a **sentence** —
and turning it into numbers you can check. A language model reads the food; **plain Python does
every calculation**; everything is stored in a local SQLite file on your machine.

> **The interface is in Simplified Chinese.** The app is built for a Chinese-speaking user and
> all user-facing text is managed centrally, so adding another language is a translation-table
> change rather than a code change.

---

## 1. What it does

* **First-run wizard** — asks for a DeepSeek API key, tests it, saves it. No config files to edit.
* **Log a meal** — upload a food photo (JPG / JPEG / PNG / WEBP / GIF), or type something like
  *"two eggs, one slice of wholemeal toast, a cup and a half of semi-skimmed milk"*.
* **Confirm before saving** — the model's answer is shown as an editable table. Change any number;
  the totals are recomputed in Python. Nothing is written to the database until you accept it.
* **Daily totals** — calories, protein, carbs, fat, sugar, fibre and sodium against a personal target.
* **Recommend the next meal** — using what you can *actually buy right now* (budget, kitchen
  equipment, time available, foods you dislike or can't get), not what happens to be in your fridge.
* **Second opinion on a food** — an optional, **manual** button that fetches an online estimate and
  shows it side by side with the built-in reference table, both normalised to per 100 g.
* **History** — today / yesterday / last 7 days / last 30 days, drillable to individual meals.
* **Edit and delete** anything, with confirmation.

> Nutrition values are estimates. Restaurant dishes, cooking oil, sauces and portion sizes are
> especially hard to judge from a photo. Daily targets are estimates too, and are not medical advice.

---

## 2. The design rule everything else follows

**The model is never trusted, and it never does arithmetic.**

Every number the app shows comes from one of exactly two places:

1. **SQL aggregation** over rows the user already confirmed, or
2. **pure functions** in `app/nutrition/calculator.py` — sums, remaining amounts, progress,
   proportional rescaling.

The language model is used only for the things it is genuinely good at: reading a food description,
recognising a photo, estimating a portion, and proposing a meal. Its output then has to pass through
a **Pydantic** validation layer (`app/ai/schemas.py`) before anything can be stored:

* `"140 kcal"` and `"90%"` are coerced to numbers; `calories = -500` or `protein = 999999999` are
  rejected outright;
* a `FoodItem` is also checked for internal consistency — an energy value that the item's own
  macronutrients cannot account for is refused;
* numeric parsing handles thousands separators and European decimal commas, so a table showing
  `4,200` reads back as `4200` and not as `4.2`.

A recommendation is additionally re-checked **locally**: if the model suggests an ingredient the user
marked as hard to buy, the app says so on the card instead of silently trusting it.

### Layering

```
main.py                      entry point: CLI flags, QApplication, AppContext, window
  └─ app/ui/*                Qt widgets: windows, pages, dialogs, custom widgets, QSS
       └─ app/context.py     AppContext: settings + database + api_key_store + client factory
            ├─ app/database/*    SQLite (raw sqlite3, no ORM)
            ├─ app/ai/*          DeepSeek client, prompts, Pydantic schemas, tolerant JSON parsing
            ├─ app/nutrition/*   pure functions: targets, totals, purchase preferences
            └─ app/utils/*       config, credentials, image preprocessing, logging, parsing
```

`app/ai/`, `app/database/`, `app/nutrition/` and `app/utils/` **do not import PySide6 at all**
(verified by grep). That is deliberate: it is what makes the core testable without a display, and it
is the single most valuable engineering property of this codebase.

---

## 3. Requirements

* Windows 10/11
* Python 3.12
* A DeepSeek API key (<https://platform.deepseek.com>) with credit on the account

Verified with: Python 3.12.3, PySide6 6.11.2, openai 3.13.0, pydantic 2.13.5,
Pillow 12.3.0, PyInstaller 6.22.3.

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

## 4. Running it

```bat
.venv\Scripts\activate
python main.py
```

On first launch the app asks for the API key, then for a short profile (age, sex, height, weight,
activity level, goal) from which it derives BMR, TDEE and daily targets using the
**Mifflin-St Jeor** equation with standard activity multipliers and a goal adjustment.

Other entry points:

```bat
python main.py --check-api     :: verify the stored API key against the API (does open a socket)
python main.py --self-test     :: offline end-to-end self check: imports, database, arithmetic
python main.py --smoke-test    :: start the GUI once offscreen and exit
```

---

## 5. Tests

```bat
python -m unittest discover -s tests -t . -v
```

**362 tests, all offline — no network, no API key required.** The expected result is `OK`.

The suite is built on two ideas:

* `tests/fakes.py` provides a `FakeDeepSeekClient`, injected through
  `AppContext.api_client_factory`, so complete UI flows can run without a display
  (`QT_QPA_PLATFORM=offscreen`) and without a single real HTTP request.
* Behaviour that must *not* happen is asserted explicitly — for example that no request is issued
  until the online-reference button is actually pressed, that a failed lookup leaves the local
  numbers untouched, and that an API key never reaches the database, the log or a screenshot.

Coverage includes nutrition arithmetic, BMR/TDEE targets, database CRUD and persistence, Pydantic
rejection of impossible values, tolerant JSON extraction, image resizing and format checks, every
dialog's construction and validation logic, API-key lifecycle (credential store first, `.env`
fallback, masked display, log redaction), the online-reference schema and all of its failure paths,
Chinese-UI string coverage, and full offscreen GUI flows.

---

## 6. Building the executable

```bat
python build.py              :: PyInstaller, one-folder (default)
python build.py --onefile    :: single file (slower to start)
python build.py --clean      :: wipe build\ and dist\ first
python build.py --no-trim    :: keep unused Qt modules (much larger)
```

Result: `dist\AI-Nutrition-Assistant\AI-Nutrition-Assistant.exe` — **135.7 MB, 206 files**.

The build script does two things that are easy to overlook and that both exist because of real
failures during development:

1. **It builds with a scrubbed `PATH`.** PyInstaller walks `PATH` looking for DLLs. This machine had
   Poppler shipping an ICU 78 `icuuc.dll`, which got bundled, while Qt6Core needs the Windows one
   (exporting an unsuffixed `ucnv_open`). The frozen app then died on start with
   `ImportError: DLL load failed while importing QtCore: WinError 127`.
2. **It deletes Windows-shipped duplicates** (`icuuc.dll`, `icuin.dll`, `icudt*.dll`) from the bundle
   so the system copies are used.

Verify a bundle at any time:

```bat
python tools\check_bundle.py dist\AI-Nutrition-Assistant\_internal
dist\AI-Nutrition-Assistant\AI-Nutrition-Assistant.exe --self-test
```

---

## 7. Where things are stored

| What | Where |
| --- | --- |
| Database (from source) | `data\nutrition.db` |
| Database (frozen EXE) | `%LOCALAPPDATA%\AI Nutrition Assistant\data\nutrition.db` |
| Log | `logs\app.log` |
| **API key** | **Windows Credential Manager** (target `AI-Nutrition-Assistant:DeepSeekAPIKey`) |
| `.env` (fallback only) | project folder, or next to the EXE |

The API key is written to `.env` **only** if the credential manager is unavailable (for example on a
non-Windows system). `.env` is in `.gitignore` and is never committed. The interface shows a mask
(`sk-a********z`), and a logging filter replaces anything matching `sk-...` as well as
`Authorization` headers with `***`. Photos are never stored — they are resized to a 1280 px longest
edge, encoded, sent once for analysis, and discarded.

Configuration precedence is **values saved in the GUI > environment variables > built-in defaults**,
inverted only when `NUTRITION_ENV_WINS=1` is set explicitly (intended for CI). Changing a setting and
pressing save always takes effect — that is deliberate.

---

## 8. The online reference, and why it is only a reference

A model is not a nutrition database: two identical questions can produce two different numbers, and
neither is verified against a source. So the online lookup is designed as a **second opinion**, never
as a data source:

* it runs **only** when the user presses the button — starting the app, changing the date, opening
  history, analysing food, saving or editing numbers all issue no request (there are tests pinning
  this);
* the request runs on a background thread, so the window never blocks;
* both sides are normalised to **per 100 g** (kcal, g for protein/carbs/fat/sugar/fibre, mg for
  sodium) before being compared;
* a missing value shows as **"unknown"**, never as `0` — the app distinguishes "we don't know" from
  "it really is zero";
* if the two differ by more than **30 %** (`DIFFERENCE_THRESHOLD`) it adds a neutral note suggesting
  the portion or cooking method be double-checked. It never claims either side is right;
* the online numbers are **not written to the database and do not enter any calculation**. The
  confirmation totals, the dashboard and the history are identical whether or not the button was
  ever pressed.

**Failure is silent by design.** Offline, timeout, invalid key, 429/5xx, non-JSON, empty or absurd
values — all handled the same way: the comparison table simply does not appear. No error dialog, no
substituted numbers, and a single warning line in `logs\app.log`. Late answers arriving after the
dialog closed are discarded (bounded at 3 seconds) so a stale card cannot reappear.

### Why it is not automatic

An automatic lookup would mean one extra API call per analysed meal — paying and waiting for a
comparison the user may not want. It is deliberately manual and on demand.

---

## 9. Recommendations are about availability, not inventory

The app does not track what is in your kitchen and does not connect to any supermarket. What it asks
is *"what can you conveniently buy?"* — country, usual stores, ingredients that are easy or
inconvenient to get, budget, available cooking equipment, time you are willing to spend, and dietary
restrictions. There is no stock table and no inventory deduction, because the question being answered
is availability, not inventory.

**It does not pretend to know stock or prices.** No live inventory or pricing API is used, so the app
only ever says an ingredient is *usually easy to find in a large supermarket*. It will not claim a
specific shop has an item in stock, and it will not quote a price. The shopping list is a suggestion,
not live data.

---

## 10. Privacy

Food photos can contain personal information, so originals are never saved. All records live in a
local SQLite file. Nothing is uploaded anywhere except the DeepSeek analysis endpoint, and only at
the moment you ask for an analysis.

---

## 11. Project layout

```text
main.py                     entry point (GUI, --check-api, --self-test, --smoke-test)
build.py                    PyInstaller packaging
create_shortcut.py          creates the desktop shortcut (via SHGetKnownFolderPath)
requirements.txt            dependencies
.env.example                API key template (.env itself is gitignored)

app/
  context.py                AppContext: settings + database + api_key_store + AI client
  i18n.py                   tr() / set_language() / format_date()
  translations.py           all user-facing strings (Simplified Chinese, ~465 lines)
  ui/                       Qt layer (14 modules) — main window, pages, dialogs, widgets, QSS
  ai/                       DeepSeek client, model catalogue, prompts, Pydantic schemas,
                            tolerant JSON parser, user-facing error types
  database/                 raw sqlite3: schema, CRUD, daily_summaries view, data models
  nutrition/                pure functions: calculator, targets (Mifflin-St Jeor),
                            purchase preferences, reference comparison, local food table
  utils/                    config and paths, Windows Credential Manager, image handling,
                            logging with secret redaction, tolerant numeric parsing

assets/                     icon.ico / icon.png
screenshots/                UI images, regenerated by tools\render_ui.py
tests/                      362 offline tests (23 modules) + fakes.py
tools/                      bundle checker, credential scanner, offline demo, UI renderer
```

### A note on the SQLite layer

`Database.connect()` uses `check_same_thread=False` with `PRAGMA foreign_keys=ON`,
`journal_mode=WAL` and `synchronous=NORMAL`, serialised through a `threading.RLock` — necessary
because AI calls run on a `QThread`.

> **Known limitation:** `Database.initialize()` runs the `CREATE ... IF NOT EXISTS` script and then
> sets `PRAGMA user_version` unconditionally. Adding a *table* is therefore handled correctly, but
> adding or changing a *column* is not: an old database would be silently stamped as current. A real
> version-by-version migration is still to be written.

---

## 12. Status and limitations

`app/__init__.py` reports version **1.0.0**. Honest state of the project:

* **Windows only**, and **Simplified Chinese only** — the prompt also hardcodes Chinese output, so
  adding another UI language means parameterising the prompt too.
* **Single user, no export** — one profile, no CSV/JSON export, no sync.
* **One task at a time** — the UI disables entry points while a request is in flight.
* **No linting, type checking or CI** — there is no `pyproject.toml`, no ruff/mypy configuration and
  no pipeline. Type annotations are used throughout but nothing verifies them.
* **The reference table has ~80 foods** (`app/nutrition/food_data.py`) and carries no source or
  version information. It is used only to flag large disagreements and is not part of any
  calculation.

---

## 13. About this codebase

The application was built by the author **working with AI coding tools**: the requirements, the
product decisions and the acceptance testing were the author's, and the code was generated with AI
assistance and then reviewed, run and iterated on.

Concretely, the decisions that shaped this program were the author's:

* the model's output is never trusted and never does arithmetic — it is validated and then confirmed
  by the user before it can be stored;
* no pretending to know stock or prices;
* recommendations follow purchasing convenience rather than an inventory;
* the online reference is manual only, and degrades silently when it cannot run;
* the API key goes to the Windows Credential Manager and never into code, database, logs or screenshots.

This is stated openly because it is relevant to how the code should be read and reviewed.

---

## 14. Licence

No licence file is included yet. Treat the code as **all rights reserved** until one is added.
