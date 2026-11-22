# GitHub and Streamlit Community Cloud deployment

## 1. Create an empty GitHub repository

Create a repository named `YojanaMitraAI` in your GitHub account. Leave **Add a README**, **Add .gitignore**, and **Choose a license** unchecked because this project already has files for those.

## 2. Review and push the project

Run these commands from the project folder in PowerShell. The `.gitignore` excludes `.env`, local vector/database files, virtual environments, and Streamlit secrets.

```powershell
git init -b main
git add .
git status --short
git diff --cached --name-only
git check-ignore -v .env data/vector_store/chunks.json data/yojanamitra.sqlite3 venv
git commit -m "Prepare YojanaMitra AI for deployment"
git remote add origin https://github.com/YOUR_GITHUB_NAME/YojanaMitraAI.git
git push -u origin main
```

Replace `YOUR_GITHUB_NAME` with your GitHub username. Check the staged file list before committing; it must not include `.env`, `.streamlit/secrets.toml`, `data/`, or a virtual environment.

## 3. Deploy from GitHub

1. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with GitHub and authorize the repository.
2. Choose **Create app** and select repository `YojanaMitraAI`, branch `main`, and main file path `app.py`.
3. In **Advanced settings**, select Python 3.12 or 3.13.
4. Add secrets in the Streamlit **Secrets** field using TOML format:

   ```toml
   GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
   GEMINI_MODEL = "gemini-3.8-flash"
   GEMINI_FALLBACK_MODEL = "gemini-3.6-flash"
   GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
   EMBEDDING_PROVIDER = "local"
   ```

   Replace the API key value with your own. Never put it in GitHub source files, commit history, screenshots, or chat.

5. Deploy. Streamlit installs packages from the root `requirements.txt` and runs `app.py`.

## Runtime notes

- `.env` is for local development. Streamlit Cloud secrets are loaded from `st.secrets` by `app.py`.
- The local vector index and SQLite dashboard are stored under ignored `data/`. A new cloud instance starts with an empty index and will retrieve sources on demand. Local guest dashboard data is not a durable cloud database and may not persist through a rebuild or instance replacement.
- `EMBEDDING_PROVIDER="local"` avoids embedding API calls. Gemini text generation and audio transcription still require `GEMINI_API_KEY`.
- Streamlit Community Cloud deploys from GitHub and updates the app after a push. Keep the repository free of API keys and personal dashboard data.
