"""Bounded public-hook adapter; no host internals, inference or feedback delivery.

Only verified host events may call start/child_start/post/end. HTTP/CLI callers
must never use start() to make a caller-supplied session appear owned.

Real-core contract (advisor_core.Service):
- ``status`` returns a state dict with a ``state`` field (``enabled`` is a state
  value, not a boolean key).
- At ``post`` time the adapter converts raw conversation rows into complete
  ``{"call": ..., "result": ...}`` pairs and lets the core's ``build_packet``
  sanitize/redact the COMPLETE source BEFORE any clipping, producing an
  immutable ``CapturedPacket`` (raw content is never copied or retained here).
  Raw conversation rows (``role``/``content`` dicts) are never valid history
  items, and no adapter pre-clip can strand a credential at a boundary.
- Packet coverage is the core's canonical set (tool_pairs_total,
  tool_pairs_included, tool_pairs_omitted, upstream_omitted_tool_pairs,
  clipped_refs); adapter omissions are forwarded via
  ``prior_coverage={'omitted_tool_pairs': N}`` metadata only.
- ``end`` passes the immutable ``CapturedPacket`` via ``capture(snapshot=...)``
  only after a matching successful end event and unchanged
  applied_revision/applied_generation.
"""
from collections import OrderedDict
import threading
import time

if __package__:
    from .advisor_core import CapturedPacket, build_packet  # noqa: F401
else:
    from advisor_core import CapturedPacket, build_packet  # noqa: F401


class EventAdapter:
    def __init__(self, service, *, surfaces, clock=time.monotonic):
        self.service = service
        self.surfaces = frozenset(surfaces)
        self.clock = clock
        self._lock = threading.RLock()
        self._active = OrderedDict()
        self._pending = OrderedDict()
        # Keep epochs only while post() calls for a session are in flight. This
        # makes invalidation checks race-safe without accumulating session IDs.
        self._invalidation_epochs = {}
        self._posts_in_progress = {}
        # Child exclusion set with LRU semantics: insertion-ordered so that
        # capacity pressure forgets the OLDEST child IDs, never the whole set.
        self._children = OrderedDict()
        self._closed = False
        self.metrics = {'snapshots': 0, 'discarded': 0, 'admitted': 0, 'errors': 0}
        # Register a public, session-scoped invalidation callback. Service invokes
        # it after local state is closed and without holding its runtime lock.
        subscribe = getattr(service, 'subscribe_invalidation', None)
        self._unsubscribe = subscribe(self.invalidate_session) if callable(subscribe) else None

    def invalidate_session(self, session_id):
        """Forget pending snapshots for one invalidated session without reading them."""
        with self._lock:
            if self._posts_in_progress.get(session_id, 0):
                self._invalidation_epochs[session_id] = (
                    self._invalidation_epochs.get(session_id, 0) + 1)
            for key in tuple(self._pending):
                if key[0] == session_id:
                    del self._pending[key]
                    self.metrics['discarded'] += 1

    @staticmethod
    def _id(value):
        return type(value) is str and 0 < len(value) <= 256

    @staticmethod
    def _enabled(status):
        """Real-core enablement: state field equals 'enabled' (fixture dicts with
        a legacy boolean ``enabled`` key remain supported for existing tests)."""
        if type(status) is not dict:
            return False
        if type(status.get('enabled')) is bool:
            return status['enabled']
        return status.get('state') == 'enabled'

    def _expire(self):
        now = self.clock()
        for key, (created, *_rest) in list(self._pending.items()):
            if now - created >= 1800:
                del self._pending[key]
                self.metrics['discarded'] += 1

    @property
    def pending_count(self):
        with self._lock:
            self._expire()
            return len(self._pending)

    def start(self, *, session_id=None, turn_id=None, platform=None, **_kwargs):
        if not self._id(session_id) or not self._id(turn_id) or platform not in self.surfaces:
            return
        with self._lock:
            if self._closed or session_id in self._children:
                return
            self._active[(session_id, turn_id)] = platform
            # Metadata is bounded too. This never inspects/digests content.
            while len(self._active) > 256:
                old, _ = self._active.popitem(last=False)
                self._pending.pop(old, None)
        try:
            self.service.discover(session_id, platform)
        except Exception:
            with self._lock:
                self._active.pop((session_id, turn_id), None)
                self.metrics['errors'] += 1

    def child_start(self, *, child_session_id=None, **_kwargs):
        if not self._id(child_session_id):
            return
        with self._lock:
            # Lifecycle tracking is metadata only. At capacity, forget the
            # OLDEST child IDs (LRU) instead of clearing the exclusion set:
            # a still-tracked child must never be re-discovered as a main
            # session, and pending packets of unrelated sessions must
            # survive overflow. Pendings that reference a dropped or
            # re-registered child ID are discarded with visible accounting,
            # never silently cleared.
            self._children.pop(child_session_id, None)
            self._children[child_session_id] = None
            while len(self._children) > 256:
                evicted, _ = self._children.popitem(last=False)
                for key in [key for key in self._pending if key[0] == evicted]:
                    del self._pending[key]
                    self.metrics['discarded'] += 1
            for key in [key for key in self._pending if key[0] == child_session_id]:
                del self._pending[key]
                self.metrics['discarded'] += 1

    def child_stop(self, *, child_session_id=None, **_kwargs):
        with self._lock:
            self._children.pop(child_session_id, None)

    def _pair_rows(self, history):
        """Convert current-turn raw rows into complete call/result pairs.

        A structural decision only: rows are referenced, never copied, clipped,
        sanitized, or digested here - the core redacts the complete source
        BEFORE any clipping when the packet is built at post time. An assistant
        tool_calls block is the call; each following tool row is its result,
        matched by individual tool_call_id in call-declaration order.
        Non-tool rows and unmatched halves are omitted and counted upstream,
        never fabricated into paired calls.
        """
        omitted = 0
        limitations = set()
        if type(history) is not list:
            limitations.add('history_unavailable')
            return [], 1
        window = history[-200:]
        if len(history) > 200:
            limitations.add('history_window_limited')
            omitted += len(history) - 200
        boundary = None
        boundaries = []
        for index, row in enumerate(window):
            if type(row) is dict and row.get('role') == 'user':
                boundaries.append(index)
                boundary = index
        if boundary is None and window:
            limitations.add('current_turn_boundary_unavailable')
            omitted += sum(1 for row in window if type(row) is dict and
                           row.get('role') == 'assistant' and type(row.get('tool_calls')) is list
                           for _call in row['tool_calls'])
        elif len(boundaries) > 1:
            # Earlier user boundary splits historical tool activity from the
            # current turn. Report every declared earlier call as omitted.
            limitations.add('current_turn_boundary_split')
            omitted += sum(1 for row in window[:boundary] if type(row) is dict and
                           row.get('role') == 'assistant' and type(row.get('tool_calls')) is list
                           for _call in row['tool_calls'])
        source = window[boundary + 1:] if boundary is not None else []
        call_order = []   # tool call ids in declaration order
        calls = {}        # call_id -> raw call dict
        results = {}      # call_id -> raw tool row (first result wins)
        for row in source:
            if type(row) is not dict:
                limitations.add('non_message_omitted')
                continue
            role = row.get('role')
            if role == 'assistant' and type(row.get('tool_calls')) is list:
                declared = row['tool_calls']
                # Bound fanout per assistant row, not across the whole turn:
                # completed pairs from earlier rows must not consume capacity.
                for call in declared[:20]:
                    if type(call) is not dict or type(call.get('function')) is not dict:
                        limitations.add('malformed_tool_call_omitted')
                        omitted += 1
                        continue
                    call_id = call.get('id')
                    if type(call_id) is not str or not call_id or call_id in calls:
                        limitations.add('malformed_tool_call_omitted')
                        omitted += 1
                        continue
                    call_order.append(call_id)
                    calls[call_id] = call
                if len(declared) > 20:
                    limitations.add('tool_call_list_limited')
            elif role == 'tool':
                call_id = row.get('tool_call_id')
                if type(call_id) is not str or not call_id:
                    limitations.add('orphan_tool_result_omitted')
                    omitted += 1
                elif call_id in calls and call_id not in results:
                    results[call_id] = row
                else:
                    # Orphan or duplicate result without a retained open call.
                    limitations.add('orphan_tool_result_omitted')
                    omitted += 1
            else:
                # user/system/assistant prose rows never enter the packet;
                # reasoning and prior turns are excluded upstream.
                limitations.add('non_tool_row_omitted')
        pairs = []
        for call_id in call_order:
            result = results.get(call_id)
            if result is None:
                # A call without its individual result is an omission, not a
                # fabricated pair.
                limitations.add('tool_pairs_missing_result')
                omitted += 1
                continue
            pairs.append({'call': calls[call_id], 'result': result})
        return pairs, omitted

    def post(self, *, session_id=None, turn_id=None, platform=None,
             user_message=None, assistant_response=None, conversation_history=None, **_kwargs):
        key = (session_id, turn_id)
        with self._lock:
            self._expire()
            if self._closed or key not in self._active or platform != self._active[key]:
                return
            epoch = self._invalidation_epochs.get(session_id, 0)
            self._posts_in_progress[session_id] = (
                self._posts_in_progress.get(session_id, 0) + 1)
        try:
            try:
                status = self.service.status(session_id)
            except Exception:
                with self._lock:
                    self.metrics['errors'] += 1
                return
            if not self._enabled(status):
                # Disabled/paused: no content inspection, no digest, no retention.
                return
            settings = status.get('settings')
            cap = settings.get('packet_bytes') if type(settings) is dict else None
            if type(cap) is not int or not 1024 <= cap <= 32768:
                cap = 32768
            try:
                pairs, omitted = self._pair_rows(conversation_history)
                # Core sanitize/clip over the COMPLETE source; the adapter never
                # pre-clips. The returned CapturedPacket is immutable from this
                # moment, so later host mutations of its history cannot leak in.
                packet = build_packet(
                    request=user_message if type(user_message) is str else '',
                    answer=assistant_response if type(assistant_response) is str else '',
                    history=pairs, packet_bytes=cap,
                    prior_coverage={'omitted_tool_pairs': omitted})
            except Exception:
                # An unsafe or unfittable packet fails closed at post; nothing
                # pending, nothing retained.
                with self._lock:
                    self.metrics['errors'] += 1
                return
            with self._lock:
                if self._closed:
                    return
                if self._invalidation_epochs.get(session_id, 0) != epoch:
                    # A callback ran after this post began. Do not retain the
                    # stale packet, even if it finished after the callback's
                    # pending-map sweep.
                    self.metrics['discarded'] += 1
                    return
                self._pending[key] = (self.clock(), packet,
                                      status.get('applied_revision'),
                                      status.get('applied_generation'))
                self._pending.move_to_end(key)
                self.metrics['snapshots'] += 1
                while len(self._pending) > 16:
                    self._pending.popitem(last=False)
                    self.metrics['discarded'] += 1
        finally:
            with self._lock:
                in_progress = self._posts_in_progress.get(session_id, 0)
                if in_progress <= 1:
                    self._posts_in_progress.pop(session_id, None)
                    self._invalidation_epochs.pop(session_id, None)
                else:
                    self._posts_in_progress[session_id] = in_progress - 1

    def end(self, *, session_id=None, turn_id=None, platform=None,
            completed=None, failed=None, interrupted=None, **_kwargs):
        key = (session_id, turn_id)
        with self._lock:
            self._expire()
            if self._closed:
                return
            entry = self._pending.pop(key, None)
            surface = self._active.pop(key, None)
        if entry is None:
            return
        success = completed is True and failed is False and interrupted is False
        if not success or platform != surface:
            with self._lock:
                self.metrics['discarded'] += 1
            return
        try:
            status = self.service.status(session_id)
            if not self._enabled(status):
                # Disabled/paused at end: discard without any content access.
                with self._lock:
                    self.metrics['discarded'] += 1
                return
            if (status.get('applied_revision') != entry[2]
                    or status.get('applied_generation') != entry[3]):
                # Settings/revision changed between post and end: the packet
                # was captured under stale configuration.
                with self._lock:
                    self.metrics['discarded'] += 1
                return
            # Pass the immutable CapturedPacket captured at post time; the
            # core validates digest/coverage and re-checks enablement. Raw
            # content arguments stay empty: no raw text is ever forwarded.
            result = self.service.capture(session_id=session_id, turn_id=turn_id,
                                          request=None, answer=None, history=(),
                                          snapshot=entry[1],
                                          expected_applied_revision=entry[2],
                                          expected_applied_generation=entry[3])
            with self._lock:
                if type(result) is dict and result.get('accepted') is False:
                    self.metrics['discarded'] += 1
                else:
                    self.metrics['admitted'] += 1
            return result
        except Exception:
            # Never surface a raw provider/storage exception into the main
            # conversation. The Service owns structured operational statuses.
            with self._lock:
                self.metrics['errors'] += 1

    def close(self):
        self._closed = True
        self._pending.clear()
        self._active.clear()
        self._children.clear()
        unsubscribe = self._unsubscribe
        self._unsubscribe = None
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception:
                pass
