"""Portable-preview regressions for the host-mounted HTTP control contract.

The documented contract is a stable 400 for a missing/empty expected instance,
409 for a foreign instance, and documented error codes instead of FastAPI's
framework-generated 422 for this API. These tests mount the real router behind
a controlled registry/runtime so the HTTP layer, not the runtime, is exercised.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import advisor_host  # noqa: E402
import dashboard.plugin_api as plugin_api  # noqa: E402
from tests._scratch_root import SCRATCH  # noqa: E402

SCRATCH.mkdir(parents=True, exist_ok=True)
PREFIX = "/api/plugins/hermes-advisor"
SESSION = "preview-session"


class _FakeRuntime:
    instance_id = "preview-instance"
    closed = False
    profile = "preview-profile"

    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def _assert_target(self, expected_instance, session_id):
        if type(expected_instance) is not str or expected_instance != self.instance_id:
            raise advisor_host.BackendRejected("backend_instance_conflict")
        if session_id != SESSION:
            raise KeyError("session_not_discovered")

    def metadata(self):
        return {"instance_id": self.instance_id, "profile": self.profile, "sessions": []}

    def status(self, expected_instance, session_id):
        self._assert_target(expected_instance, session_id)
        return {"session_id": session_id, "state": "enabled"}

    def notes(self, expected_instance, session_id):
        self._assert_target(expected_instance, session_id)
        return []

    def control(self, expected_instance, session_id, action, params):
        self._assert_target(expected_instance, session_id)
        return {"ok": True, "action": action, "params": params}


class PreviewApiContractTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        data_dir = Path(self._temp.name) / "plugin-data" / plugin_api.PLUGIN_ID
        data_dir.mkdir(parents=True)
        self.data_dir = str(data_dir.resolve())
        self._saved = {name: sys.modules.get(name) for name in
                       ("plugins", "plugins.plugin_storage", plugin_api.REGISTRY_NAME)}

        plugins = types.ModuleType("plugins")
        plugins.__path__ = []
        storage = types.ModuleType("plugins.plugin_storage")
        setattr(storage, "plugin_data_dir", lambda _plugin_id: data_dir)
        registry = types.ModuleType(plugin_api.REGISTRY_NAME)
        setattr(registry, "lock", threading.RLock())
        setattr(registry, "runtimes", {self.data_dir: _FakeRuntime(self.data_dir)})
        sys.modules["plugins"] = plugins
        sys.modules["plugins.plugin_storage"] = storage
        sys.modules[plugin_api.REGISTRY_NAME] = registry
        self.registry = registry

        app = FastAPI()
        app.include_router(plugin_api.router, prefix=PREFIX)
        self.client = TestClient(app)

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        self._temp.cleanup()

    def _error(self, response):
        self.assertEqual(response.headers.get("content-type", "").split(";")[0],
                         "application/json")
        return response.json()["detail"]["error"]

    def test_metadata_uses_the_registered_owner(self):
        response = self.client.get(f"{PREFIX}/metadata")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["instance_id"], _FakeRuntime.instance_id)

    def test_status_and_notes_accept_the_matching_instance(self):
        status = self.client.get(f"{PREFIX}/status",
                                 params={"session_id": SESSION,
                                         "expected_instance": _FakeRuntime.instance_id})
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "enabled")
        notes = self.client.get(f"{PREFIX}/notes",
                                params={"session_id": SESSION,
                                        "expected_instance": _FakeRuntime.instance_id})
        self.assertEqual(notes.status_code, 200)
        self.assertEqual(notes.json(), [])

    def test_status_missing_expected_instance_is_documented_400_not_422(self):
        response = self.client.get(f"{PREFIX}/status", params={"session_id": SESSION})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_status_empty_expected_instance_is_documented_400_not_409(self):
        response = self.client.get(f"{PREFIX}/status",
                                   params={"session_id": SESSION, "expected_instance": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_status_missing_session_id_is_documented_400_not_422(self):
        response = self.client.get(f"{PREFIX}/status",
                                   params={"expected_instance": _FakeRuntime.instance_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_status_foreign_instance_is_409(self):
        response = self.client.get(f"{PREFIX}/status",
                                   params={"session_id": SESSION, "expected_instance": "foreign"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._error(response), "backend_instance_conflict")

    def test_notes_missing_or_empty_expected_instance_is_documented_400(self):
        for params in ({"session_id": SESSION},
                       {"session_id": SESSION, "expected_instance": ""}):
            with self.subTest(params=params):
                response = self.client.get(f"{PREFIX}/notes", params=params)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self._error(response), "invalid_control_shape")

    def test_notes_foreign_instance_is_409(self):
        response = self.client.get(f"{PREFIX}/notes",
                                   params={"session_id": SESSION, "expected_instance": "foreign"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._error(response), "backend_instance_conflict")

    def _body(self, **overrides):
        body = {"expected_instance": _FakeRuntime.instance_id, "session_id": SESSION,
                "action": "enable", "params": {}}
        body.update(overrides)
        return body

    def test_control_accepts_the_matching_instance(self):
        response = self.client.post(f"{PREFIX}/control", json=self._body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "enable")

    def test_control_missing_expected_instance_is_documented_400(self):
        body = self._body()
        body.pop("expected_instance")
        response = self.client.post(f"{PREFIX}/control", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_control_empty_expected_instance_is_documented_400_not_409(self):
        response = self.client.post(f"{PREFIX}/control", json=self._body(expected_instance=""))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_control_foreign_instance_is_409(self):
        response = self.client.post(f"{PREFIX}/control", json=self._body(expected_instance="foreign"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._error(response), "backend_instance_conflict")

    def test_control_type_and_shape_failures_keep_documented_codes(self):
        response = self.client.post(f"{PREFIX}/control", json=self._body(expected_instance=1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_types")

        response = self.client.post(f"{PREFIX}/control", json={**self._body(), "extra": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

        body = self._body()
        body.pop("params")
        response = self.client.post(f"{PREFIX}/control", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

    def test_malformed_or_non_object_control_body_is_documented_400(self):
        cases = {
            "invalid json": b"{not json",
            "array": b"[]",
            "string": b'"text"',
            "null": b"null",
            "empty": b"",
        }
        for label, raw in cases.items():
            with self.subTest(label):
                response = self.client.post(f"{PREFIX}/control", content=raw,
                                            headers={"content-type": "application/json"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self._error(response), "invalid_control_shape")

    def test_unknown_session_still_returns_404_after_instance_validation(self):
        response = self.client.get(f"{PREFIX}/status",
                                   params={"session_id": "unknown",
                                           "expected_instance": _FakeRuntime.instance_id})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._error(response), "session_not_discovered")

    def test_shape_validation_needs_no_owner(self):
        self.registry.runtimes.clear()
        response = self.client.get(f"{PREFIX}/status", params={"session_id": SESSION})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._error(response), "invalid_control_shape")

        response = self.client.get(f"{PREFIX}/status",
                                   params={"session_id": SESSION,
                                           "expected_instance": _FakeRuntime.instance_id})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self._error(response), "hook_owner_unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
