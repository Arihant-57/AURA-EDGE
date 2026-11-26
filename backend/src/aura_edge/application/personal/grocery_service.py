"""Phase 4 Grocery, Household Inventory, and Offline Recipe matching service."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.data.models import GroceryInventoryItem, LocalRecipeRecord, ShoppingListItem
from aura_edge.data.repositories.personal import GroceryRepository

_ALIASES: dict[str, tuple[str, ...]] = {
    "milk": ("milk", "दूध", "doodh", "ಹಾಲು", "haalu"),
    "tea_leaves": ("tea leaves", "tea powder", "chai patti", "चाय पत्ती", "ಟೀ ಪುಡಿ"),
    "sugar": ("sugar", "चीनी", "शक्कर", "ಸಕ್ಕರೆ"),
    "rice": ("rice", "चावल", "chawal", "ಅಕ್ಕಿ", "akki"),
    "tomato": ("tomato", "tomatoes", "टमाटर", "ಟೊಮೆಟೊ"),
    "onion": ("onion", "onions", "प्याज", "pyaaz", "ಈರುಳ್ಳಿ"),
    "lentils": ("lentils", "dal", "toor dal", "दाल", "ಬೇಳೆ"),
    "potato": ("potato", "potatoes", "आलू", "aloo", "ಆಲೂಗಡ್ಡೆ"),
    "wheat_flour": ("wheat flour", "atta", "आटा", "ಗೋಧಿ ಹಿಟ್ಟು"),
    "curd": ("curd", "yogurt", "dahi", "दही", "ಮೊಸರು"),
    "poha": ("poha", "flattened rice", "पोहा", "ಅವಲಕ್ಕಿ"),
    "besan": ("besan", "gram flour", "बेसन", "ಕಡಲೆ ಹಿಟ್ಟು"),
    "lemon": ("lemon", "नींबू", "ನಿಂಬೆಹಣ್ಣು"),
    "mustard_seeds": ("mustard seeds", "rai", "राई", "ಸಾಸಿವೆ"),
    "turmeric": ("turmeric", "haldi", "हल्दी", "ಅರಿಶಿನ"),
    "oil": ("oil", "cooking oil", "तेल", "ಎಣ್ಣೆ"),
    "salt": ("salt", "नमक", "ಉಪ್ಪು"),
}

DEFAULT_OFFLINE_RECIPES: list[dict[str, Any]] = [
    {
        "id": "masala_chai",
        "title": "Masala Chai",
        "aliases": ["chai", "tea", "चाय", "ಟೀ", "ಮಸಾಲಾ ಚಹಾ"],
        "cuisine": "indian",
        "prep_minutes": 10,
        "ingredients": [
            {"name": "milk", "quantity": 1, "unit": "cup"},
            {"name": "tea_leaves", "quantity": 2, "unit": "tsp"},
            {"name": "sugar", "quantity": 2, "unit": "tsp"},
        ],
        "steps": ["Boil water with tea leaves.", "Add milk and sugar and simmer."],
    },
    {
        "id": "vegetable_poha",
        "title": "Vegetable Poha",
        "aliases": ["poha", "पोहा", "ಅವಲಕ್ಕಿ"],
        "cuisine": "indian",
        "prep_minutes": 15,
        "ingredients": [
            {"name": "poha", "quantity": 2, "unit": "cup"},
            {"name": "onion", "quantity": 1, "unit": "unit"},
            {"name": "potato", "quantity": 1, "unit": "unit"},
            {"name": "lemon", "quantity": 1, "unit": "unit"},
            {"name": "mustard_seeds", "quantity": 1, "unit": "tsp"},
        ],
        "steps": ["Rinse poha.", "Temper mustard seeds, saute onion and potato, fold in poha."],
    },
    {
        "id": "dal_tadka",
        "title": "Dal Tadka",
        "aliases": ["dal", "दाल", "ಬೇಳೆ ಸಾರು"],
        "cuisine": "indian",
        "prep_minutes": 25,
        "ingredients": [
            {"name": "lentils", "quantity": 1, "unit": "cup"},
            {"name": "tomato", "quantity": 1, "unit": "unit"},
            {"name": "onion", "quantity": 1, "unit": "unit"},
            {"name": "turmeric", "quantity": 1, "unit": "tsp"},
        ],
        "steps": ["Cook lentils with turmeric.", "Top with tomato and onion tempering."],
    },
    {
        "id": "tomato_rice",
        "title": "Tomato Rice",
        "aliases": ["tomato bath", "टोमैटो राइस", "ಟೊಮೆಟೊ ಬಾತ್"],
        "cuisine": "south_indian",
        "prep_minutes": 20,
        "ingredients": [
            {"name": "rice", "quantity": 1, "unit": "cup"},
            {"name": "tomato", "quantity": 2, "unit": "unit"},
            {"name": "onion", "quantity": 1, "unit": "unit"},
            {"name": "mustard_seeds", "quantity": 1, "unit": "tsp"},
        ],
        "steps": ["Cook spiced tomato onion base.", "Mix with cooked rice."],
    },
    {
        "id": "besan_chilla",
        "title": "Besan Chilla",
        "aliases": ["chilla", "बेसन चीला", "ಕಡಲೆ ಹಿಟ್ಟಿನ ದೋಸೆ"],
        "cuisine": "indian",
        "prep_minutes": 15,
        "ingredients": [
            {"name": "besan", "quantity": 1, "unit": "cup"},
            {"name": "onion", "quantity": 1, "unit": "unit"},
            {"name": "tomato", "quantity": 1, "unit": "unit"},
            {"name": "oil", "quantity": 1, "unit": "tbsp"},
        ],
        "steps": ["Whisk besan batter with chopped veggies.", "Cook on hot skillet with oil."],
    },
    {
        "id": "curd_rice",
        "title": "Curd Rice",
        "aliases": ["dahi chawal", "दही चावल", "ಮೊಸರನ್ನ"],
        "cuisine": "south_indian",
        "prep_minutes": 10,
        "ingredients": [
            {"name": "rice", "quantity": 1, "unit": "cup"},
            {"name": "curd", "quantity": 1, "unit": "cup"},
            {"name": "mustard_seeds", "quantity": 1, "unit": "tsp"},
        ],
        "steps": ["Mash warm cooked rice with curd.", "Add mustard seed tempering."],
    },
    {
        "id": "aloo_paratha",
        "title": "Aloo Paratha",
        "aliases": ["paratha", "आलू पराठा", "ಆಲೂ ಪರೋಟಾ"],
        "cuisine": "north_indian",
        "prep_minutes": 30,
        "ingredients": [
            {"name": "wheat_flour", "quantity": 2, "unit": "cup"},
            {"name": "potato", "quantity": 2, "unit": "unit"},
            {"name": "oil", "quantity": 1, "unit": "tbsp"},
        ],
        "steps": ["Stuff spiced potato mash into atta dough.", "Roast on tawa."],
    },
    {
        "id": "lemon_rice",
        "title": "Lemon Rice",
        "aliases": ["chitranna", "लेमन राइस", "ಚಿತ್ರಾನ್ನ"],
        "cuisine": "south_indian",
        "prep_minutes": 15,
        "ingredients": [
            {"name": "rice", "quantity": 1, "unit": "cup"},
            {"name": "lemon", "quantity": 1, "unit": "unit"},
            {"name": "mustard_seeds", "quantity": 1, "unit": "tsp"},
            {"name": "turmeric", "quantity": 1, "unit": "tsp"},
        ],
        "steps": ["Temper mustard seeds and turmeric.", "Toss cooked rice with fresh lemon juice."],
    },
]


def canonicalize_ingredient(name: str) -> str:
    clean = " ".join((name or "").split()).strip().lower()
    for canon, aliases in _ALIASES.items():
        if clean == canon or clean in aliases:
            return canon
    return clean.replace(" ", "_")


class GroceryService:
    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = GroceryRepository(engine)
        self.ws_hub = ws_hub
        self._clock = clock or (lambda: datetime.now(UTC))

    def ensure_seeded(self) -> None:
        self.repo.ensure_recipes(DEFAULT_OFFLINE_RECIPES)

    @staticmethod
    def shopping_to_dict(row: ShoppingListItem) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "item_name": row.item_name,
            "canonical_name": row.canonical_name,
            "quantity": float(row.quantity),
            "unit": row.unit,
            "purchased": row.purchased,
            "added_via": row.added_via,
            "created_at_utc": row.created_at_utc.isoformat(),
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    def inventory_to_dict(self, row: GroceryInventoryItem) -> dict[str, Any]:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        exp = row.expires_at_utc
        exp_soon = bool(exp is not None and exp <= now + timedelta(days=3))
        return {
            "id": str(row.id),
            "canonical_name": row.canonical_name,
            "display_name": row.display_name,
            "aliases": list(row.aliases_json or []),
            "category": row.category,
            "quantity": float(row.quantity),
            "unit": row.unit,
            "min_threshold": float(row.min_threshold),
            "is_low_stock": row.quantity < row.min_threshold or row.quantity == 0,
            "expires_at_utc": exp.isoformat() if exp else None,
            "expiring_soon": exp_soon,
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    @staticmethod
    def recipe_to_dict(row: LocalRecipeRecord) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "aliases": list(row.aliases_json or []),
            "cuisine": row.cuisine,
            "prep_minutes": row.prep_minutes,
            "ingredients": list(row.ingredients_json or []),
            "steps": list(row.steps_json or []),
        }

    async def add_to_shopping_list(
        self,
        *,
        item_name: str,
        quantity: Decimal | float = 1,
        unit: str = "unit",
        added_via: str = "manual",
    ) -> dict[str, Any]:
        clean = " ".join((item_name or "").split()).strip()
        if not clean:
            raise ValueError("Item name cannot be empty.")
        qty = Decimal(str(quantity))
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        canon = canonicalize_ingredient(clean)
        row = self.repo.add_or_increment_shopping(
            item_name=clean,
            canonical_name=canon,
            quantity=qty,
            unit=(unit or "unit").strip()[:32],
            added_via=added_via if added_via in {"manual", "voice", "low_stock", "recipe"} else "manual",
        )
        view = self.shopping_to_dict(row)
        await self.ws_hub.broadcast("grocery_updated", {"scope": "shopping_list", "action": "added", "item": view})
        return view

    def list_shopping(self, *, include_purchased: bool = True) -> list[dict[str, Any]]:
        return [
            self.shopping_to_dict(r)
            for r in self.repo.list_shopping(include_purchased=include_purchased)
        ]

    async def update_shopping_item(
        self,
        item_id: uuid.UUID,
        *,
        purchased: bool | None = None,
        quantity: Decimal | float | None = None,
        unit: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if purchased is not None:
            fields["purchased"] = purchased
        if quantity is not None:
            q = Decimal(str(quantity))
            if q <= 0:
                raise ValueError("Quantity must be greater than zero.")
            fields["quantity"] = q
        if unit is not None:
            fields["unit"] = unit.strip()[:32]
        row = self.repo.update_shopping(item_id, **fields)
        if row is None:
            raise LookupError(f"Shopping item '{item_id}' not found.")
        view = self.shopping_to_dict(row)
        await self.ws_hub.broadcast("grocery_updated", {"scope": "shopping_list", "action": "updated", "item": view})
        return view

    async def delete_shopping_item(self, item_id: uuid.UUID) -> bool:
        ok = self.repo.delete_shopping(item_id)
        if not ok:
            raise LookupError(f"Shopping item '{item_id}' not found.")
        await self.ws_hub.broadcast(
            "grocery_updated",
            {"scope": "shopping_list", "action": "deleted", "item_id": str(item_id)},
        )
        return True

    async def upsert_inventory_item(
        self,
        *,
        name: str,
        quantity: Decimal | float = 1,
        unit: str = "unit",
        category: str = "pantry",
        min_threshold: Decimal | float = 1,
        expires_at_utc: datetime | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        clean = " ".join((name or "").split()).strip()
        if not clean:
            raise ValueError("Inventory item name cannot be empty.")
        qty = Decimal(str(quantity))
        thresh = Decimal(str(min_threshold))
        if qty < 0 or thresh < 0:
            raise ValueError("Quantity and min_threshold must be non-negative.")
        canon = canonicalize_ingredient(clean)
        row = self.repo.upsert_inventory(
            canonical_name=canon,
            display_name=clean,
            aliases=list(aliases or _ALIASES.get(canon, ())),
            category=(category or "pantry").strip().lower()[:64],
            quantity=qty,
            unit=(unit or "unit").strip()[:32],
            min_threshold=thresh,
            expires_at_utc=expires_at_utc,
        )
        view = self.inventory_to_dict(row)
        await self.ws_hub.broadcast("grocery_updated", {"scope": "inventory", "action": "upserted", "item": view})
        return view

    def list_inventory(self) -> list[dict[str, Any]]:
        return [self.inventory_to_dict(r) for r in self.repo.list_inventory()]

    async def delete_inventory_item(self, item_id: uuid.UUID) -> bool:
        ok = self.repo.delete_inventory(item_id)
        if not ok:
            raise LookupError(f"Inventory item '{item_id}' not found.")
        await self.ws_hub.broadcast(
            "grocery_updated",
            {"scope": "inventory", "action": "deleted", "item_id": str(item_id)},
        )
        return True

    def list_missing_or_low_stock(self) -> list[dict[str, Any]]:
        return [item for item in self.list_inventory() if item["is_low_stock"]]

    def suggest_recipes(self, *, query: str | None = None) -> list[dict[str, Any]]:
        self.ensure_seeded()
        inv_rows = self.repo.list_inventory()
        in_stock: set[str] = set()
        for row in inv_rows:
            if row.quantity > 0:
                in_stock.add(row.canonical_name)
                for al in row.aliases_json or []:
                    in_stock.add(canonicalize_ingredient(str(al)))

        q_low = (query or "").strip().lower()
        results: list[dict[str, Any]] = []
        for rec in self.repo.list_recipes():
            if q_low:
                hay = " ".join([rec.title.lower(), *[str(a).lower() for a in (rec.aliases_json or [])]])
                if q_low not in hay and rec.id not in q_low:
                    continue
            available: list[str] = []
            missing: list[str] = []
            for ing in rec.ingredients_json or []:
                canon_ing = canonicalize_ingredient(str(ing.get("name", "")))
                if canon_ing in in_stock:
                    available.append(canon_ing)
                else:
                    missing.append(canon_ing)
            results.append(
                {
                    **self.recipe_to_dict(rec),
                    "available_ingredients": available,
                    "missing_ingredients": missing,
                    "can_make_now": len(missing) == 0 and len(available) > 0,
                    "match_ratio": round(len(available) / max(1, len(available) + len(missing)), 2),
                }
            )
        results.sort(key=lambda r: (r["can_make_now"], r["match_ratio"]), reverse=True)
        return results
