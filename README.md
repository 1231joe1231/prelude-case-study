# Prelude Case Study

Analyze customs data to recommend customer outreach targets for a given factory.

## Stack
- **Frontend**: Vite + React + TypeScript + Tailwind CSS (port 5173)
- **Backend**: FastAPI + SQLAlchemy + SQLite (port 8000)

## Layout
```
backend/    FastAPI app, SQLite DB lives here as data.db
frontend/   Vite React app, proxies /api → backend in dev
```

## Run

### Backend
```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Health check: http://localhost:8000/api/health
API docs: http://localhost:8000/docs

### Frontend
```powershell
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 — calls to `/api/*` are proxied to the backend.
