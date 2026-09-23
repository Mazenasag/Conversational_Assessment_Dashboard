# Conversational Assessment Dashboard — authoritative pipeline

This project has one processing pipeline and one fast live frontend.

## Authoritative flow

```text
Raw weekly Excel upload (local Streamlit)
        ↓
processor.py — deterministic reconstruction
        ↓
processor_with_classes.py — Adelaide class assignment
        ↓
processor_groq.py — cached/new Gemini semantic enrichment
        ↓
processed_cache/latest_processed_analysis.xlsx
        ↓  automatic after a successful local upload
export_dashboard.py
        ↓
frontend/dashboard_data.json
frontend/latest_processed_analysis.xlsx
        ↓
fast static frontend
```

The static frontend is intentionally **view-only**. It does not process raw spreadsheets and cannot bypass the Gemini research pipeline.

## Setup

Copy `.env.example` to `.env` and set your real Gemini key:

```text
APP_MODE=local
GEMINI_API_KEY=your_real_key
GEMINI_MODEL=gemini-2.5-flash-lite
```

Install requirements:

```bash
pip install -r requirements.txt
```

## Process new data

Run `run_dashboard.bat` or:

```bash
streamlit run app.py
```

Upload files whose names contain `Week N`. The app:
1. reconstructs deterministic events;
2. assigns class;
3. reuses `llm_cache.json` when possible;
4. calls Gemini only for missing semantic cache entries;
5. merges the new week into the cumulative workbook;
6. automatically refreshes the static frontend JSON/workbook.

### Cache behavior

- `llm_cache.json` is the expensive semantic cache. Keep it.
- `processed_cache/processed_<hash>.xlsx` is an exact-input processed cache.
- `processed_cache/latest_processed_analysis.xlsx` is the cumulative workbook.
- If the API key is absent, a run succeeds only when every required Gemini judgement is already present in `llm_cache.json`. A cache miss stops the run instead of silently saving deterministic-only results.

## Fast frontend

Serve `frontend/` with any static server. For example:

```bash
python -m http.server 8080 -d frontend
```

Open `http://localhost:8080/`.

For hosting, deploy only the contents of `frontend/`.
