# AURA EDGE â€” Collaborative Development Workflow

> **Repository:** `https://github.com/Arihant-57/AURA-EDGE` (Private)  
> **Team Architecture:** 2-Developer Split (Backend Intelligence & Frontend Experience)  
> **Target Platform:** Single Windows 11 host or synchronized local dev environments running strict offline mode.

---

## 1. Team Ownership & Responsibilities

To ensure zero merge conflicts and clean architectural boundaries, feature ownership is explicitly partitioned:

| Role | Primary Branch | Owned Subsystems & Paths | Responsibilities |
|---|---|---|---|
| **Backend Developer** | `feat/backend-intelligence` | `backend/`, `docker-compose.yml`, `alembic.ini`, `scripts/db_smoke_test.py` | Python 3.11+, FastAPI endpoints, offline network policy, local AI models (multilingual Whisper STT, local Indic TTS, Ollama LLM integration), PostgreSQL 16 + `pgvector` schemas, Alembic migrations, intent routing, smart-home simulation logic, timers/routines. |
| **Frontend Developer** | `feat/frontend-experience` | `frontend/`, `frontend/src/` | React 18, TypeScript, Tailwind CSS v4, Lucide icons, responsive dashboard, Dynamic Island assistant state pill, live audio waveform / status animations, telemetry console, frontend API client and WebSocket integration. |
| **Joint Responsibility** | `main` (Reviews) | `docs/API_CONTRACT.md`, `.gitignore`, `.env.example`, `README.md` | API contract alignment, integration verification, PR reviews before merging into `main`. |

---

## 2. Branching Strategy & Release Rules

```
                      â”Œâ”€â”€â”€ [feat/backend-intelligence] â”€â”€â”€â”€ (Backend features)
                      â”‚             â”‚ (PR & Review)
                      â”‚             â–¼
[main] â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–º (Clean, verified baseline)
                      â”²             â–²
                      â”‚             â”‚ (PR & Review)
                      â””â”€â”€â”€ [feat/frontend-experience] â”€â”€â”€â”€â”€ (Frontend features)
```

### Golden Rules
1. **Never commit directly to `main`**: All ongoing work happens on `feat/backend-intelligence` or `feat/frontend-experience`.
2. **Never merge unfinished features into `main`**: A feature branch is only merged into `main` when:
   - Full regression suite passes: `pytest` (backend) + `vitest` & `npm run build` (frontend).
   - Both developers have verified the integration against `docs/API_CONTRACT.md`.
3. **No Private Secrets or Model Weights in Git**:
   - Never commit `.env` or personal tokens (`.gitignore` enforces this).
   - Never commit binary model weights (`*.onnx`, `*.pt`, `*.gguf`, `*.bin`, `*.safetensors`).
   - Models are kept in local Ollama (`~/.ollama/models`) or HuggingFace cache (`~/.cache/huggingface/hub`).
4. **Clean Room Respect**: Do not copy untracked personal data or unvetted scripts from the legacy `AURA V4` project (`phase4-agent`).

---

## 3. Second Laptop (Teammate) Setup Guide

When setting up the second developer machine:

### Step 1: Install System Prerequisites
- **Git**
- **Python 3.11+** (Python 3.13 recommended)
- **Node.js 20+** (verified on Node `v26.13.1` / npm `11.20.0`)
- **Docker Desktop** (for PostgreSQL 16 + `pgvector`)
- **GitHub CLI (`gh`)** (optional, recommended)

### Step 2: Clone the Private Repository
```powershell
# Using GitHub CLI:
gh repo clone Arihant-57/AURA-EDGE

# Or using standard Git HTTPS:
git clone https://github.com/Arihant-57/AURA-EDGE.git
cd AURA-EDGE
```

### Step 3: Setup Local Environment & Database
```powershell
# Create .env from template
copy .env.example .env

# Start PostgreSQL + pgvector container
docker compose up -d postgres
```

### Step 4: Setup Backend Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .\backend -r .\backend\requirements-dev.txt

# Run initial Alembic database migration
alembic -c backend\alembic.ini upgrade head
```

### Step 5: Setup Frontend Dependencies
```powershell
cd frontend
npm install
npm run build
cd ..
```

### Step 6: Verify Regression Suite Locally
```powershell
# Run backend tests (requires live Docker PostgreSQL for integration test)
$env:AURA_EDGE_REQUIRE_LIVE_DB = "1"
.\.venv\Scripts\pytest.exe -v backend/tests

# Run frontend tests
cd frontend
npm test -- --run
npm run build
cd ..
```

### Step 7: Launch Development Environment
```powershell
# Starts FastAPI (127.0.0.1:8000) and Vite (127.0.0.1:5173)
.\scripts\start-dev.ps1
```

---

## 4. Git Collaboration Daily Workflow

### Working on your assigned branch:
```powershell
# Backend developer:
git checkout feat/backend-intelligence
git pull origin feat/backend-intelligence

# Frontend developer:
git checkout feat/frontend-experience
git pull origin feat/frontend-experience
```

### Syncing latest changes from `main`:
Before submitting changes for review, rebase or merge from `main` to ensure you are up to date:
```powershell
git fetch origin
git merge origin/main
```

### Committing changes:
Use descriptive conventional commit prefixes:
- `feat(backend): ...` or `feat(frontend): ...`
- `fix(...)`
- `docs(...)`
- `test(...)`

### Pushing changes:
```powershell
git push origin feat/backend-intelligence
# or
git push origin feat/frontend-experience
```

---

## 5. How to Add Your Teammate as a Collaborator

Since this is a private GitHub repository (`Arihant-57/AURA-EDGE`), your teammate needs explicit collaborator access.

### Option A: Via GitHub Web Interface
1. Go to the repository page: `https://github.com/Arihant-57/AURA-EDGE`
2. Click on **Settings** (top navigation tab).
3. In the left sidebar, click **Collaborators** (under "Access").
4. Click the green button **"Add people"**.
5. Enter your teammate's GitHub username or email address.
6. Select **"Add [username] to this repository"**.
7. Your teammate will receive an email invitation and a notification at `https://github.com/notifications` to accept the invite. Once accepted, they have full push and clone access.

### Option B: Via GitHub CLI (`gh`)
Run the following command from PowerShell:
```powershell
gh api --method PUT /repos/Arihant-57/AURA-EDGE/collaborators/<TEAMMATE_GITHUB_USERNAME> -f permission=push
```
Replace `<TEAMMATE_GITHUB_USERNAME>` with their actual GitHub handle.
