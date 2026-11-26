# AURA EDGE — Phase 4 Handover (`feat/backend-intelligence`)

**Date:** 2026-11-26
**Branch:** `feat/backend-intelligence`
**Alembic Revision:** `0005_phase4_personal_assistant`
**Boundaries Verified:** `main` and `frontend/` remain untouched; strict offline defaults preserved.

---

## 1. Part A — Shared Multilingual Intelligence & Execution Grounding

- **Code-Switched EN/HI/KN/Hinglish/Kanglish Normalization (`intelligence/normalizer.py`):**
  - Expanded `_EN_NUMBER_WORDS` and `_INDIC_SCRIPT_NUMBER_WORDS` with compound numbers and Devanagari/Kannada transliterations (`ट्वेंटी फोर` -> `24`, `थर्टी` -> `30`, `ಟ್ವೆಂಟಿ ಫೋರ್` -> `24`, `ಥರ್ಟಿ` -> `30`), plus `_CODE_SWITCHED_LOANWORDS` (`सेकंड्स`/`ಸೆಕೆಂಡ್ಸ್` -> `seconds`, `ಮಿನಿಟ್` -> `minute`, `ಡಿಗ್ರೀಸ್`/`डिग्रीज` -> `degrees`, `ಸೆಟ್`/`सेट` -> `set`).
  - Fixed `detect_language()` to compare Devanagari vs Kannada character counts (`kan_count >= dev_count` -> `"kn"`), preventing Kannada utterances from receiving Hindi replies.
- **Mixed-Script Speech Preservation (`speech/stt.py`):**
  - Preserves high-confidence mixed-script KN+EN Whisper multilingual transcripts (`has_kn_script and has_latin_alpha`) rather than overwriting them with Kannada-only `indic-conformer-kn`.
- **Routing, Verified Anaphora & Grounding (`fast_router.py`, `conversation.py`, `planner.py`, `voice_orchestrator.py`):**
  - Added SOV Hindi/Kannada memory deletion precedence (`"मेरा preferred temperature भूल जाओ."`, `"ನನ್ನ preferred temperature ಮರೆತುಬಿಡು"`), mixed relative-delay extraction (`"के बाद"`, `"ಆದ್ಮೇಲೆ"`), and delayed smart-home scheduling (`"ಒಂದು minute ಆದ್ಮೇಲೆ fan off ಮಾಡಿ"`).
  - Added `ConversationSession.record_verified_entity()`, `resolve_anaphora()`, and `invalidate_memory_context()` (scrubbing deleted memory facts from conversational history).
  - Coerced boolean `power` strings (`"on"`/`"off"`) safely in `HybridPlanner` and enforced `_OPERATIONAL_GROUNDING_RE` + `validate_no_unverified_action_claim()` so conversational LLM turns never claim unverified mutations.

---

## 2. Part B — Phase 4 Personal Secretary (`0005_phase4_personal_assistant`)

| Module | Tables | Capabilities |
|---|---|---|
| **Tasks** (`task_service.py`) | `tasks` | CRUD, priority/category filtering, completion, automatic linked reminder job creation/cancellation in `SchedulerService`, WS `task_updated`. |
| **Offline Calendar** (`calendar_service.py`) | `calendar_events` | Offline CRUD, `Asia/Kolkata` today/tomorrow/upcoming windows, daily/weekly recurrence expansion, conflict detection (`conflicts[]`), event reminders via `SchedulerService`, WS `calendar_updated`. |
| **Daily Briefing** (`briefing_service.py`) | Read-only aggregation | Aggregates live PostgreSQL date, events, pending tasks, upcoming reminders, scheduled appliance ops, unread notifications, low-stock items, and parcels into EN/HI/KN summaries. |
| **Groceries & Recipes** (`grocery_service.py`) | `grocery_items`, `shopping_list_items`, `local_recipes` | Shopping list + pantry inventory CRUD, low-stock/expiry checks, EN/HI/KN ingredient canonicalization, and 8 seeded offline recipes showing `available_ingredients` vs `missing_ingredients`. |
| **Expenses** (`expense_service.py`) | `expense_imports`, `expense_transactions` | Exact `BIGINT amount_paise` storage, SQL `SUM()` day/month/all/category summaries, and CSV import with SHA-256 file + row deduplication. |
| **Parcels** (`parcel_service.py`) | `parcels` | Locally cached/manual records with `source`, `is_sample`, `last_updated_at_utc`, explicit `offline_provenance`, and delivery reminders via `SchedulerService`. |

---

## 3. Deferred Optional Scope

- **Local Document RAG & Allowlisted Desktop/File Launcher (Optional P2):** Deferred intentionally to prioritize reliability and full PostgreSQL/API/voice test verification of Part A + P1 Personal Secretary modules before competition freeze.
