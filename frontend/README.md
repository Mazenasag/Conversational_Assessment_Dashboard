# Hosted frontend

This folder is the **view-only live dashboard**.

It consumes only:

- `dashboard_data.json`
- `latest_processed_analysis.xlsx`

Raw weekly spreadsheets must **not** be processed here. Use the local Streamlit backend in the project root so deterministic reconstruction and Gemini enrichment are always applied first.

After a successful local Streamlit upload, the backend automatically refreshes this folder.

For static hosting, deploy this folder only.
