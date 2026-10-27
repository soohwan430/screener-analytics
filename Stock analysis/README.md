# SGX Equity Screener - Password Protected

Place your existing `sector_rankings.py` and `table_styles.py` alongside `app.py`.

## Local

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and set `APP_PASSWORD`.

```bash
python -m streamlit run app.py
```

## Streamlit Community Cloud

1. Upload `app.py`, `sector_rankings.py`, `table_styles.py`, `requirements.txt`, `packages.txt`, `.streamlit/config.toml` and `.gitignore` to GitHub.
2. Do not upload `.streamlit/secrets.toml`.
3. Deploy `app.py` at https://share.streamlit.io and set `APP_PASSWORD` in Advanced settings > Secrets.
4. Browser install may require a hosted environment that supports Playwright's Chromium installation. If Community Cloud cannot launch Chromium, use Docker hosting with `python -m playwright install --with-deps chromium` at build time.

Note: Password protection is basic and does not include global login rate limiting or per-user audit logs. Avoid sensitive data.
