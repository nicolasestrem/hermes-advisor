"""Controlled fixture harness for CLI-control / explicit-feedback verification.

LABELS (kept honest on purpose):

* The real product objects under test are ``advisor_host.register`` / ``Runtime`` /
  ``EventAdapter`` and the real ``advisor_core.Service`` + SQLite store.
* ``FixtureContext`` is a LABELLED CONTROLLED FIXTURE of the public plugin
  context.  It exposes only the public surface the plugin may use
  (``has_capability``, ``register_hook``, ``register_command``, ``on_unload``,
  ``llm.acomplete_structured``).  Any other attribute access is recorded as a
  violation and raises, so a private host API / main-agent submission or
  steering path cannot be used silently.
* ``ScriptedReviewer`` is the controlled reviewer.  It performs NO inference and
  every response is scripted by the test; results carry the provider/model
  label ``controlled-fixture/scripted-reviewer`` so a scripted ``no_findings``
  can never be mistaken for real provider output.
* Hook and command dispatch (``Lab.turn`` / ``Lab.cmd``) is FIXTURE dispatch of
  the callbacks the plugin registered.  It is not genuine Hermes hook proof.
* No model, network, paid provider, Hermes profile, or production state is used.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shlex
import sqlite3
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import advisor_host  # noqa: E402  (real product module, imported as the repo's top-level module)
from advisor_core import ReviewerResult  # noqa: E402,F401

FIXTURE_PROVIDER = 'controlled-fixture'
FIXTURE_MODEL = 'scripted-reviewer'
ROUTE = {'provider': FIXTURE_PROVIDER, 'model': FIXTURE_MODEL}

# Obviously-synthetic credential lookalikes; each matches a core redaction rule.
FAKE_SECRETS = {
    'sk': 'sk-' + 'FIXTUREfixtureFIXTURE0001',
    'bearer': 'Bearer FIXTUREbearerTOKEN0002',
    'kv': 'api_key=FIXTUREkvVALUE0003',
    'url': 'https://fixtureuser:FIXTUREpass0004@example.invalid/x',
    'ghp': 'ghp_FIXTUREfixtureFIXTURE00050000',
}
FAKE_SECRET_CORES = ['FIXTUREfixtureFIXTURE0001', 'FIXTUREbearerTOKEN0002', 'FIXTUREkvVALUE0003',
                     'FIXTUREpass0004', 'fixtureuser', 'FIXTUREfixtureFIXTURE00050000']

ALLOWED_CTX = {'llm', 'has_capability', 'register_hook', 'register_command', 'on_unload'}

EVIDENCE_ENV = 'ADVISOR_CLI_FEEDBACK_EVIDENCE'
_evidence_lock = threading.Lock()
CURRENT_TEST = {'id': None}


def q(obj):
    """Shell-quote a JSON object as one single-quoted argument (CLI documented form)."""
    return shlex.quote(json.dumps(obj, separators=(',', ':')))


def _scrub(text):
    for core in FAKE_SECRET_CORES:
        text = text.replace(core, '<FIXTURE-SECRET>')
    return text


def record_evidence(kind, **fields):
    path = os.environ.get(EVIDENCE_ENV)
    if not path:
        return
    row = {'test': CURRENT_TEST['id'], 'kind': kind, 'label': 'FIXTURE-DISPATCH', **fields}
    line = _scrub(json.dumps(row, ensure_ascii=False, sort_keys=True))
    with _evidence_lock, open(path, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')


class StrictLLM:
    """Controlled facade fixture: only ``acomplete_structured`` exists."""

    def __init__(self, ctx):
        self._ctx = ctx
        self.calls = []
        self.script = []        # items consumed in FIFO order
        self.unscripted = []
        self.gate = None        # optional threading.Event to hold calls
        self._lock = threading.Lock()

    def push(self, *items):
        with self._lock:
            self.script.extend(items)

    async def acomplete_structured(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            item = self.script.pop(0) if self.script else None
        if item is None:
            self.unscripted.append(kwargs.get('purpose'))
            raise AssertionError('unscripted reviewer call')
        packet = json.loads(kwargs['input'][0]['text'])
        if callable(item):
            item = item(packet, kwargs)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict) and item.get('__findings__'):
            refs = item.get('refs') or ['answer']
            parsed = {'outcome': 'findings', 'findings': [
                {'severity': f.get('severity', 'concern'), 'explanation': f['explanation'],
                 'evidence_refs': f.get('evidence_refs', refs[:1]), 'next_step': f['next_step']}
                for f in item['__findings__']]}
        elif item == 'no_findings':
            parsed = {'outcome': 'no_findings', 'findings': []}
        else:
            parsed = item   # raw (possibly invalid) mapping
        return SimpleNamespace(parsed=parsed, provider=FIXTURE_PROVIDER, model=FIXTURE_MODEL, usage=None)

    def __getattr__(self, name):
        self._ctx.violations.append('llm.' + name)
        raise AttributeError(name)


def findings(*items):
    return {'__findings__': list(items)}


class FixtureContext:
    """Labelled controlled public-context fixture (see module docstring)."""

    def __init__(self, capabilities=('llm.provider_override', 'llm.model_override')):
        self.violations = []
        self.capabilities = set(capabilities)
        self.hooks = {}
        self.commands = {}
        self.unload = []
        self.llm = StrictLLM(self)

    def has_capability(self, name):
        return name in self.capabilities

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_command(self, **kwargs):
        self.commands[kwargs['name']] = kwargs

    def on_unload(self, callback):
        self.unload.append(callback)

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        self.violations.append(name)   # private/host API or main-agent steering attempt
        raise AttributeError(name)


class Lab:
    """One plugin registration against a profile-local data directory."""

    def __init__(self, tmp_root, profile='profile-a', capabilities=None):
        self.tmp_root = Path(tmp_root)
        self.profile = profile
        self.data_dir = self.tmp_root / profile / 'plugin-data' / advisor_host.PLUGIN_ID
        self.ctx = FixtureContext() if capabilities is None else FixtureContext(capabilities)
        self.transcript = []      # host-owned main transcript; the plugin gets copies only
        self.transcript_digests = []
        self._register()

    def _register(self):
        storage = types.ModuleType('plugins.plugin_storage')
        storage.plugin_data_dir = lambda plugin_id: str(self.data_dir)
        pkg = types.ModuleType('plugins')
        pkg.plugin_storage = storage
        # LABELLED FIXTURE of the public ``hermes_constants.profile_name_for_home`` identity function
        # (not importable in this interpreter): maps <tmp_root>/<profile> -> '<profile>'.
        constants = types.ModuleType('hermes_constants')
        constants.profile_name_for_home = lambda home: Path(home).name if Path(home).parent == self.tmp_root else None
        keys = ('plugins', 'plugins.plugin_storage', 'hermes_constants')
        saved = {k: sys.modules.get(k) for k in keys}
        sys.modules['plugins'] = pkg
        sys.modules['plugins.plugin_storage'] = storage
        sys.modules['hermes_constants'] = constants
        registered_atexit = []
        real_register = advisor_host.atexit.register
        advisor_host.atexit.register = lambda fn, *a, **k: registered_atexit.append(fn) or fn
        try:
            advisor_host.register(self.ctx)
        finally:
            advisor_host.atexit.register = real_register
            for key, value in saved.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
        self.atexit_callbacks = registered_atexit
        self.runtime = advisor_host.registry().runtimes[str(self.data_dir.resolve())]

    # ---- fixture dispatch ------------------------------------------------
    def cmd(self, raw):
        out = self.ctx.commands['advisor']['handler'](raw)
        record_evidence('command', profile=self.profile, input=raw, output=out)
        return out

    def j(self, raw):
        out = self.cmd(raw)
        return json.loads(out)

    def turn(self, session_id, turn_id, user, answer, *, platform='cli', history=None, success=True, settle=True):
        """Dispatch pre_llm_call / post_llm_call / on_session_end for one main turn."""
        self.transcript.append({'role': 'user', 'content': user})
        self.ctx.hooks['pre_llm_call'](session_id=session_id, turn_id=turn_id, platform=platform)
        self.transcript.append({'role': 'assistant', 'content': answer})
        rows = list(self.transcript) + list(history or [])
        before = copy.deepcopy(rows)
        digest = self.transcript_digest()
        self.ctx.hooks['post_llm_call'](session_id=session_id, turn_id=turn_id, platform=platform,
                                        user_message=user, assistant_response=answer,
                                        conversation_history=rows)
        self.ctx.hooks['on_session_end'](session_id=session_id, turn_id=turn_id, platform=platform,
                                         completed=success, failed=not success, interrupted=False)
        record_evidence('hooks', profile=self.profile, session=session_id, turn=turn_id, success=success)
        # The plugin only ever receives references to host rows; it must not mutate them.
        assert rows == before, 'hook mutated host-supplied conversation rows'
        assert self.transcript_digest() == digest, 'hook mutated the main transcript'
        self.transcript_digests.append(digest)
        if settle:
            self.settle(session_id)

    def transcript_digest(self):
        return hashlib.sha256(json.dumps(self.transcript, sort_keys=True).encode()).hexdigest()

    def settle(self, session_id, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.runtime.service.status(session_id)
            if not status.get('active') and not status.get('pending'):
                return status
            time.sleep(0.02)
        raise AssertionError('review did not settle')

    # ---- convenience -----------------------------------------------------
    def sid(self, session_id='sid-1', platform='cli', turn='t0'):
        self.ctx.hooks['pre_llm_call'](session_id=session_id, turn_id=turn, platform=platform)
        return session_id

    def status(self, session_id):
        return self.j(f'--session {shlex.quote(session_id)} status')

    def notes(self, session_id):
        return self.j(f'--session {shlex.quote(session_id)} notes')

    def configure(self, session_id, revision=None, **settings):
        if revision is None:
            revision = self.status(session_id)['saved_profile_revision']
        body = {'settings': {'route': dict(ROUTE), **settings}, 'expected_revision': revision}
        return self.j(f'--session {shlex.quote(session_id)} configure --json {q(body)}')

    def action(self, session_id, action, params=None):
        raw = f'--session {shlex.quote(session_id)} {action}'
        if params is not None:
            raw += f' --json {q(params)}'
        return self.j(raw)

    def ready(self, session_id='sid-1', **settings):
        self.sid(session_id)
        self.configure(session_id, **settings)
        assert 'error' not in self.action(session_id, 'enable'), 'enable failed'
        return session_id

    # ---- storage readers (read-only; SQLite URI mode=ro) ------------------
    def db(self, sql, args=()):
        path = self.data_dir / 'advisor.sqlite3'
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def data_bytes(self):
        blob = b''
        for path in self.data_dir.rglob('*'):
            if path.is_file():
                blob += path.read_bytes()
        return blob

    def close(self):
        for callback in list(self.ctx.unload):
            callback()
        for callback in self.atexit_callbacks:
            try:
                callback()
            except Exception:
                pass
