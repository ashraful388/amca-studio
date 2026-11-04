"""Adaptive User Interaction and Explicit Preference Foundation (AG-AMCA-STUDIO-BETA-B9).

Provides privacy-first user interaction context modeling, explicit preference management,
and non-invasive, contextual assistance recommendations.
Zero covert behavioral tracking, zero external telemetry, strictly local SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class UserInteractionContext:
    context_id: str
    user_id: str
    active_view: str
    current_project_id: str
    recent_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "user_id": self.user_id,
            "active_view": self.active_view,
            "current_project_id": self.current_project_id,
            "recent_actions": list(self.recent_actions),
        }


@dataclass(frozen=True, slots=True)
class AdaptiveAssistanceRecommendation:
    recommendation_id: str
    category: str
    title: str
    description: str
    rationale: str
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "rationale": self.rationale,
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True, slots=True)
class UserPreferenceRecord:
    user_id: str
    preferred_model_id: str
    preferred_collaboration_mode: str
    ui_theme: str = "dark"
    notifications_enabled: bool = True
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "preferred_model_id": self.preferred_model_id,
            "preferred_collaboration_mode": self.preferred_collaboration_mode,
            "ui_theme": self.ui_theme,
            "notifications_enabled": self.notifications_enabled,
            "updated_at": self.updated_at,
        }


def generate_adaptive_assistance(
    context: UserInteractionContext,
) -> tuple[AdaptiveAssistanceRecommendation, ...]:
    """Derives context-aware assistance and shortcuts purely from current explicit UI state."""
    recs: list[AdaptiveAssistanceRecommendation] = []
    view = context.active_view.casefold().strip()

    if view == "chat":
        recs.append(
            AdaptiveAssistanceRecommendation(
                recommendation_id=f"help-{uuid4().hex[:12]}",
                category="shortcut",
                title="Quick Jump Navigation",
                description="Press Ctrl+K to jump between views or search projects instantly.",
                rationale="Active in Chat view where fast navigation helps productivity.",
                suggested_action="open_palette",
            )
        )
    elif view == "models":
        recs.append(
            AdaptiveAssistanceRecommendation(
                recommendation_id=f"help-{uuid4().hex[:12]}",
                category="feature_discovery",
                title="Local $0 Inference Transparency",
                description="Explore local Ollama models with zero token billing.",
                rationale="Active in Model Hub view.",
                suggested_action="filter_local_models",
            )
        )
    elif view == "agents":
        recs.append(
            AdaptiveAssistanceRecommendation(
                recommendation_id=f"help-{uuid4().hex[:12]}",
                category="next_step",
                title="Inspect Capability Profiles",
                description="Review specialized agent capabilities before dispatching team tasks.",
                rationale="Active in Agent Center view.",
                suggested_action="view_agent_capabilities",
            )
        )

    return tuple(recs)


class UserInteractionStore:
    """Project/user-partitioned SQLite store for explicit user preferences and assistance settings."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id TEXT PRIMARY KEY,
                    preferred_model_id TEXT NOT NULL,
                    preferred_collaboration_mode TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def save_preferences(self, prefs: UserPreferenceRecord) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO user_preferences VALUES (?, ?, ?, ?, ?)
                """,
                (
                    prefs.user_id,
                    prefs.preferred_model_id,
                    prefs.preferred_collaboration_mode,
                    json.dumps(prefs.to_dict()),
                    prefs.updated_at or _now(),
                ),
            )

    def get_preferences(self, user_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT record_json FROM user_preferences WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return json.loads(str(row["record_json"]))

