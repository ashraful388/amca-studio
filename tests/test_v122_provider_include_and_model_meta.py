"""Provider include/exclude toggle and operator-recorded model metadata.

ZCODE-AMCA-STUDIO-NEXT-MODEL-SETTINGS-SURFACE-02

The Model settings surface gained, per the operator's reference design:

* a provider-level include/exclude toggle.  Excluded is a stored operator
  decision: an excluded provider publishes nothing into the chat model
  selectors, whatever its per-model visibility says;
* operator-recorded per-model metadata (context window, max output tokens).
  These are what the operator typed about their own provider -- AMCA has not
  measured them, so they are stored verbatim, displayed back as recorded, and
  read by no routing, costing or verification path.

Pinned contracts:

* existing databases migrate additively and idempotently (providers keep
  enabled=1; the meta table simply appears);
* an excluded provider yields no Auto Intelligence candidates while a healthy
  included provider still does;
* metadata survives a metadata-only provider update and dies with the
  provider;
* metadata never leaks into a routing candidate's context window.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
SOFTWARE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOFTWARE_ROOT / "src"))

import pytest  # noqa: E402

from amca.credentials import MemoryCredentialStore  # noqa: E402
from amca.model_hub import ModelHubStore  # noqa: E402
from amca.service import ServiceContext  # noqa: E402

_contexts: list = []


def _close_all_contexts() -> None:
    while _contexts:
        context = _contexts.pop()
        try:
            context.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _cleanup_service_contexts():
    yield
    _close_all_contexts()


def _context(td: str) -> ServiceContext:
    context = ServiceContext(
        root=Path(td),
        database=Path(td) / "amca.sqlite3",
        benchmarks=Path(td),
        credential_store=MemoryCredentialStore(),
    )
    _contexts.append(context)
    return context


def _create_hub_provider(context: ServiceContext, name: str, model: str, endpoint: str):
    result = context.create_hub_provider({
        "provider_kind": "custom",
        "name": name,
        "endpoint": endpoint,
        "secret": "test-key",
        "model_ids": [model],
    })
    return result["provider"]


def _auto_candidates(context: ServiceContext):
    candidates, _required, _quality, _target = context._auto_model_candidates(
        goal="Reply briefly.",
        attachment_names=[],
        inventory={"models": [], "runtime_reachable": False},
        profiles=[],
    )
    return [item.model_ref for item in candidates]


# -- store level ------------------------------------------------------------


class ProviderEnabledStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"
        self.store = ModelHubStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_new_provider_is_included_by_default(self):
        provider, _ = self.store.create_provider(
            {"provider_kind": "custom", "name": "P", "endpoint": ""}
        )
        assert provider["enabled"] is True
        assert self.store.list_providers()[0]["enabled"] is True

    def test_exclude_persists_and_survives_a_metadata_only_update(self):
        provider, _ = self.store.create_provider(
            {"provider_kind": "custom", "name": "P", "endpoint": "", "model_ids": ["m-1"]}
        )
        excluded = self.store.update_provider(provider["id"], {"enabled": False})
        assert excluded["enabled"] is False
        # A later rename must not silently re-include the provider.
        renamed = self.store.update_provider(provider["id"], {"name": "Renamed"})
        assert renamed["enabled"] is False
        assert renamed["name"] == "Renamed"

    def test_enabled_rejects_non_boolean(self):
        provider, _ = self.store.create_provider(
            {"provider_kind": "custom", "name": "P", "endpoint": ""}
        )
        with pytest.raises(ValueError):
            self.store.update_provider(provider["id"], {"enabled": "no"})


class ModelMetaStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "amca.sqlite3"
        self.store = ModelHubStore(self.database)
        self.provider, _ = self.store.create_provider(
            {"provider_kind": "custom", "name": "P", "endpoint": "", "model_ids": ["m-1"]}
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_meta_starts_empty_and_records_operator_values(self):
        assert self.store.list_providers()[0]["model_meta"] == {}
        updated = self.store.set_model_meta(
            self.provider["id"], "m-1", {"context_window": 128000}
        )
        meta = updated["model_meta"]["m-1"]
        assert meta == {"context_window": 128000, "max_output_tokens": None}

    def test_meta_merges_partially_and_clears_with_none(self):
        self.store.set_model_meta(
            self.provider["id"], "m-1", {"context_window": 128000, "max_output_tokens": 8192}
        )
        updated = self.store.set_model_meta(
            self.provider["id"], "m-1", {"max_output_tokens": None}
        )
        # A partial write keeps the untouched field; an explicit null clears
        # exactly the field it names.
        assert updated["model_meta"]["m-1"] == {"context_window": 128000, "max_output_tokens": None}

    def test_meta_rejects_non_integer_and_negative_values(self):
        for bad in (True, "big", 1.5, -1):
            with pytest.raises(ValueError):
                self.store.set_model_meta(
                    self.provider["id"], "m-1", {"context_window": bad}
                )

    def test_meta_rejects_values_beyond_sqlite_integer_range(self):
        # 2**63 would raise OverflowError at bind time, which escapes the
        # service's 400 mapping; it must be a plain ValueError instead.
        with pytest.raises(ValueError):
            self.store.set_model_meta(
                self.provider["id"], "m-1", {"context_window": 2**63}
            )
        stored = self.store.set_model_meta(
            self.provider["id"], "m-1", {"context_window": 2**63 - 1}
        )
        assert stored["model_meta"]["m-1"]["context_window"] == 2**63 - 1

    def test_meta_rejects_unknown_provider_and_unknown_model_and_empty_meta(self):
        with pytest.raises(ValueError):
            self.store.set_model_meta("provider.nope", "m-1", {"context_window": 1})
        with pytest.raises(ValueError):
            self.store.set_model_meta(self.provider["id"], "m-other", {"context_window": 1})
        with pytest.raises(ValueError):
            self.store.set_model_meta(self.provider["id"], "m-1", {})

    def test_meta_dies_with_the_provider(self):
        self.store.set_model_meta(self.provider["id"], "m-1", {"context_window": 4096})
        self.store.delete_provider(self.provider["id"])
        fresh, _ = self.store.create_provider(
            {"provider_kind": "custom", "name": "P", "endpoint": "", "model_ids": ["m-1"]}
        )
        # A new provider that reuses the same model id must not inherit the
        # deleted provider's recorded metadata.
        assert fresh["model_meta"] == {}

    def test_readding_the_same_model_id_shows_its_own_record_again(self):
        """A discovery that transiently omits a model, or an operator edit
        that removes one, must never destroy operator-typed records: the row
        stays in the database, is hidden while the model is out of the
        catalog, and is the model's own record again when it returns."""
        self.store.set_model_meta(self.provider["id"], "m-1", {"context_window": 4096})
        self.store.update_provider(self.provider["id"], {"model_ids": []})
        hidden = self.store.provider(self.provider["id"])
        assert hidden is not None
        assert hidden["model_meta"] == {}
        self.store.update_provider(self.provider["id"], {"model_ids": ["m-1"]})
        updated = self.store.provider(self.provider["id"])
        assert updated is not None
        # Same provider, same model id: the operator's own recorded numbers
        # for that model are shown again. Only provider deletion clears them.
        assert updated["model_meta"] == {"m-1": {"context_window": 4096, "max_output_tokens": None}}


class MigrationTests(unittest.TestCase):
    def test_database_created_before_the_toggle_migrates_additively(self):
        with tempfile.TemporaryDirectory() as td:
            database = Path(td) / "amca.sqlite3"
            # Build the pre-toggle schema by hand: ten columns, no `enabled`,
            # no meta table.
            raw = sqlite3.connect(database)
            raw.executescript(
                """
                CREATE TABLE model_hub_providers (
                    id TEXT PRIMARY KEY, provider_kind TEXT NOT NULL, name TEXT NOT NULL,
                    endpoint TEXT NOT NULL, credential_ref TEXT NOT NULL,
                    status TEXT NOT NULL, pricing_mode TEXT NOT NULL,
                    model_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                """
            )
            raw.execute(
                "INSERT INTO model_hub_providers VALUES"
                " ('provider.old', 'custom', 'Old', '', 'model-hub.provider.old',"
                "  'catalog_ready', 'auto', '[\"m-1\"]', 't', 't')"
            )
            raw.commit()
            raw.close()

            store = ModelHubStore(database)
            providers = store.list_providers()
            assert [item["enabled"] for item in providers] == [True]
            assert providers[0]["model_meta"] == {}
            # The migrated provider can then be excluded like any other.
            excluded = store.update_provider("provider.old", {"enabled": False})
            assert excluded["enabled"] is False

            # Reopening stays idempotent: no duplicate migration, no error.
            reopened = ModelHubStore(database)
            assert reopened.list_providers()[0]["enabled"] is False


# -- service level ----------------------------------------------------------


def test_excluded_provider_yields_no_auto_candidates():
    """Exclusion is the operator's stored decision: an excluded provider
    publishes nothing into Auto Intelligence, whatever its per-model
    visibility says, and an included provider is unaffected."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        excluded = _create_hub_provider(
            context, "Provider Excluded", "ex-model", "https://excluded.example/v1"
        )
        included = _create_hub_provider(
            context, "Provider Included", "in-model", "https://included.example/v1"
        )
        context.update_hub_provider(excluded["id"], {"enabled": False})

        refs = _auto_candidates(context)
        assert refs == [f"{included['id']}:in-model"]


def test_model_meta_never_reaches_routing_candidates():
    """Operator-recorded metadata is display state, not a routing fact: the
    candidate's context window stays the registry value (0 = unestablished),
    never the number the operator typed."""

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider Meta", "meta-model", "https://meta.example/v1"
        )
        context.set_hub_model_meta(
            provider["id"], {"model_id": "meta-model", "meta": {"context_window": 999999}}
        )
        candidates, _required, _quality, _target = context._auto_model_candidates(
            goal="Reply briefly.",
            attachment_names=[],
            inventory={"models": [], "runtime_reachable": False},
            profiles=[],
        )
        assert len(candidates) == 1
        # 0 is the sentinel for "not established"; a recorded window from a
        # probe would be non-zero, and the operator's 999999 must not leak in.
        assert candidates[0].context_length == 0


def test_set_hub_model_meta_service_contract():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        context = _context(td)
        provider = _create_hub_provider(
            context, "Provider Meta", "meta-model", "https://meta.example/v1"
        )
        result = context.set_hub_model_meta(
            provider["id"],
            {"model_id": "meta-model", "meta": {"max_output_tokens": 4096}},
        )
        assert result["provider"]["model_meta"]["meta-model"] == {
            "context_window": None,
            "max_output_tokens": 4096,
        }
        with pytest.raises(ValueError):
            context.set_hub_model_meta(provider["id"], {"model_id": "", "meta": {}})


if __name__ == "__main__":
    unittest.main()
