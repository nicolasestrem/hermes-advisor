"""End-to-end CLI control + explicit selected-feedback verification.

Real: advisor_host.register/Runtime/EventAdapter, advisor_core.Service, SQLite store.
Controlled fixtures (labelled in harness.py): public plugin context, scripted reviewer, hook/command
dispatch.  No model, no network, no natural-language provider turn, no production state.

Tests for confirmed defects are ordinary tests and must pass once their owned fix lands.
"""
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
from harness import Lab, ROUTE, FAKE_SECRETS, FAKE_SECRET_CORES, findings, q  # noqa: E402

import advisor_core.service as service_module  # noqa: E402

GENERIC = 'Advisor command rejected; inspect explicit session/configuration/status.'
SPECIALS = r'([\\`*_{}\[\]()#+\-.!|<>])'


_HTML_UNSAFE = re.compile(
    r"<\s*/?\s*[a-z][^>]*>"                         # any HTML tag
    r"|&\s*#\d+;?|&\s*[a-z]+\s*;|&#x[0-9a-f]+;?"    # entity-encoded payloads
    r"|\b(?:data|javascript|vbscript|livescript|mocha)\s*:\s*[^\s\"'`<>]*(?:\s|\b|$)",
    re.IGNORECASE,
)


def esc(text):
    """Independent oracle for the documented safe-copy contract (A21): unsafe
    HTML/control-sequence payloads are stripped, then live Markdown markup is
    escaped."""
    return re.sub(SPECIALS, lambda m: '\\' + m.group(1), _HTML_UNSAFE.sub(" ", text))


class Case(unittest.TestCase):
    def setUp(self):
        harness.CURRENT_TEST['id'] = self.id().split('.', 1)[-1]
        self._tmp = tempfile.TemporaryDirectory()
        self.labs = []

    def tearDown(self):
        try:
            for lab in self.labs:
                self.assertEqual(lab.ctx.violations, [], 'plugin touched non-public ctx surface / main-agent path')
                self.assertEqual(lab.ctx.llm.unscripted, [], 'unscripted reviewer call (hidden inference)')
                self.assertEqual(lab.transcript_digest(), lab.transcript_digests[-1] if lab.transcript_digests
                                 else lab.transcript_digest())
        finally:
            for lab in self.labs:
                lab.close()
            self._tmp.cleanup()

    def lab(self, profile='profile-a', **kw):
        lab = Lab(self._tmp.name, profile, **kw)
        self.labs.append(lab)
        return lab

    # helpers
    def assertRejected(self, out, forbidden=()):
        row = json.loads(out)
        self.assertEqual(set(row), {'error', 'detail'}, out)
        self.assertEqual(row['detail'], GENERIC)
        self.assertRegex(row['error'], r'^[A-Za-z]+$')
        for item in tuple(forbidden) + tuple(FAKE_SECRET_CORES):
            self.assertNotIn(item, out)
        return row['error']

    def assertNoSecrets(self, text, where='output'):
        # Report only the leaked fixture token + location; never dump the haystack (may be a binary DB).
        for core in FAKE_SECRET_CORES:
            if core in text:
                self.fail(f'fixture secret {core[:12]}... leaked into {where}')

    def reviewer_calls(self, lab):
        return len(lab.ctx.llm.calls)

    def attempts(self, lab, sid=None):
        if sid:
            return lab.db('SELECT * FROM attempts WHERE session_id=? ORDER BY created_at', (sid,))
        return lab.db('SELECT * FROM attempts')


# ---------------------------------------------------------------- command parsing / quoting
class CommandParsing(Case):
    def test_help_and_metadata_need_no_session(self):
        lab = self.lab()
        self.assertIn('Feedback is returned for user Copy only', lab.cmd('help'))
        self.assertIn('Nothing is sent or inserted automatically', lab.cmd(''))
        meta = lab.j('metadata')
        self.assertEqual(meta['sessions'], [])
        self.assertEqual(meta['profile'], 'profile-a')
        self.assertEqual(self.reviewer_calls(lab), 0)

    def test_registration_surface_is_public_only(self):
        lab = self.lab()
        self.assertEqual(set(lab.ctx.hooks), {'pre_llm_call', 'post_llm_call', 'on_session_end',
                                              'subagent_start', 'subagent_stop'})
        self.assertEqual(set(lab.ctx.commands), {'advisor'})
        self.assertEqual(lab.ctx.violations, [])

    def test_unquoted_json_rejected_single_quoted_accepted(self):
        lab = self.lab()
        sid = lab.sid('sid-q')
        body = {'settings': {'route': ROUTE}, 'expected_revision': 0}
        # shlex removes the unquoted double quotes -> not JSON.
        compact = json.dumps(body, separators=(',', ':'))
        self.assertRejected(lab.cmd(f'--session {sid} configure --json {compact}'))
        # Default separators add spaces -> split into several argv tokens.
        spaced = json.dumps(body)
        self.assertRejected(lab.cmd(f'--session {sid} configure --json {spaced}'))
        st = lab.status(sid)
        self.assertEqual((st['saved_profile_revision'], st['applied_revision'], st['settings']['route']), (0, 0, None))
        out = lab.j(f"--session {sid} configure --json '{compact}'")
        self.assertEqual((out['saved_profile_revision'], out['applied_revision']), (1, 1))

    def test_quoting_forms_round_trip(self):
        lab = self.lab()
        sid = lab.sid('sid-forms')
        cases = []
        route = {'provider': 'p x', 'model': "it's a \"model\" モデル"}
        body = {'settings': {'route': route}, 'expected_revision': 0}
        raw_json = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        cases.append(('shlex.quote', f'--session {sid} configure --json {shlex.quote(raw_json)}', route))
        ascii_json = json.dumps(body, separators=(',', ':')).replace("'", "'\\''")   # \u escapes + POSIX '\'' idiom
        cases.append(('ascii-escaped single quotes', f"--session {sid} configure --json '{ascii_json}'", route))
        for label, raw, expected in cases:
            with self.subTest(label):
                rev = lab.status(sid)['saved_profile_revision']
                raw = raw.replace('"expected_revision":0', f'"expected_revision":{rev}')
                out = lab.j(raw)
                self.assertNotIn('error', out, out)
                self.assertEqual(lab.status(sid)['settings']['route'], expected)
        rev = lab.status(sid)['saved_profile_revision']
        eq_form = json.dumps({'settings': {'route': ROUTE}, 'expected_revision': rev}, separators=(',', ':'))
        out = lab.j(f"--session {sid} configure --json={shlex.quote(eq_form)}")
        self.assertNotIn('error', out)
        self.assertEqual(lab.status(sid)['settings']['route'], ROUTE)
        # dquote with escaped inner quotes also survives shlex
        rev = lab.status(sid)['saved_profile_revision']
        inner = json.dumps({'settings': {'route': ROUTE}, 'expected_revision': rev}, separators=(',', ':')).replace('"', '\\"')
        self.assertNotIn('error', lab.j(f'--session {sid} configure --json "{inner}"'))

    def test_unterminated_quote_and_bad_json_sanitized(self):
        lab = self.lab()
        sid = lab.sid('sid-bad')
        for raw in (f"--session {sid} configure --json '{{\"settings\":",
                    f"--session {sid} configure --json '{{not json}}'",
                    f"--session {sid} configure --json ''",
                    f"--session {sid} configure --json"):
            with self.subTest(raw=raw):
                self.assertRejected(lab.cmd(raw))
        self.assertEqual(lab.status(sid)['saved_profile_revision'], 0)

    def test_json_shape_rejections_change_nothing(self):
        lab = self.lab()
        sid = lab.sid('sid-shape')
        good = {'route': ROUTE}
        bad = {
            'array': [], 'string': 'x', 'number': 1, 'null': None,
            'unknown-top-key': {'settings': good, 'expected_revision': 0, 'extra': 1},
            'missing-revision': {'settings': good},
            'missing-settings': {'expected_revision': 0},
            'unknown-setting': {'settings': {**good, 'tools': True}, 'expected_revision': 0},
            'route-missing-model': {'settings': {'route': {'provider': 'p'}}, 'expected_revision': 0},
            'route-extra-key': {'settings': {'route': {**ROUTE, 'key': 'k'}}, 'expected_revision': 0},
            'no-route': {'settings': {'max_findings': 2}, 'expected_revision': 0},
            'rev-bool': {'settings': good, 'expected_revision': True},
            'rev-string': {'settings': good, 'expected_revision': '0'},
            'rev-float': {'settings': good, 'expected_revision': 0.0},
            'rev-negative': {'settings': good, 'expected_revision': -1},
            'bytes-bool': {'settings': {**good, 'packet_bytes': True}, 'expected_revision': 0},
            'bytes-low': {'settings': {**good, 'packet_bytes': 1023}, 'expected_revision': 0},
            'findings-float': {'settings': {**good, 'max_findings': 2.0}, 'expected_revision': 0},
            'allowance-high': {'settings': {**good, 'session_allowance': 21}, 'expected_revision': 0},
            'padded-model': {'settings': {'route': {'provider': 'p', 'model': ' m'}}, 'expected_revision': 0},
            'control-char-model': {'settings': {'route': {'provider': 'p', 'model': 'm\u0007'}}, 'expected_revision': 0},
            'secret-model': {'settings': {'route': {'provider': 'p', 'model': FAKE_SECRETS['sk']}}, 'expected_revision': 0},
        }
        for label, body in bad.items():
            with self.subTest(label):
                self.assertRejected(lab.cmd(f'--session {sid} configure --json {q(body)}'))
        # JSON literal NaN is accepted by json.loads; it must still be rejected as a revision.
        self.assertRejected(lab.cmd(f"--session {sid} configure --json '{{\"settings\":{json.dumps(good)},\"expected_revision\":NaN}}'"))
        st = lab.status(sid)
        self.assertEqual((st['saved_profile_revision'], st['applied_revision'], st['applied_generation']), (0, 0, 0))
        self.assertEqual(st['settings']['route'], None)

    def test_exact_session_id_only(self):
        lab = self.lab()
        lab.sid('Sid-Exact-1')
        lab.sid('sid with space')
        variants = ['sid-exact-1', 'SID-EXACT-1', 'Sid-Exact-1 ', ' Sid-Exact-1', 'Sid-Exact', 'Sid-Exact-1\u200b',
                    '\u0405id-Exact-1', '', 'Sid-Exact-1;enable', 'Sid-Exact-1/../x', 'sid', 'ghost-never-discovered']
        for variant in variants:
            with self.subTest(variant=variant):
                for action in ('status', 'notes', 'enable', 'review_now', 'disable'):
                    self.assertRejected(lab.cmd(f'--session {shlex.quote(variant)} {action}'))
        self.assertRejected(lab.cmd('status'))   # no --session at all
        self.assertRejected(lab.cmd('--session sid with space status'))   # unquoted space -> extra token
        # exact IDs work, including one needing quoting
        self.assertEqual(lab.status('Sid-Exact-1')['session_id'], 'Sid-Exact-1')
        self.assertEqual(lab.j("--session 'sid with space' status")['session_id'], 'sid with space')
        ids = [s['session_id'] for s in lab.j('metadata')['sessions']]
        self.assertEqual(sorted(ids), ['Sid-Exact-1', 'sid with space'])   # nothing registered by probing
        self.assertEqual(self.reviewer_calls(lab), 0)

    def test_observation_option_prefix_never_selects_another_session(self):
        # OBSERVATION: argparse allow_abbrev lets `--sess` act as `--session`. Not a SID match issue; recorded only.
        lab = self.lab()
        lab.sid('sid-a')
        lab.sid('sid-b')
        abbreviated = lab.cmd('--sess sid-a status')
        exact = lab.cmd('--session sid-a status')
        row = json.loads(abbreviated)
        self.assertTrue('error' in row or row['session_id'] == 'sid-a')
        if 'error' not in row:
            self.assertEqual(abbreviated, exact)

    def test_unknown_actions_and_extra_tokens_rejected(self):
        lab = self.lab()
        sid = lab.ready('sid-act')
        for action in ('close', 'discover', 'capture', '__init__', 'status;enable', 'ENABLE', 'Enable', 'service',
                       'metadata_x', 'reviewnow', 'rate_', 'feedback2', '-h', '--help'):
            with self.subTest(action=action):
                self.assertRejected(lab.cmd(f'--session {sid} {shlex.quote(action)}'))
        self.assertRejected(lab.cmd(f'--session {sid} status extra'))
        self.assertRejected(lab.cmd(f"--session {sid} enable --json '{{\"x\":1}}'"))
        self.assertRejected(lab.cmd(f"--session {sid} review_now --json '{{\"force\":true}}'"))
        self.assertEqual(lab.status(sid)['state'], 'enabled')   # service still open and unchanged
        self.assertEqual(self.reviewer_calls(lab), 0)

    def test_error_output_never_echoes_input_or_secret(self):
        lab = self.lab()
        sid = lab.ready('sid-echo')
        sk, bearer, kv, url = (FAKE_SECRETS[k] for k in ('sk', 'bearer', 'kv', 'url'))
        attempts = [
            f'--session {shlex.quote(sk)} status',
            f'--session {shlex.quote(bearer)} notes',
            f'--session {sid} {shlex.quote(kv)}',
            f'--session {sid} configure --json {shlex.quote("{" + sk)}',
            f'--session {sid} configure --json {q({"settings": {"route": {"provider": "p", "model": sk}}, "expected_revision": 1})}',
            f'--session {sid} configure --json {q({"settings": {"route": ROUTE, "unknown_" + FAKE_SECRETS["ghp"]: 1}, "expected_revision": 1})}',
            f'--session {sid} feedback --json {q({"note_ids": [url]})}',
            f'--session {sid} rate --json {q({"attempt_id": sk, "rating": "useful", "reason": kv})}',
            f'--session {sid} reset_allowance --json {q({"confirm": bearer})}',
            f'--session {sid} {shlex.quote(url)}',
            f"--session {sid} status --json '{sk}'",
        ]
        for raw in attempts:
            with self.subTest(raw=harness._scrub(raw)):
                out = lab.cmd(raw)
                if '"error"' in out and set(json.loads(out)) == {'error', 'detail'}:
                    self.assertRejected(out)
                else:   # status with ignored --json may legitimately succeed; it must still carry no input echo
                    self.assertNoSecrets(out)
        self.assertEqual(lab.status(sid)['saved_profile_revision'], 1)
        self.assertNoSecrets(lab.data_bytes().decode('latin-1'), 'profile-local storage bytes')

    def test_reviewer_exception_text_is_not_persisted_or_echoed(self):
        lab = self.lab()
        sid = lab.ready('sid-exc')
        lab.ctx.llm.push(RuntimeError(f"upstream refused {FAKE_SECRETS['sk']} {FAKE_SECRETS['bearer']}"))
        lab.turn(sid, 't1', 'question', 'answer')
        st = lab.cmd(f'--session {sid} status')
        nt = lab.cmd(f'--session {sid} notes')
        self.assertNoSecrets(st + nt + lab.data_bytes().decode('latin-1'))
        attempt = lab.status(sid)['latest_attempt']
        self.assertEqual((attempt['status'], attempt['error_code'], attempt['error']),
                         ('failed', 'reviewer_error', 'reviewer request failed (RuntimeError)'))
        # quota/auth wording classifies and pauses, still without echoing the body
        lab2 = self.lab('profile-q')
        sid2 = lab2.ready('sid-quota')
        lab2.ctx.llm.push(RuntimeError(f"429 rate limit for {FAKE_SECRETS['sk']}"))
        lab2.turn(sid2, 't1', 'q', 'a')
        st2 = lab2.status(sid2)
        self.assertEqual((st2['state'], st2['pause_reason'], st2['latest_attempt']['error_code']),
                         ('paused', 'quota_or_auth', 'quota_or_auth'))
        self.assertNoSecrets(json.dumps(st2) + lab2.data_bytes().decode('latin-1'))


# ---------------------------------------------------------------- lifecycle
class Lifecycle(Case):
    def test_enable_without_model_fails_closed(self):
        lab = self.lab()
        sid = lab.sid('sid-nm')
        self.assertEqual(lab.status(sid)['configuration_error'], 'no_model')
        self.assertEqual(self.assertRejected(lab.cmd(f'--session {sid} enable')), 'ServiceError')
        st = lab.status(sid)
        self.assertEqual((st['state'], st['applied_generation']), ('disabled', 0))
        lab.turn(sid, 't1', 'q', 'a')                  # disabled: nothing captured
        self.assertEqual(self.reviewer_calls(lab), 0)
        self.assertEqual(self.attempts(lab), [])
        rn = lab.action(sid, 'review_now')
        self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'disabled', 0))
        lab.configure(sid)
        self.assertNotIn('error', lab.action(sid, 'enable'))
        self.assertEqual(lab.status(sid)['state'], 'enabled')

    def test_service_level_enable_without_model_is_no_model_without_host_validator(self):
        # The Runtime's route validator would also reject a null route (defence in depth), so pin the
        # Service's own fail-closed rule with the real Service and NO validator.
        from advisor_core import Service
        from advisor_core.service import ServiceError

        async def reviewer(*args, **kwargs):          # never reachable; any call is a defect
            raise AssertionError('reviewer must not run')

        svc = Service(str(Path(self._tmp.name) / 'svc-only'), reviewer)
        try:
            svc.discover('svc-1', 'cli')
            with self.assertRaisesRegex(ServiceError, '^no_model'):
                svc.enable('svc-1')
            status = svc.status('svc-1')
            self.assertEqual((status['state'], status['configuration_error']), ('disabled', 'no_model'))
        finally:
            self.assertTrue(svc.close())

    def test_service_level_guards_independent_of_runtime_param_validation(self):
        # Runtime.control validates param shapes first; pin the real Service's own guards too.
        from advisor_core import Service

        async def reviewer(*args, **kwargs):
            raise AssertionError('reviewer must not run')

        svc = Service(str(Path(self._tmp.name) / 'svc-guards'), reviewer)
        try:
            svc.discover('svc-2', 'cli')
            for confirm in (False, None, 'true', 1, 'yes'):
                with self.subTest(confirm=confirm), self.assertRaises(ValueError):
                    svc.reset_allowance('svc-2', confirm)
            self.assertEqual(svc.status('svc-2')['pause_reason'], None)     # nothing was reset
            for ids in (['a', 'a'], [], 'abc', [1], ['x'] * 101):
                with self.subTest(note_ids=ids), self.assertRaises(ValueError):
                    svc.feedback('svc-2', ids)
            with self.assertRaises(ValueError):
                svc.feedback('svc-2', ['not-a-note'])                        # unknown / foreign id
            self.assertEqual(svc.store.read(lambda c: c.execute('SELECT COUNT(*) FROM feedback_actions').fetchone()[0]), 0)
        finally:
            self.assertTrue(svc.close())

    def test_missing_host_grants_reject_configure_and_enable(self):
        lab = self.lab(capabilities=())
        sid = lab.sid('sid-grants')
        body = {'settings': {'route': ROUTE}, 'expected_revision': 0}
        self.assertRejected(lab.cmd(f'--session {sid} configure --json {q(body)}'))
        self.assertRejected(lab.cmd(f'--session {sid} enable'))
        st = lab.status(sid)
        self.assertEqual((st['saved_profile_revision'], st['state']), (0, 'disabled'))

    def test_unsupported_surface(self):
        lab = self.lab()
        lab.ctx.hooks['pre_llm_call'](session_id='tg-hook', turn_id='t', platform='telegram')
        self.assertEqual(lab.j('metadata')['sessions'], [])     # unsupported surface never becomes CLI-addressable
        self.assertRejected(lab.cmd('--session tg-hook status'))
        # FIXTURE (private-state): force a service-level unsupported session into the runtime table to prove
        # the CLI control path itself fails closed.  Uses runtime._sessions, so not a public-hook proof.
        lab.runtime.service.discover('tg-fixture', 'telegram')
        lab.runtime._sessions['tg-fixture'] = {'session_id': 'tg-fixture', 'surface': 'telegram'}
        st = lab.status('tg-fixture')
        self.assertEqual((st['surface_supported'], st['state'], st['pause_reason']), (False, 'disabled', 'unsupported_surface'))
        self.assertNotIn('error', lab.configure('tg-fixture'))   # configuring is allowed...
        self.assertEqual(self.assertRejected(lab.cmd('--session tg-fixture enable')), 'ServiceError')   # ...enabling is not
        rn = lab.action('tg-fixture', 'review_now')
        self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'unsupported_surface', 0))
        self.assertEqual(lab.status('tg-fixture')['state'], 'disabled')
        self.assertEqual(self.reviewer_calls(lab), 0)

    def test_stale_revisions_and_independent_applied_state(self):
        lab = self.lab()
        s1, s2 = lab.sid('sid-1'), lab.sid('sid-2')
        self.assertEqual(lab.configure(s1, 0)['saved_profile_revision'], 1)
        st2 = lab.status(s2)
        self.assertEqual((st2['saved_profile_revision'], st2['applied_revision'], st2['configuration_error']), (1, 0, 'no_model'))
        stale = {'settings': {'route': ROUTE, 'max_findings': 1}, 'expected_revision': 0}
        self.assertEqual(self.assertRejected(lab.cmd(f'--session {s2} configure --json {q(stale)}')), 'ConfigurationConflict')
        self.assertEqual(lab.status(s2)['applied_revision'], 0)
        self.assertEqual(lab.status(s2)['saved_settings']['max_findings'], 4)   # stale write changed nothing
        self.assertEqual(lab.configure(s2, 1, max_findings=1)['saved_profile_revision'], 2)
        st1 = lab.status(s1)
        self.assertEqual((st1['saved_profile_revision'], st1['applied_revision']), (2, 1))   # s1 not silently re-applied
        self.assertEqual(self.assertRejected(lab.cmd(f'--session {s1} configure --json {q({"settings": {"route": ROUTE}, "expected_revision": 1})}')),
                         'ConfigurationConflict')
        self.assertEqual(lab.configure(s1, 2)['saved_profile_revision'], 3)

    def test_configure_between_post_and_end_discards_snapshot(self):
        lab = self.lab()
        sid = lab.ready('sid-race')
        h = lab.ctx.hooks
        h['pre_llm_call'](session_id=sid, turn_id='t1', platform='cli')
        h['post_llm_call'](session_id=sid, turn_id='t1', platform='cli', user_message='q', assistant_response='a',
                           conversation_history=[{'role': 'user', 'content': 'q'}])
        self.assertNotIn('error', lab.configure(sid, max_findings=2))
        h['on_session_end'](session_id=sid, turn_id='t1', platform='cli', completed=True, failed=False, interrupted=False)
        self.assertEqual(self.reviewer_calls(lab), 0)
        self.assertEqual(self.attempts(lab), [])
        self.assertEqual(lab.runtime.adapter.metrics['discarded'], 1)

    def test_configure_invalidates_cached_snapshot_for_review_now(self):
        lab = self.lab()
        sid = lab.ready('sid-inv')
        lab.ctx.llm.push(RuntimeError('transient'))
        lab.turn(sid, 't1', 'q', 'a')
        self.assertTrue(lab.status(sid)['review_now_available'])
        self.assertNotIn('error', lab.configure(sid, deadline_seconds=30))
        st = lab.status(sid)
        self.assertFalse(st['review_now_available'])
        rn = lab.action(sid, 'review_now')
        self.assertFalse(rn['accepted'])
        self.assertEqual((rn['requests'], self.reviewer_calls(lab), st['invocations_used']), (0, 1, 1))

    def test_default_inherited_settings_are_profile_local_and_disabled(self):
        a = self.lab('profile-a')
        before = a.sid('sid-before')
        first = a.sid('sid-first')
        a.configure(first, 0, packet_bytes=2048, session_allowance=5, max_findings=2)
        after = a.sid('sid-after')
        st = a.status(after)
        self.assertEqual(st['settings'], a.status(first)['settings'])
        self.assertEqual((st['settings']['packet_bytes'], st['settings']['session_allowance'], st['settings']['route']),
                         (2048, 5, ROUTE))
        self.assertEqual((st['state'], st['applied_revision'], st['saved_profile_revision']), ('disabled', 1, 1))
        self.assertEqual(a.status(before)['applied_revision'], 0)      # pre-existing session not retro-applied
        self.assertIsNone(a.status(before)['settings']['route'])
        a.turn(after, 't1', 'q', 'a')                                  # inherited but NOT enabled -> no review
        self.assertEqual(self.reviewer_calls(a), 0)
        b = self.lab('profile-b')
        other = b.sid('sid-after')                                    # same SID, different profile
        stb = b.status(other)
        self.assertEqual((stb['saved_profile_revision'], stb['settings']['route'], stb['settings']['packet_bytes']), (0, None, 32768))
        self.assertNotEqual(a.data_dir, b.data_dir)

    def test_disable_stops_admission_and_reenable_resumes(self):
        lab = self.lab()
        sid = lab.ready('sid-dis')
        self.assertEqual(lab.action(sid, 'disable')['state'], 'disabled')
        lab.turn(sid, 't1', 'q', 'a')
        self.assertEqual((self.reviewer_calls(lab), self.attempts(lab)), (0, []))
        lab.ctx.llm.push('no_findings')
        self.assertNotIn('error', lab.action(sid, 'enable'))
        lab.turn(sid, 't2', 'q2', 'a2')
        self.assertEqual(self.reviewer_calls(lab), 1)


# ---------------------------------------------------------------- review_now
class ReviewNow(Case):
    def test_without_capture_is_refused_without_invocation(self):
        lab = self.lab()
        sid = lab.ready('sid-nc')
        rn = lab.action(sid, 'review_now')
        # enable() deliberately clears any retained snapshot and demands a fresh completed answer.
        self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'new_answer_required', 0))
        st = lab.status(sid)
        self.assertEqual((st['invocations_used'], st['review_now_unavailable_reason'], self.reviewer_calls(lab)),
                         (0, 'new_answer_required', 0))

    def test_completed_packet_is_not_rereviewed_and_fixture_label_is_honest(self):
        lab = self.lab()
        sid = lab.ready('sid-done')
        lab.ctx.llm.push('no_findings')            # SCRIPTED fixture output, not provider output
        lab.turn(sid, 't1', 'q', 'a')
        rn = lab.action(sid, 'review_now')
        self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'already_reviewed', 0))
        att = lab.status(sid)['latest_attempt']
        self.assertEqual(att['outcome'], {'outcome': 'no_findings', 'findings': []})
        self.assertEqual(att['served_route'], ROUTE)
        self.assertNotIn(att['served_route']['provider'], {'nous', 'openai', 'anthropic', 'openrouter'})
        self.assertEqual((att['usage'], att['usage_known']), (None, False))   # unknown stays unknown
        self.assertEqual((lab.status(sid)['invocations_used'], self.reviewer_calls(lab)), (1, 1))

    def test_retry_after_failure_consumes_one_allowance_on_same_packet(self):
        lab = self.lab()
        sid = lab.ready('sid-retry', session_allowance=3)
        lab.ctx.llm.push(RuntimeError('transient failure'), 'no_findings')
        lab.turn(sid, 't1', 'the question', 'the answer')
        st = lab.status(sid)
        self.assertEqual((st['latest_attempt']['status'], st['invocations_used'], st['review_now_available']),
                         ('failed', 1, True))
        digest = lab.transcript_digest()
        rn = lab.action(sid, 'review_now')
        self.assertTrue(rn['accepted'], rn)
        self.assertNotEqual(rn['attempt_id'], st['latest_attempt']['attempt_id'])
        lab.settle(sid)
        st = lab.status(sid)
        self.assertEqual((st['latest_attempt']['status'], st['invocations_used'], st['invocations_remaining']),
                         ('completed', 2, 1))
        first, second = (c['input'][0]['text'] for c in lab.ctx.llm.calls)
        self.assertEqual(first, second)                         # same retained packet, no re-capture
        again = lab.action(sid, 'review_now')
        self.assertEqual((again['accepted'], again['reason']), (False, 'already_reviewed'))
        self.assertEqual((lab.status(sid)['invocations_used'], self.reviewer_calls(lab)), (2, 2))
        self.assertEqual(lab.transcript_digest(), digest)

    def test_allowance_exhaustion_and_confirmed_reset(self):
        lab = self.lab()
        sid = lab.ready('sid-allow', session_allowance=2)
        lab.ctx.llm.push(RuntimeError('one'), RuntimeError('two'))
        lab.turn(sid, 't1', 'q', 'a')
        self.assertTrue(lab.action(sid, 'review_now')['accepted'])
        lab.settle(sid)
        st = lab.status(sid)
        self.assertEqual((st['state'], st['pause_reason'], st['invocations_used'], st['invocations_remaining']),
                         ('paused', 'allowance_exhausted', 2, 0))
        rn = lab.action(sid, 'review_now')
        self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'allowance_exhausted', 0))
        lab.turn(sid, 't2', 'q2', 'a2')                       # paused: no capture, no reviewer call
        self.assertEqual(self.reviewer_calls(lab), 2)
        for params in (None, {}, {'confirm': False}, {'confirm': 'true'}, {'confirm': 1}, {'confirm': True, 'x': 1}):
            with self.subTest(params=params):
                raw = f'--session {sid} reset_allowance' + ('' if params is None else f' --json {q(params)}')
                self.assertRejected(lab.cmd(raw))
        self.assertEqual(lab.status(sid)['invocations_used'], 2)
        reset = lab.action(sid, 'reset_allowance', {'confirm': True})
        self.assertEqual((reset['state'], reset['pause_reason'], reset['invocations_used']), ('paused', 'allowance_reset', 0))
        rn = lab.action(sid, 'review_now')
        self.assertEqual((rn['accepted'], rn['reason']), (False, 'allowance_reset'))
        self.assertNotIn('error', lab.action(sid, 'enable'))
        rn = lab.action(sid, 'review_now')                     # enable clears the retained snapshot
        self.assertEqual((rn['accepted'], rn['reason'], self.reviewer_calls(lab)), (False, 'new_answer_required', 2))
        lab.ctx.llm.push('no_findings')
        lab.turn(sid, 't3', 'q3', 'a3')
        st = lab.status(sid)
        self.assertEqual((st['invocations_used'], st['latest_attempt']['status'], self.reviewer_calls(lab)), (1, 'completed', 3))

    def test_snapshot_cache_expiry_is_reported_without_new_attempt_or_extension(self):
        lab = self.lab()
        sid = lab.ready('sid-ttl', session_allowance=5)
        ttl = service_module.Service.SNAPSHOT_TTL_SECONDS

        class Clock:
            offset = 0.0
            def __getattr__(self, name):
                return getattr(__import__('time'), name)
            def monotonic(self):
                return __import__('time').monotonic() + self.offset

        clock = Clock()
        with mock.patch.object(service_module, 'time', clock):
            lab.ctx.llm.push(RuntimeError('transient'), 'no_findings')
            lab.turn(sid, 't1', 'q', 'a')
            st = lab.status(sid)
            self.assertTrue(st['review_now_available'])
            self.assertLessEqual(st['review_now_expires_in_seconds'], ttl)
            self.assertGreater(st['review_now_expires_in_seconds'], ttl - 30)
            clock.offset = ttl - 30
            st = lab.status(sid)
            self.assertTrue(st['review_now_available'])
            self.assertLessEqual(st['review_now_expires_in_seconds'], 30)
            clock.offset = ttl + 1
            for _ in range(2):                                   # repeated reads must not extend/recreate
                st = lab.status(sid)
                self.assertEqual((st['review_now_available'], st['review_now_unavailable_reason'],
                                  st['review_now_expires_in_seconds']), (False, 'expired', None))
            rn = lab.action(sid, 'review_now')
            self.assertEqual((rn['accepted'], rn['reason'], rn['requests']), (False, 'expired', 0))
            self.assertEqual((len(self.attempts(lab)), st['invocations_used'], self.reviewer_calls(lab)), (1, 1, 1))
            lab.turn(sid, 't2', 'q2', 'a2')                      # a fresh capture restores availability
            self.assertEqual(self.reviewer_calls(lab), 2)
            self.assertTrue(lab.status(sid)['review_now_available'])


# ---------------------------------------------------------------- ratings
class Ratings(Case):
    def completed(self, lab, sid='sid-rate', **kw):
        lab.ready(sid, **kw)
        lab.ctx.llm.push(findings({'explanation': 'e1', 'next_step': 'n1'}))
        lab.turn(sid, 't1', 'q', 'a')
        return lab.status(sid)['latest_attempt']['attempt_id']

    def test_reason_redaction_bounds_and_overwrite(self):
        lab = self.lab()
        att = self.completed(lab)
        reason = ' '.join(FAKE_SECRETS.values()) + ' ctrl:\x00\x1b[31m\x07 end'
        out = lab.action('sid-rate', 'rate', {'attempt_id': att, 'rating': 'not_useful', 'reason': reason})
        self.assertEqual((out['saved'], out['rating']), (True, 'not_useful'))
        self.assertIn('[REDACTED]', out['reason'])
        self.assertNoSecrets(json.dumps(out))
        self.assertFalse(re.search(r'[\x00-\x09\x0b-\x1f\x7f]', out['reason']))
        stored = lab.notes('sid-rate')[0]['rating']
        self.assertEqual(stored, {'rating': 'not_useful', 'reason': out['reason']})
        self.assertEqual(lab.db('SELECT reason FROM ratings')[0]['reason'], out['reason'])
        self.assertNoSecrets(lab.data_bytes().decode('latin-1'), 'profile-local storage bytes')
        # overwrite, clear, boundary
        self.assertEqual(lab.action('sid-rate', 'rate', {'attempt_id': att, 'rating': 'useful', 'reason': None})['reason'], None)
        self.assertEqual(len(lab.db('SELECT * FROM ratings')), 1)
        ok500 = 'x' * 500
        self.assertEqual(lab.action('sid-rate', 'rate', {'attempt_id': att, 'rating': 'not_rated', 'reason': ok500})['reason'], ok500)
        before = lab.db('SELECT * FROM ratings')
        for label, params in {
            'reason-501': {'attempt_id': att, 'rating': 'useful', 'reason': 'x' * 501},
            'reason-int': {'attempt_id': att, 'rating': 'useful', 'reason': 5},
            'bad-rating': {'attempt_id': att, 'rating': 'great', 'reason': None},
            'rating-bool': {'attempt_id': att, 'rating': True, 'reason': None},
            'unknown-attempt': {'attempt_id': 'nope', 'rating': 'useful', 'reason': None},
            'secret-attempt': {'attempt_id': FAKE_SECRETS['sk'], 'rating': 'useful', 'reason': None},
            'extra-key': {'attempt_id': att, 'rating': 'useful', 'reason': None, 'session_id': 'other'},
        }.items():
            with self.subTest(label):
                self.assertRejected(lab.cmd(f'--session sid-rate rate --json {q(params)}'))
        self.assertEqual(lab.db('SELECT * FROM ratings'), before)

    def test_reason_with_secret_straddling_clip_boundary_is_not_partially_stored(self):
        lab = self.lab()
        att = self.completed(lab)
        reason = 'a' * 460 + ' ' + FAKE_SECRETS['sk']   # <=500 input chars, secret near the tail
        out = lab.action('sid-rate', 'rate', {'attempt_id': att, 'rating': 'useful', 'reason': reason})
        self.assertNoSecrets(json.dumps(out), 'rate output')
        self.assertNoSecrets(json.dumps(lab.db('SELECT reason FROM ratings')), 'ratings table')
        self.assertNotIn('FIXTURE', out['reason'])

    def test_redaction_catches_embedded_credentials_with_word_character_prefix(self):
        # Core sanitize_text anchors with \b, so `xsk-...` / `_ghp_...` survive redaction.  Low severity.
        lab = self.lab()
        att = self.completed(lab)
        out = lab.action('sid-rate', 'rate', {'attempt_id': att, 'rating': 'useful',
                                              'reason': 'pastedxsk-fixturecredential00000000 prefix_ghp_fixturecredential0000000000000000'})
        self.assertNoSecrets(json.dumps(out), 'rate output')
        self.assertIn('[REDACTED]', out['reason'])

    def test_failed_and_foreign_attempts_cannot_be_rated(self):
        lab = self.lab()
        lab.ready('sid-f')
        lab.ctx.llm.push(RuntimeError('x'))
        lab.turn('sid-f', 't1', 'q', 'a')
        failed = lab.status('sid-f')['latest_attempt']
        self.assertEqual(failed['status'], 'failed')
        self.assertRejected(lab.cmd(f"--session sid-f rate --json {q({'attempt_id': failed['attempt_id'], 'rating': 'useful', 'reason': None})}"))
        other = self.completed(lab, 'sid-other')
        self.assertRejected(lab.cmd(f"--session sid-f rate --json {q({'attempt_id': other, 'rating': 'useful', 'reason': None})}"))
        self.assertEqual(lab.db('SELECT * FROM ratings'), [])

    def test_core_rating_reason_defaults_none_and_persists(self):
        # The registered command handler currently rejects the omitted key before reaching Core;
        # preserve that separate API dependency in the red capture. Exercise the owned Service
        # contract directly here, backed by the same runtime's SQLite store.
        lab = self.lab()
        att = self.completed(lab)
        out = lab.runtime.service.rate('sid-rate', att, 'useful')
        self.assertEqual((out.get('saved'), out.get('rating'), out.get('reason')), (True, 'useful', None), out)
        self.assertEqual(lab.db('SELECT rating,reason FROM ratings'), [{'rating': 'useful', 'reason': None}])
        self.assertEqual(lab.notes('sid-rate')[0]['rating'], {'rating': 'useful', 'reason': None})


# ---------------------------------------------------------------- feedback
NASTY_EXPLANATION = ('Use `rm -rf` **now** [click](http://x.invalid) <script>alert(1)</script> # head | pipe '
                     '_under_ {brace} - dash 1. list + plus !bang \\back ' + FAKE_SECRETS['sk'])
NASTY_NEXT = 'Run `pytest -k *` then (verify) [x] ' + FAKE_SECRETS['kv']
NASTY_TURN = 'turn_[7]*(x)`<b>'
TOOL_HISTORY = [
    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'function': {'name': 'read', 'arguments': '{"p":1}'}}]},
    {'role': 'tool', 'tool_call_id': 'c1', 'content': 'file text'},
]


class Feedback(Case):
    def seed(self, lab, sid='sid-fb', turn=NASTY_TURN, items=None, **settings):
        lab.ready(sid, **settings)
        items = items or [
            {'explanation': NASTY_EXPLANATION, 'next_step': NASTY_NEXT, 'severity': 'blocker',
             'evidence_refs': ['tool-0001-result', 'answer']},
            {'explanation': 'ctrl \x00\x07 chars\tand tab', 'next_step': 'plain step', 'severity': 'nit',
             'evidence_refs': ['request', 'answer']},
            {'explanation': 'unselected finding', 'next_step': 'unselected step'},
        ]

        def reviewer(packet, kwargs):
            refs = [e['ref'] for e in packet['evidence']]
            assert 'tool-0001-result' in refs and 'tool-0001-call' in refs, refs
            enum = kwargs['json_schema']['properties']['findings']['items']['properties']['evidence_refs']['items']['enum']
            assert enum == refs, (enum, refs)
            return findings(*items)

        lab.ctx.llm.push(reviewer)
        lab.turn(sid, turn, 'the question', 'the answer', history=TOOL_HISTORY)
        notes = lab.notes(sid)
        self.assertEqual(notes[0]['status'], 'completed', notes)
        return [f['note_id'] for f in notes[0]['findings']]

    def actions(self, lab):
        return lab.db('SELECT * FROM feedback_actions ORDER BY created_at')

    def test_selected_notes_are_escaped_ordered_and_provided_output_only(self):
        lab = self.lab()
        ids = self.seed(lab)
        calls_before, digest = self.reviewer_calls(lab), lab.transcript_digest()
        raw = f"--session sid-fb feedback --json {q({'note_ids': [ids[1], ids[0]]})}"
        out = lab.cmd(raw)
        self.assertNotIn('\n', out)                          # one JSON line; text newlines are JSON-escaped
        row = json.loads(out)
        self.assertEqual(set(row), {'action_id', 'notes', 'text'})
        self.assertEqual([n['note_id'] for n in row['notes']], [ids[1], ids[0]])      # exact selection and order
        self.assertNotIn(ids[2], out)
        self.assertNotIn('unselected', out)
        n1, n0 = row['notes']
        # answerRef: escaped host turn id; structured evidence refs and next_step are carried
        self.assertEqual(n0['answer_ref'], esc(NASTY_TURN))
        self.assertEqual(n0['evidence_refs'], ['tool-0001-result', 'answer'])
        self.assertEqual(n1['evidence_refs'], ['request', 'answer'])
        self.assertEqual((n0['severity'], n1['severity']), ('blocker', 'nit'))
        self.assertTrue(n0['next_step'] and n1['next_step'] == 'plain step')
        # credentials redacted everywhere; control chars neutralised
        self.assertNoSecrets(out)
        self.assertIn('[REDACTED]', n0['explanation'])
        self.assertFalse(re.search(r'[\x00-\x08\x0b-\x1f\x7f]', row['text']))
        self.assertNotIn('\x00', n1['explanation'])
        # independent escape oracle on every dynamic line of the Copy text
        text = row['text']
        expl0 = n0['explanation']
        self.assertIn(f"[blocker] {esc(expl0)}", text)
        self.assertIn(f"Reviewed answer: {esc(NASTY_TURN)}", text)
        self.assertIn(f"Evidence refs: {esc('tool-0001-result')}, answer", text)
        self.assertIn(f"Suggested next step: {esc(n0['next_step'])}", text)
        # No dynamic markup character survives unescaped: drop every backslash pair, then only the
        # template's own '[severity]' brackets may remain on the explanation line.
        for dynamic in (n0['explanation'], n0['next_step'], n0['answer_ref']):
            plain = re.sub(r'\\.', '', esc(dynamic))
            self.assertIsNone(re.search(SPECIALS, plain), plain)
        for live_markup in ('<script>', '[click](http', '**now**', '`rm -rf`', '<b>', '_under_'):
            self.assertNotIn(live_markup, text)
        # nothing was sent anywhere: no extra reviewer call, transcript untouched, only a local log row
        self.assertEqual((self.reviewer_calls(lab), lab.transcript_digest()), (calls_before, digest))
        logged = self.actions(lab)
        self.assertEqual(len(logged), 1)
        self.assertEqual(json.loads(logged[0]['note_ids_json']), [ids[1], ids[0]])
        self.assertEqual(json.loads(logged[0]['attempt_ids_json']), [lab.notes('sid-fb')[0]['attempt_id']])
        self.assertNotIn('explanation', ' '.join(logged[0]))       # log carries ids only, no copied text
        self.assertNoSecrets(json.dumps(lab.db('SELECT * FROM findings')), 'findings table')
        self.assertNoSecrets(json.dumps(lab.db('SELECT * FROM feedback_actions')), 'feedback_actions table')
        self.assertNoSecrets(json.dumps(lab.notes('sid-fb')[0]['findings']), 'notes.findings')

    def test_reviewer_credentials_are_redacted_from_durable_outcome_and_public_old_rows(self):
        # attempts.outcome stores the raw validated reviewer JSON; `status.latest_attempt.outcome` and
        # `notes[].outcome` return it, so credentials the reviewer echoes bypass the redaction applied to
        # findings rows / feedback text.  Medium severity (at-rest + CLI output).
        lab = self.lab()
        self.seed(lab, items=[{'explanation': 'see ' + FAKE_SECRETS['sk'], 'next_step': 'use ' + FAKE_SECRETS['bearer']}])
        self.assertNoSecrets(lab.cmd('--session sid-fb status'), 'status output')
        self.assertNoSecrets(lab.cmd('--session sid-fb notes'), 'notes output')
        self.assertNoSecrets(json.dumps(lab.db('SELECT outcome FROM attempts')), 'attempts.outcome at rest')
        # Simulate a legacy row that predates the durable redaction guarantee; all outward paths still sanitize it.
        unsafe = {'outcome': 'findings', 'findings': [{'severity': 'concern',
                  'explanation': 'old ' + FAKE_SECRETS['sk'], 'evidence_refs': ['answer'],
                  'next_step': 'legacy ' + FAKE_SECRETS['bearer']}]}
        lab.runtime.service.store.transaction(lambda conn: conn.execute(
            'UPDATE attempts SET outcome=? WHERE session_id=?', (json.dumps(unsafe), 'sid-fb')))
        self.assertNoSecrets(lab.cmd('--session sid-fb status'), 'legacy status output')
        self.assertNoSecrets(lab.cmd('--session sid-fb notes'), 'legacy notes output')
        self.assertNoSecrets(json.dumps(lab.notes('sid-fb')), 'legacy core notes output')
        self.assertIn(FAKE_SECRETS['sk'], lab.db('SELECT outcome FROM attempts')[0]['outcome'])  # legacy bytes stay immutable
        self.assertNotIn('FIXTURE', lab.cmd('--session sid-fb status'))
        self.assertNotIn('FIXTURE', lab.cmd('--session sid-fb notes'))

    def test_newline_in_reviewer_text_cannot_forge_labelled_copy_lines(self):
        # _markdown_escape keeps newlines, so reviewer-controlled text can start a line with a label the
        # Copy template itself emits.  Structured `notes` is unaffected; only the Copy `text` is.
        lab = self.lab()
        items = [{'explanation': 'ok\nReviewed answer: FORGED-ANSWER', 'next_step': 'x\nSuggested next step: FORGED-STEP'}]
        ids = self.seed(lab, items=items)
        row = lab.action('sid-fb', 'feedback', {'note_ids': ids[:1]})
        lines = row['text'].split('\n')
        self.assertFalse([l for l in lines if l.startswith('Reviewed answer: FORGED') or l.startswith('Suggested next step: FORGED')])

    def test_rejections_log_nothing_and_leak_nothing(self):
        lab = self.lab()
        ids = self.seed(lab)
        att = lab.notes('sid-fb')[0]['attempt_id']
        cases = {
            'missing-note-ids': None, 'empty': {'note_ids': []}, 'too-many': {'note_ids': [f'x{i}' for i in range(101)]},
            'duplicates': {'note_ids': [ids[0], ids[0]]}, 'non-string': {'note_ids': [1]}, 'null-item': {'note_ids': [None]},
            'string-instead-of-list': {'note_ids': ids[0]}, 'object': {'note_ids': {'a': 1}},
            'unknown': {'note_ids': ['nope']}, 'valid-plus-unknown': {'note_ids': [ids[0], 'nope']},
            'attempt-id-as-note-id': {'note_ids': [att]}, 'extra-key': {'note_ids': [ids[0]], 'session_id': 'other'},
            'case-changed-id': {'note_ids': [ids[0].upper()]}, 'padded-id': {'note_ids': [ids[0] + ' ']},
            'secret-id': {'note_ids': [FAKE_SECRETS['sk']]},
        }
        for label, params in cases.items():
            with self.subTest(label):
                raw = '--session sid-fb feedback' + ('' if params is None else f' --json {q(params)}')
                out = lab.cmd(raw)
                self.assertRejected(out, forbidden=('explanation', 'next_step', 'unselected'))
        self.assertEqual(self.actions(lab), [])

    def test_cross_session_cross_profile_and_undiscovered_rejection(self):
        a = self.lab('profile-a')
        a1 = self.seed(a, 'sid-1', items=[{'explanation': 'ALPHA-ONE-PRIVATE', 'next_step': 'alpha step'}])
        a2 = self.seed(a, 'sid-2', items=[{'explanation': 'BETA-TWO-PRIVATE', 'next_step': 'beta step'}])
        b = self.lab('profile-b')
        b1 = self.seed(b, 'sid-1', items=[{'explanation': 'GAMMA-OTHER-PROFILE', 'next_step': 'gamma step'}])
        b.sid('only-b')
        probes = [
            (a, 'sid-1', a2, 'BETA-TWO'), (a, 'sid-2', a1, 'ALPHA-ONE'), (a, 'sid-1', a1 + a2, 'BETA-TWO'),
            (a, 'sid-1', b1, 'GAMMA'), (b, 'sid-1', a1, 'ALPHA'), (a, 'only-b', a1, 'ALPHA'),
            (a, 'ghost-never-discovered', a1, 'ALPHA'), (a, 'sid-1 ', a1, 'ALPHA'),
        ]
        for lab, sid, note_ids, leak in probes:
            with self.subTest(sid=sid, notes=note_ids):
                out = lab.cmd(f"--session {shlex.quote(sid)} feedback --json {q({'note_ids': note_ids})}")
                self.assertRejected(out, forbidden=(leak, 'PRIVATE', 'step'))
        self.assertEqual(self.actions(a), [])
        self.assertEqual(self.actions(b), [])
        self.assertEqual([s['session_id'] for s in b.j('metadata')['sessions']], ['only-b', 'sid-1'])
        # control: own notes still work; notes view never crosses sessions
        own = a.j(f"--session sid-2 feedback --json {q({'note_ids': a2})}")
        self.assertIn(esc('BETA-TWO-PRIVATE'), own['text'])
        self.assertIn('BETA-TWO-PRIVATE', own['notes'][0]['explanation'])
        self.assertNotIn('ALPHA', json.dumps(a.notes('sid-2')))
        self.assertEqual(len(self.actions(a)), 1)

    def test_no_findings_and_invalid_reviewer_output_produce_nothing_selectable(self):
        lab = self.lab()
        lab.ready('sid-nf', session_allowance=5)
        lab.ctx.llm.push('no_findings')                         # scripted fixture output
        lab.turn('sid-nf', 't1', 'q', 'a')
        att = lab.notes('sid-nf')[0]
        self.assertEqual((att['outcome']['outcome'], att['findings']), ('no_findings', []))
        self.assertEqual(att['served_route']['provider'], harness.FIXTURE_PROVIDER)
        self.assertRejected(lab.cmd(f"--session sid-nf feedback --json {q({'note_ids': [att['attempt_id']]})}"))
        bad = {'outcome': 'findings', 'findings': [
            {'severity': 'concern', 'explanation': 'valid-looking', 'evidence_refs': ['answer'], 'next_step': 'n'},
            {'severity': 'concern', 'explanation': 'bad ref', 'evidence_refs': ['tool-0099-call'], 'next_step': 'n'}]}
        extra = {'outcome': 'no_findings', 'findings': [], 'tool_calls': []}
        for label, payload in (('unavailable-ref', bad), ('extra-key', extra)):
            with self.subTest(label):
                lab.ctx.llm.push(payload)
                lab.turn('sid-nf', f't-{label}', 'q-' + label, 'a-' + label)
                latest = lab.status('sid-nf')['latest_attempt']
                self.assertEqual((latest['status'], latest['outcome']), ('failed', None))
                self.assertEqual(lab.db('SELECT COUNT(*) AS n FROM findings WHERE attempt_id=?', (latest['attempt_id'],))[0]['n'], 0)
        self.assertEqual(self.actions(lab), [])


# ---------------------------------------------------------------- side channels
class NoSideChannel(Case):
    def test_full_command_tour_invokes_reviewer_only_on_admitted_reviews(self):
        lab = self.lab()
        sid = lab.ready('sid-tour', session_allowance=4)
        lab.ctx.llm.push(findings({'explanation': 'e', 'next_step': 's'}))
        lab.turn(sid, 't1', 'q', 'a')
        digest, metrics = lab.transcript_digest(), dict(lab.runtime.adapter.metrics)
        note = lab.notes(sid)[0]
        calls = self.reviewer_calls(lab)
        for raw in ('help', 'metadata', f'--session {sid} status', f'--session {sid} notes',
                    f"--session {sid} rate --json {q({'attempt_id': note['attempt_id'], 'rating': 'useful', 'reason': 'ok'})}",
                    f"--session {sid} feedback --json {q({'note_ids': [note['findings'][0]['note_id']]})}",
                    f'--session {sid} review_now', f'--session {sid} disable', f'--session {sid} enable',
                    f"--session {sid} reset_allowance --json {q({'confirm': True})}",
                    f"--session {sid} configure --json {q({'settings': {'route': ROUTE}, 'expected_revision': 1})}"):
            lab.cmd(raw)
        self.assertEqual(self.reviewer_calls(lab), calls)        # none of the controls ran inference
        self.assertEqual(lab.transcript_digest(), digest)
        self.assertEqual(lab.runtime.adapter.metrics, metrics)   # commands never feed the hook adapter
        kwargs = lab.ctx.llm.calls[0]
        self.assertEqual(set(kwargs), {'instructions', 'input', 'json_schema', 'schema_name', 'provider', 'model',
                                       'max_tokens', 'timeout', 'temperature', 'purpose'})
        self.assertNotIn('tools', kwargs)
        self.assertEqual((kwargs['provider'], kwargs['model'], kwargs['purpose']),
                         (ROUTE['provider'], ROUTE['model'], 'hermes-advisor.review'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
