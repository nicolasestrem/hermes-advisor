"""Authenticated host-mounted API; never creates a worker or registers caller IDs.

The host authenticates every ``/api/plugins/hermes-advisor/*`` request and scopes
it to a profile (``HERMES_HOME``). This module only *looks up* the Runtime that the
hook process registered for that profile's ``plugin_data_dir``; it never
instantiates one, never accepts a caller profile/path, and never discovers a
session from HTTP input. Anything it cannot reach is 503, not a new owner.

Error contract (``detail.error``):
  503 hook_owner_unavailable | profile_identity_unavailable | worker_unavailable
  409 backend_instance_conflict (stale/foreign instance) | ConfigurationConflict
      | invalid_or_stale_request (core rejected values) | <ServiceError class>
  404 session_not_discovered (unknown, or known only to another owner)
  400 invalid_control_shape | invalid_control_types | invalid_control_action
      | invalid_control_params

A missing or empty ``expected_instance`` is a documented 400 shape rejection,
never the framework's generated 422 and never a 409 conflict.
"""
from pathlib import Path
import json
import sys
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

router = APIRouter()
PLUGIN_ID = 'hermes-advisor'
REGISTRY_NAME = 'hermes_advisor_owned_registry_v01'
REJECTION_STATUS = {'backend_instance_conflict': 409, 'worker_unavailable': 503,
                    'invalid_control_action': 400, 'invalid_control_params': 400}


def _unavailable(error='hook_owner_unavailable'):
    return HTTPException(503, detail={'error': error})


def _shape_error():
    return HTTPException(400, detail={'error': 'invalid_control_shape'})


def _required_expected_instance(value):
    """The target instance is caller-proven identity, never optional or empty."""
    if type(value) is not str or not value:
        raise _shape_error()
    return value


def current_runtime():
    """The registered, open, profile-identified owner for this request's profile, else 503."""
    try:
        from plugins.plugin_storage import plugin_data_dir
        key = str(Path(plugin_data_dir(PLUGIN_ID)).resolve())
        owned = sys.modules.get(REGISTRY_NAME)
        if owned is None:
            raise _unavailable()
        with owned.lock:
            runtime = owned.runtimes.get(key)
    except HTTPException:
        raise
    except Exception:
        raise _unavailable() from None
    if runtime is None or getattr(runtime, 'closed', True) is not False:
        raise _unavailable()
    if str(getattr(runtime, 'data_dir', '')) != key:
        raise _unavailable()
    if type(getattr(runtime, 'profile', None)) is not str or not runtime.profile:
        raise _unavailable('profile_identity_unavailable')
    return runtime


def invoke(method, *args, **kwargs):
    try:
        return method(*args, **kwargs)
    except HTTPException:
        raise
    except KeyError as exc:
        if exc.args == ('session_not_discovered',):
            raise HTTPException(404, detail={'error': 'session_not_discovered'}) from None
        raise HTTPException(409, detail={'error': 'KeyError'}) from None
    except ValueError as exc:
        code = getattr(exc, 'code', None)
        if type(exc).__name__ == 'BackendRejected' and code in REJECTION_STATUS:
            raise HTTPException(REJECTION_STATUS[code], detail={'error': code}) from None
        raise HTTPException(409, detail={'error': 'invalid_or_stale_request'}) from None
    except Exception as exc:
        # Error class only: provider/storage exception bodies may contain secrets.
        raise HTTPException(409, detail={'error': type(exc).__name__}) from None


@router.get('/metadata')
def metadata():
    return invoke(current_runtime().metadata)


@router.get('/status')
def status(session_id: str | None = None, expected_instance: str | None = None):
    instance = _required_expected_instance(expected_instance)
    if type(session_id) is not str or not session_id:
        raise _shape_error()
    return invoke(current_runtime().status, instance, session_id)


@router.get('/notes')
def notes(session_id: str | None = None, expected_instance: str | None = None):
    instance = _required_expected_instance(expected_instance)
    if type(session_id) is not str or not session_id:
        raise _shape_error()
    return invoke(current_runtime().notes, instance, session_id)


@router.post('/control')
async def control(request: Request):
    # Parse the raw body here so a malformed, empty or non-object body is the
    # documented 400 shape rejection, never the framework's generated 422.
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        raise _shape_error() from None
    if type(body) is not dict:
        raise _shape_error()
    if set(body) != {'expected_instance', 'session_id', 'action', 'params'}:
        raise _shape_error()
    if any(type(body[k]) is not str for k in ['expected_instance', 'session_id', 'action']) or type(body['params']) is not dict:
        raise HTTPException(400, detail={'error': 'invalid_control_types'})
    instance = _required_expected_instance(body['expected_instance'])
    # Service calls may wait on storage; keep them off the event loop, as the
    # former synchronous handler did.
    return await run_in_threadpool(
        lambda: invoke(current_runtime().control, instance, body['session_id'], body['action'], body['params']))
