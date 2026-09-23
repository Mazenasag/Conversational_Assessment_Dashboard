# Replace your current repo safely

1. Back up your existing repository, especially `llm_cache.json` and your real `processed_cache/latest_processed_analysis.xlsx`.
2. Copy these project files into the repo.
3. **Keep your existing `llm_cache.json`**. Do not replace/delete it.
4. Replace the sample `processed_cache/latest_processed_analysis.xlsx` in this ZIP with your real cumulative workbook.
5. Run `python export_dashboard.py`.
6. Test live frontend locally: `python -m http.server 8080 -d frontend`.
7. Push the repo.
8. In Vercel, deploy the `frontend` directory as a static site.

The sample workbook/JSON in this package contains only the two 10-session sample files supplied in this chat and is included only so the new frontend works immediately for testing.
