from scripts.japan_pool_monitor import Monitor
import pytest


class FakeController:
    def __init__(self, samples, current='a'):
        self.samples = {key: list(value) for key, value in samples.items()}
        self.selected = current
        self.changes = []

    def current(self):
        return {'type': 'Selector', 'now': self.selected, 'all': ['a', 'b', 'c']}

    def probe(self, node):
        return self.samples[node].pop(0)

    def select(self, node):
        self.changes.append(node)
        self.selected = node


def monitor(controller, state=None):
    return Monitor({'nodes': ['a', 'b', 'c'], 'failure_confirmations': 3,
                    'replacement_confirmations': 2, 'quarantine_seconds': 600}, controller,
                   state=state, clock=lambda: 1000, sleep=lambda seconds: None)


def test_healthy_current_is_not_switched_to_faster_or_preferred_node():
    controller = FakeController({'b': [300]}, current='b')
    assert monitor(controller).step()['status'] == 'healthy'
    assert controller.changes == []


def test_transient_failure_does_not_switch_or_close_connections():
    controller = FakeController({'a': [None, None, 100]})
    assert monitor(controller).step()['recovered_after_failures'] == 2
    assert controller.changes == []


def test_confirmed_failure_selects_only_twice_healthy_replacement():
    controller = FakeController({'a': [None] * 3, 'b': [100, None], 'c': [150, 160]})
    policy = monitor(controller)
    assert policy.step()['to'] == 'c'
    assert controller.changes == ['c']
    assert policy.state['quarantine_until'] == {'a': 1600, 'b': 1600}


def test_all_failed_keeps_selection_without_direct_fallback():
    controller = FakeController({'a': [None] * 3, 'b': [None], 'c': [None]})
    assert monitor(controller).step()['status'] == 'all_unavailable'
    assert controller.changes == []


def test_recently_failed_node_is_not_immediately_reselected():
    controller = FakeController({'a': [None] * 3, 'c': [180, 190]})
    assert monitor(controller, {'quarantine_until': {'b': 1200}}).step()['to'] == 'c'


def test_pool_wide_outage_does_not_block_recovery_for_full_quarantine():
    controller = FakeController({'a': [None] * 3, 'b': [180, 190]})
    state = {'quarantine_until': {'b': 1500, 'c': 1550}}
    assert monitor(controller, state).step()['to'] == 'b'


def test_just_failed_replacements_are_not_retried_immediately():
    controller = FakeController({'a': [None] * 3})
    state = {'quarantine_until': {'b': 1590, 'c': 1595}}
    assert monitor(controller, state).step()['status'] == 'all_unavailable'
    assert controller.changes == []


def test_controller_failure_never_triggers_node_switch():
    controller = FakeController({})
    def unavailable():
        raise OSError('controller unavailable')
    controller.current = unavailable
    with pytest.raises(OSError):
        monitor(controller).step()
    assert controller.changes == []


def test_invalid_pool_membership_never_triggers_switch():
    controller = FakeController({}, current='unexpected')
    with pytest.raises(RuntimeError, match='approved'):
        monitor(controller).step()
    assert controller.changes == []


def test_external_change_during_probe_is_preserved():
    controller = FakeController({'a': [None] * 3, 'b': [100, 110]})
    original = controller.probe
    def probe(node):
        value = original(node)
        if node == 'b':
            controller.selected = 'c'
        return value
    controller.probe = probe
    assert monitor(controller).step()['status'] == 'selection_changed_externally'
    assert controller.changes == []


def configured(controller, state=None, now=1000, **options):
    config = {'nodes': ['a', 'b', 'c'], 'failure_confirmations': 3,
              'replacement_confirmations': 2, 'quarantine_seconds': 600, **options}
    return Monitor(config, controller, state=state, clock=lambda: now, sleep=lambda _: None)


def test_dead_standby_is_retained_and_rechecked_while_primary_healthy():
    controller = FakeController({'a': [100], 'b': [None]})
    policy = configured(controller, standby_probes_per_cycle=1)
    policy.step()
    assert policy.config['nodes'] == ['a', 'b', 'c']
    assert policy.state['nodes']['b']['status'] == 'quarantined'
    assert policy.state['nodes']['b']['next_probe_at'] == 1060
    assert controller.changes == []


def test_recovered_node_returns_to_standby_without_automatic_failback():
    state = {'quarantine_until': {'a': 1600},
             'nodes': {'a': {'consecutive_successes': 2, 'failure_episodes': 1},
                       'b': {'last_probe_at': 999}}}
    controller = FakeController({'c': [120], 'a': [80]}, current='c')
    policy = configured(controller, state, standby_probes_per_cycle=1, recovery_successes=3)
    policy.step()
    assert policy.state['nodes']['a']['status'] == 'ready'
    assert 'a' not in policy.state['quarantine_until']
    assert controller.changes == []


def test_failed_recovery_resets_consecutive_success_requirement():
    state = {'quarantine_until': {'a': 1600},
             'nodes': {'a': {'consecutive_successes': 2}, 'b': {'last_probe_at': 999}}}
    controller = FakeController({'c': [120], 'a': [None]}, current='c')
    policy = configured(controller, state, standby_probes_per_cycle=1)
    policy.step()
    assert policy.state['nodes']['a']['consecutive_successes'] == 0
    assert policy.state['nodes']['a']['status'] == 'quarantined'


def test_recovery_checks_respect_backoff_and_cycle_budget():
    state = {'nodes': {'b': {'next_probe_at': 1200}}}
    controller = FakeController({'a': [100], 'c': [90]})
    policy = configured(controller, state, standby_probes_per_cycle=1)
    assert [n['node'] for n in policy.step()['standbys']] == ['c']
    assert controller.changes == []


def test_three_successes_required_for_failed_backup_before_selection():
    controller = FakeController({'a': [None]*3, 'b': [90, 95, 99], 'c': [None]})
    state = {'quarantine_until': {'b': 1500}}
    policy = configured(controller, state, recovery_successes=3)
    assert policy.step()['to'] == 'b'
    assert controller.samples['b'] == []
    assert 'b' not in state['quarantine_until']


def test_live_stream_progress_overrides_failed_probe_without_switching():
    controller = FakeController({'a': [None]*3})
    snapshots = iter([{'stream': 100}, {'stream': 250}])
    controller.activity = lambda _: next(snapshots)
    policy = configured(controller, protect_active_traffic=True)
    assert policy.step()['status'] == 'probe_failed_but_stream_active'
    assert controller.changes == []


def test_stalled_stream_does_not_block_failover_forever():
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    controller.activity = lambda _: {'stream': 100}
    policy = configured(controller, protect_active_traffic=True)
    assert policy.step()['to'] == 'b'


def test_switch_limit_persists_across_monitor_restart():
    import json
    state = json.loads(json.dumps({'switch_history': [
        {'time': 100, 'from': 'c', 'to': 'b'}, {'time': 200, 'from': 'b', 'to': 'a'}]}))
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    policy = configured(controller, state, max_switches_per_window=2, switch_window_seconds=1800)
    result = policy.step()
    assert result['status'] == 'switch_rate_limited'
    assert result['retry_after_seconds'] == 900
    assert result['ready_backup'] == 'b'
    assert controller.changes == []


def test_expired_switch_window_allows_verified_backup():
    state = {'switch_history': [{'time': 100, 'from': 'c', 'to': 'a'}]}
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    policy = configured(controller, state, now=2000, max_switches_per_window=1,
                        switch_window_seconds=1800)
    assert policy.step()['to'] == 'b'
    assert len(state['switch_history']) == 1
    assert state['switch_history'][0]['time'] == 2000


def test_minimum_hold_prevents_immediate_second_switch():
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    policy = configured(controller, {'last_switch_at': 980}, minimum_switch_interval_seconds=60)
    assert policy.step()['retry_after_seconds'] == 40
    assert controller.changes == []


def test_probe_uses_second_url_only_after_first_failure():
    from scripts.japan_pool_monitor import Controller
    controller = Controller({'test_url': 'first', 'fallback_test_urls': ['second']})
    calls = []
    def probe(node, url):
        calls.append((node, url))
        return 123 if url == 'second' else None
    controller._probe_url = probe
    assert controller.probe('a') == 123
    assert calls == [('a', 'first'), ('a', 'second')]


def test_switch_api_never_closes_connections_or_reloads_configuration():
    from scripts.japan_pool_monitor import Controller
    controller = Controller({'group': 'Pool'})
    calls = []
    controller.request = lambda *args: calls.append(args)
    controller.select('b')
    assert calls == [('/proxies/Pool', 'PUT', {'name': 'b'})]


def test_failed_switch_response_still_consumes_persisted_attempt():
    import copy
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    saved = []
    policy = configured(controller)
    policy.persist = lambda state: saved.append(copy.deepcopy(state))
    def timeout(node):
        assert saved, 'Persist before changing controller selection'
        raise OSError('reply lost')
    controller.select = timeout
    with pytest.raises(OSError):
        policy.step()
    assert saved[0]['switch_history'] == [{'time': 1000, 'from': 'a', 'to': 'b'}]


def test_corrupt_state_is_not_silently_replaced_with_empty_switch_history(tmp_path):
    import json
    from scripts.japan_pool_monitor import load_state
    path = tmp_path/'state.json'
    assert load_state(path) == {'quarantine_until': {}}
    path.write_text('{broken')
    with pytest.raises(json.JSONDecodeError):
        load_state(path)


def test_activity_ignores_unrelated_hosts_and_other_nodes():
    from scripts.japan_pool_monitor import Controller
    controller = Controller({})
    controller.request = lambda _: {'connections': [
        {'id': 'real', 'chains': ['a'], 'metadata': {'host': 'chatgpt.com'}, 'download': 10},
        {'id': 'fake', 'chains': ['a'], 'metadata': {'host': 'notchatgpt.com'}, 'download': 20},
        {'id': 'other', 'chains': ['b'], 'metadata': {'host': 'chatgpt.com'}, 'download': 30}]}
    assert controller.activity('a') == {'real': 10}


def test_original_policy_has_no_switch_count_or_hold_limit():
    state = {'last_switch_at': 1001, 'switch_history': [
        {'time': 990, 'from': 'c', 'to': 'b'}, {'time': 999, 'from': 'b', 'to': 'a'}]}
    controller = FakeController({'a': [None]*3, 'b': [100, 110]})
    policy = configured(controller, state)
    assert policy.step()['to'] == 'b'
    assert len(state['switch_history']) == 3
    assert controller.changes == ['b']
