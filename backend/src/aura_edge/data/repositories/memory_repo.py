"""PostgreSQL + pgvector repository for persistent personal memory and opt-in history."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, delete, func, or_, select, text
from sqlalchemy.orm import Session

from aura_edge.data.models import ConversationHistoryItem, MemoryItem, MemorySetting

_TOKEN_RE = re.compile(r"[\w\u0900-\u097F\u0C80-\u0CFF]+", re.UNICODE)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _unicode_tokens(query: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(query or "") if len(tok) >= 2}


class MemoryRepository:
    """Authoritative PostgreSQL storage and 3-tier retrieval for persistent memories."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_settings(self, profile_id: str = "default") -> MemorySetting:
        with Session(self.engine) as session:
            row = session.get(MemorySetting, profile_id)
            if row is None:
                row = MemorySetting(profile_id=profile_id)
                session.add(row)
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    def update_settings(
        self,
        profile_id: str = "default",
        *,
        memory_enabled: bool | None = None,
        auto_save_preferences: bool | None = None,
        persist_conversation_history: bool | None = None,
        retention_days: int | None = None,
        clear_retention_days: bool = False,
        semantic_search_enabled: bool | None = None,
        embedding_model: str | None = None,
    ) -> MemorySetting:
        with Session(self.engine) as session:
            row = session.get(MemorySetting, profile_id)
            if row is None:
                row = MemorySetting(profile_id=profile_id)
                session.add(row)
            if memory_enabled is not None:
                row.memory_enabled = bool(memory_enabled)
            if auto_save_preferences is not None:
                row.auto_save_preferences = bool(auto_save_preferences)
            if persist_conversation_history is not None:
                row.persist_conversation_history = bool(persist_conversation_history)
            if clear_retention_days:
                row.retention_days = None
            elif retention_days is not None:
                if retention_days <= 0:
                    raise ValueError("retention_days must be positive.")
                row.retention_days = int(retention_days)
            if semantic_search_enabled is not None:
                row.semantic_search_enabled = bool(semantic_search_enabled)
            if embedding_model is not None and embedding_model.strip():
                row.embedding_model = embedding_model.strip()[:64]
            row.updated_at_utc = _utc_now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def upsert_item(
        self,
        *,
        profile_id: str = "default",
        category: str,
        canonical_key: str | None,
        original_text: str,
        structured_value: dict[str, Any] | None = None,
        language: str = "en",
        provenance: str = "explicit_text",
        enabled: bool = True,
        expires_at_utc: datetime | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
    ) -> tuple[MemoryItem, bool]:
        """Insert or update by canonical_key (or exact normalized text) without duplicates.

        Returns ``(record, created)``.
        """
        now = _utc_now()
        clean_text = " ".join((original_text or "").split()).strip()
        if not clean_text:
            raise ValueError("Memory original_text cannot be empty.")
        clean_key = canonical_key.strip()[:160] if canonical_key and canonical_key.strip() else None
        payload = dict(structured_value or {})

        with Session(self.engine) as session:
            existing: MemoryItem | None = None
            if clean_key is not None:
                existing = session.scalar(
                    select(MemoryItem).where(
                        MemoryItem.profile_id == profile_id,
                        MemoryItem.canonical_key == clean_key,
                    )
                )
            else:
                existing = session.scalar(
                    select(MemoryItem).where(
                        MemoryItem.profile_id == profile_id,
                        MemoryItem.category == category,
                        func.lower(MemoryItem.original_text) == clean_text.lower(),
                    )
                )

            if existing is not None:
                existing.category = category
                existing.original_text = clean_text
                existing.structured_value = payload
                existing.language = language
                existing.provenance = provenance
                existing.enabled = enabled
                existing.expires_at_utc = expires_at_utc
                if embedding is not None:
                    existing.embedding = embedding
                    existing.embedding_model = embedding_model
                    existing.embedding_dim = embedding_dim
                existing.updated_at_utc = now
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, False

            item = MemoryItem(
                id=uuid.uuid4(),
                profile_id=profile_id,
                category=category,
                canonical_key=clean_key,
                original_text=clean_text,
                structured_value=payload,
                language=language,
                provenance=provenance,
                enabled=enabled,
                expires_at_utc=expires_at_utc,
                embedding=embedding,
                embedding_model=embedding_model if embedding is not None else None,
                embedding_dim=embedding_dim if embedding is not None else None,
                created_at_utc=now,
                updated_at_utc=now,
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            session.expunge(item)
            return item, True

    def get_item(self, item_id: uuid.UUID) -> MemoryItem | None:
        with Session(self.engine) as session:
            row = session.get(MemoryItem, item_id)
            if row is not None:
                session.expunge(row)
            return row

    def update_item(
        self,
        item_id: uuid.UUID,
        *,
        original_text: str | None = None,
        category: str | None = None,
        canonical_key: str | None = None,
        structured_value: dict[str, Any] | None = None,
        language: str | None = None,
        enabled: bool | None = None,
        expires_at_utc: datetime | None = None,
        clear_expiration: bool = False,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
    ) -> MemoryItem | None:
        with Session(self.engine) as session:
            row = session.get(MemoryItem, item_id)
            if row is None:
                return None
            if original_text is not None:
                cleaned = " ".join(original_text.split()).strip()
                if not cleaned:
                    raise ValueError("Memory original_text cannot be empty.")
                row.original_text = cleaned
            if category is not None:
                row.category = category
            if canonical_key is not None:
                row.canonical_key = canonical_key.strip()[:160] or None
            if structured_value is not None:
                row.structured_value = dict(structured_value)
            if language is not None:
                row.language = language
            if enabled is not None:
                row.enabled = bool(enabled)
            if clear_expiration:
                row.expires_at_utc = None
            elif expires_at_utc is not None:
                row.expires_at_utc = expires_at_utc
            if embedding is not None:
                row.embedding = embedding
                row.embedding_model = embedding_model
                row.embedding_dim = embedding_dim
            row.updated_at_utc = _utc_now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def delete_item(self, item_id: uuid.UUID) -> bool:
        with Session(self.engine) as session:
            row = session.get(MemoryItem, item_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def clear_all(self, profile_id: str = "default") -> int:
        with Session(self.engine) as session:
            res = session.execute(
                delete(MemoryItem).where(MemoryItem.profile_id == profile_id)
            )
            session.commit()
            return int(res.rowcount or 0)

    def list_items(
        self,
        *,
        profile_id: str = "default",
        category: str | None = None,
        enabled_only: bool = True,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[MemoryItem]:
        now = _utc_now()
        with Session(self.engine) as session:
            stmt = select(MemoryItem).where(MemoryItem.profile_id == profile_id)
            if category:
                stmt = stmt.where(MemoryItem.category == category)
            if enabled_only:
                stmt = stmt.where(MemoryItem.enabled.is_(True))
            if not include_expired:
                stmt = stmt.where(
                    or_(MemoryItem.expires_at_utc.is_(None), MemoryItem.expires_at_utc > now)
                )
            stmt = stmt.order_by(MemoryItem.updated_at_utc.desc()).limit(max(1, min(limit, 500)))
            rows = list(session.scalars(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def find_by_canonical_key(
        self,
        canonical_key: str,
        *,
        profile_id: str = "default",
        enabled_only: bool = True,
    ) -> MemoryItem | None:
        now = _utc_now()
        with Session(self.engine) as session:
            stmt = select(MemoryItem).where(
                MemoryItem.profile_id == profile_id,
                MemoryItem.canonical_key == canonical_key,
            )
            if enabled_only:
                stmt = stmt.where(
                    MemoryItem.enabled.is_(True),
                    or_(MemoryItem.expires_at_utc.is_(None), MemoryItem.expires_at_utc > now),
                )
            row = session.scalar(stmt)
            if row is not None:
                session.expunge(row)
            return row

    def find_appliance_preferences(
        self,
        *,
        device_id: str | None = None,
        room: str | None = None,
        device_type: str | None = None,
        prop: str | None = None,
        profile_id: str = "default",
    ) -> list[MemoryItem]:
        rows = self.list_items(
            profile_id=profile_id,
            category="appliance_preference",
            enabled_only=True,
            include_expired=False,
            limit=100,
        )
        matched: list[MemoryItem] = []
        for row in rows:
            sv = row.structured_value or {}
            if device_id and sv.get("device_id") != device_id:
                continue
            if room and sv.get("room") != room:
                continue
            if device_type and sv.get("device_type") != device_type:
                continue
            if prop and sv.get("property") != prop:
                continue
            matched.append(row)
        return matched

    def keyword_search(
        self,
        query: str,
        *,
        profile_id: str = "default",
        category: str | None = None,
        limit: int = 8,
    ) -> list[tuple[MemoryItem, float]]:
        """Unicode-safe hybrid PostgreSQL FTS ('simple') + token overlap retrieval."""
        q_clean = (query or "").strip()
        if not q_clean:
            return []
        now = _utc_now()
        tokens = _unicode_tokens(q_clean)

        with Session(self.engine) as session:
            # Tier 2a: PostgreSQL 'simple' tsvector GIN query
            fts_ids: set[uuid.UUID] = set()
            fts_stmt = select(MemoryItem.id).where(
                MemoryItem.profile_id == profile_id,
                MemoryItem.enabled.is_(True),
                or_(MemoryItem.expires_at_utc.is_(None), MemoryItem.expires_at_utc > now),
                text("to_tsvector('simple', original_text) @@ plainto_tsquery('simple', :q)"),
            )
            if category:
                fts_stmt = fts_stmt.where(MemoryItem.category == category)
            for mid in session.scalars(fts_stmt, {"q": q_clean}).all():
                fts_ids.add(mid)

            # Tier 2b: Load active items for exact Unicode substring / token overlap ranking
            all_stmt = select(MemoryItem).where(
                MemoryItem.profile_id == profile_id,
                MemoryItem.enabled.is_(True),
                or_(MemoryItem.expires_at_utc.is_(None), MemoryItem.expires_at_utc > now),
            )
            if category:
                all_stmt = all_stmt.where(MemoryItem.category == category)
            candidates = list(session.scalars(all_stmt.order_by(MemoryItem.updated_at_utc.desc())).all())

            scored: list[tuple[float, MemoryItem]] = []
            q_low = q_clean.lower()
            for item in candidates:
                haystack = " ".join(
                    [
                        item.original_text,
                        item.canonical_key or "",
                        str(item.structured_value or {}),
                    ]
                ).lower()
                score = 0.0
                if item.id in fts_ids:
                    score += 0.5
                if q_low in haystack:
                    score += 0.6
                if tokens:
                    hits = sum(1 for tok in tokens if tok in haystack)
                    if hits > 0:
                        score += hits / max(len(tokens), 1)
                if score > 0.0:
                    session.expunge(item)
                    scored.append((round(min(1.0, score), 4), item))

        scored.sort(key=lambda pair: (pair[0], pair[1].updated_at_utc), reverse=True)
        return [(item, sc) for sc, item in scored[:limit]]

    def semantic_search(
        self,
        query_embedding: list[float],
        *,
        embedding_model: str,
        profile_id: str = "default",
        category: str | None = None,
        limit: int = 8,
        min_similarity: float = 0.35,
    ) -> list[tuple[MemoryItem, float]]:
        """Exact pgvector cosine similarity search scoped to matching embedding_model."""
        now = _utc_now()
        dist_expr = MemoryItem.embedding.cosine_distance(query_embedding)
        with Session(self.engine) as session:
            stmt = (
                select(MemoryItem, (1.0 - dist_expr).label("similarity"))
                .where(
                    MemoryItem.profile_id == profile_id,
                    MemoryItem.enabled.is_(True),
                    or_(MemoryItem.expires_at_utc.is_(None), MemoryItem.expires_at_utc > now),
                    MemoryItem.embedding.is_not(None),
                    MemoryItem.embedding_model == embedding_model,
                )
                .order_by(dist_expr.asc())
                .limit(max(1, limit))
            )
            if category:
                stmt = stmt.where(MemoryItem.category == category)
            results: list[tuple[MemoryItem, float]] = []
            for item, sim in session.execute(stmt).all():
                sim_val = float(sim or 0.0)
                if sim_val >= min_similarity:
                    session.expunge(item)
                    results.append((item, round(sim_val, 4)))
            return results

    def append_conversation_turn(
        self,
        *,
        profile_id: str = "default",
        turn_id: str | None,
        user_text: str,
        assistant_text: str,
        language: str,
        intent: str,
    ) -> None:
        now = _utc_now()
        with Session(self.engine) as session:
            session.add(
                ConversationHistoryItem(
                    profile_id=profile_id,
                    turn_id=turn_id,
                    role="user",
                    content=user_text,
                    language=language,
                    intent=intent,
                    created_at_utc=now,
                )
            )
            session.add(
                ConversationHistoryItem(
                    profile_id=profile_id,
                    turn_id=turn_id,
                    role="assistant",
                    content=assistant_text,
                    language=language,
                    intent=intent,
                    created_at_utc=now,
                )
            )
            session.commit()

    def list_conversation_history(
        self, *, profile_id: str = "default", limit: int = 50
    ) -> list[ConversationHistoryItem]:
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(ConversationHistoryItem)
                    .where(ConversationHistoryItem.profile_id == profile_id)
                    .order_by(ConversationHistoryItem.id.desc())
                    .limit(max(1, min(limit, 500)))
                ).all()
            )
            for row in rows:
                session.expunge(row)
            return list(reversed(rows))

    def clear_conversation_history(self, *, profile_id: str = "default") -> int:
        with Session(self.engine) as session:
            res = session.execute(
                delete(ConversationHistoryItem).where(
                    ConversationHistoryItem.profile_id == profile_id
                )
            )
            session.commit()
            return int(res.rowcount or 0)
