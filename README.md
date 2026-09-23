# Conversational Assessment Dashboard

Portable local processing pipeline plus a fast, view-only frontend.

## What is portable now

The project no longer contains machine-specific paths such as `C:\\Users\\...`. All project files are resolved relative to the repository itself, and the launchers create/use a `.venv` inside the project.

Supported local environments:

- Windows 10/11
- macOS
- Linux

Python 3.10+ is recommended.

## Project flow

```text
Raw weekly Excel upload
        ↓
Streamlit app.py
        ↓
processor.py
        ↓
processor_with_classes.py
        ↓
processor_groq.py + Gemini semantic enrichment/cache
        ↓
processed_cache/latest_processed_analysis.xlsx
        ↓
export_dashboard.py
        ↓
frontend/dashboard_data.json
frontend/latest_processed_analysis.xlsx
        ↓
view-only static dashboard
```

## First-time setup

### Windows

Double-click:

```text
setup.bat
```

Then open `.env` and add your Gemini API key:

```text
APP_MODE=local
GEMINI_API_KEY=your_real_key
GEMINI_MODEL=gemini-2.5-flash-lite
```

Start the processor with:

```text
run_dashboard.bat
```

`run_dashboard.bat` also performs first-run setup automatically if `.venv` does not exist.

### macOS / Linux

From Terminal:

```bash
chmod +x setup.sh run_dashboard.sh run_fast_dashboard.sh update_live_dashboard.sh
./setup.sh
```

Edit `.env`, add `GEMINI_API_KEY`, then run:

```bash
./run_dashboard.sh
```

## Manual setup (all platforms)

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
```

Activate it, then:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, add your API key, and run:

```bash
python -m streamlit run app.py --server.port 8501
```

## Process new data

Upload Excel files whose filenames contain `Week N`.

The app:

1. reconstructs deterministic events;
2. assigns class;
3. reuses `llm_cache.json` when possible;
4. calls Gemini only for missing semantic cache entries;
5. merges the new week into the cumulative workbook;
6. refreshes the static frontend JSON/workbook.

## API keys and GitHub

Never commit `.env`. It is intentionally ignored by Git.

Commit `.env.example` instead. Each machine/user should create its own `.env` locally.

If a Gemini key was ever committed to Git history, revoke it and create a new key even after deleting the file from the current repository.

## Cache behavior

- `llm_cache.json`: semantic cache; keep it if you want to reuse previous classifications.
- `llm_state.json`: runtime progress/state; ignored by Git.
- `processed_cache/processed_<hash>.xlsx`: exact-input processed cache.
- `processed_cache/latest_processed_analysis.xlsx`: cumulative workbook.

If the API key is absent, processing can only complete when every required semantic judgement is already cached.

## Fast frontend

After processing data, start the static dashboard.

### Windows

```text
run_fast_dashboard.bat
```

### macOS / Linux

```bash
./run_fast_dashboard.sh
```

Then open:

```text
http://localhost:8080/
```

To refresh frontend export files manually:

Windows:

```text
update_live_dashboard.bat
```

macOS / Linux:

```bash
./update_live_dashboard.sh
```

## GitHub recommendations

Do not upload these local/runtime files:

```text
.env
.venv/
__pycache__/
*.pyc
llm_state.json
```

The included `.gitignore` already covers them.
