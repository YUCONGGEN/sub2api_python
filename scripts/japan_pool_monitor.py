#!/usr/bin/env python3
"""Sticky active/standby proxy pool. Never closes or migrates existing streams.

Only the Selector API is changed after confirmed failure. This process is not
in the data path; it does not buffer, retry or inspect application SSE payloads.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import quote, urlencode


COUNTRY_ALIASES = {
    'JP': ('日本', '东京', '大阪', '埼玉', 'jp', 'japan', 'tokyo', 'osaka'),
    'HK': ('香港', '港', 'hk', 'hong kong'),
    'TW': ('台湾', '台北', '新北', 'tw', 'taiwan', 'taipei'),
    'SG': ('新加坡', '狮城', 'sg', 'singapore'),
    'KR': ('韩国', '首尔', '韩', 'kr', 'korea', 'seoul'),
    'US': ('美国', '洛杉矶', '西雅图', '硅谷', '纽约', '达拉斯', 'us', 'usa', 'united states'),
    'GB': ('英国', '伦敦', 'uk', 'gb', 'united kingdom', 'london'),
    'DE': ('德国', '法兰克福', 'de', 'germany', 'frankfurt'),
    'CA': ('加拿大', '多伦多', '温哥华', 'ca', 'canada', 'toronto', 'vancouver'),
    'AU': ('澳大利亚', '澳洲', '悉尼', 'au', 'australia', 'sydney'),
}


def country_for_node(name: str) -> str:
    lowered = str(name or '').casefold()
    for code, aliases in COUNTRY_ALIASES.items():
        for alias in aliases:
            candidate = alias.casefold()
            if candidate.isascii() and len(candidate) <= 2:
                if re.search(rf'(^|[^a-z]){re.escape(candidate)}([^a-z]|$)', lowered):
                    return code
            elif candidate in lowered:
                return code
    return 'OTHER'


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__('localhost', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Controller:
    def __init__(self, config: dict):
        self.config = config

    def request(self, path: str, method='GET', payload=None):
        connection = UnixConnection(self.config['controller_socket'], 10)
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request(method, path, body=body, headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read()
            if not 200 <= response.status < 300:
                raise RuntimeError(f'controller HTTP {response.status}')
            return json.loads(data) if data else {}
        finally:
            connection.close()

    def current(self):
        return self.request('/proxies/' + quote(self.config['group'], safe=''))

    def probe(self, node: str):
        for url in [self.config['test_url'], *self.config.get('fallback_test_urls', [])]:
            delay = self._probe_url(node, url)
            if delay is not None:
                return delay
        return None

    def _probe_url(self, node: str, url: str):
        query = urlencode({'url': url, 'expected': self.config.get('expected_status', 200),
                           'timeout': self.config.get('probe_timeout_ms', 5000)})
        try:
            data = self.request('/proxies/' + quote(node, safe='') + '/delay?' + query)
            delay = data.get('delay')
            return int(delay) if isinstance(delay, (int, float)) and delay > 0 else None
        except (OSError, RuntimeError, http.client.HTTPException, ValueError):
            return None

    def activity(self, node: str):
        """Counters only; never inspect request bodies, tokens or client identity."""
        def ai_host(item):
            host = str(item.get('metadata', {}).get('host', '')).lower().rstrip('.')
            return any(host == domain or host.endswith('.' + domain)
                       for domain in ('chatgpt.com', 'openai.com', 'anthropic.com', 'claude.ai'))
        return {item['id']: int(item.get('download', 0))
                for item in self.request('/connections').get('connections', [])
                if node in item.get('chains', []) and item.get('id')
                and ai_host(item)}

    def select(self, node: str):
        # A selected proxy is resolved when a NEW connection is dialed. Existing
        # TCP streams retain their original outbound. No /connections DELETE,
        # core restart, config reload or latency-driven auto-failback is used.
        self.request('/proxies/' + quote(self.config['group'], safe=''), 'PUT', {'name': node})


class Monitor:
    def __init__(self, config, controller, *, state=None, clock=time.time, sleep=time.sleep, persist=None):
        if not config.get('nodes') or len(set(config['nodes'])) != len(config['nodes']):
            raise ValueError('Pool must contain distinct approved nodes')
        if int(config.get('failure_confirmations', 3)) < 2:
            raise ValueError('At least two consecutive failures are required')
        if int(config.get('replacement_confirmations', 2)) < 2:
            raise ValueError('At least two successful replacement probes are required')
        self.config, self.controller = config, controller
        self.state = state if state is not None else {'quarantine_until': {}}
        self.clock, self.sleep = clock, sleep
        self.persist = persist or (lambda state: None)

    def eligible_nodes(self):
        """Apply global country, subscription enablement and per-source caps."""
        allowed = {str(value).upper() for value in self.config.get(
            'allowed_countries', [*COUNTRY_ALIASES.keys(), 'OTHER'])}
        disabled = {str(value) for value in self.config.get('disabled_sources', [])}
        sources = self.config.get('sources', {})
        countries = self.config.get('node_countries', {})
        limits = self.config.get('source_node_limits', {})
        grouped = {}
        ranks = {'active': 0, 'ready': 1, 'probation': 2, 'unknown': 3, 'quarantined': 4}
        try:
            current = str(self.controller.current().get('now') or '')
        except Exception:
            current = ''
        records = self.state.get('nodes', {})
        for index, node in enumerate(self.config['nodes']):
            source = str(sources.get(node, ''))
            country = str(countries.get(node) or country_for_node(node)).upper()
            if source in disabled or country not in allowed:
                continue
            recorded = str(records.get(node, {}).get('status') or 'unknown')
            status = 'active' if node == current and recorded != 'quarantined' else recorded
            grouped.setdefault(source, []).append((ranks.get(status, 3), index, node))
        result = []
        for source, candidates in grouped.items():
            limit = max(1, min(int(limits.get(source, 20)), 20))
            result.extend(node for _, _, node in sorted(candidates)[:limit])
        return result

    def standbys(self, current):
        """Return the configured number of ordered failover candidates."""
        alternatives = [node for node in self.eligible_nodes() if node != current]
        requested = int(self.config.get('standby_pool_size', len(alternatives)))
        if requested < 1:
            raise ValueError('standby_pool_size must be at least 1')
        return alternatives[:requested]

    def select_policy_replacement(self, current):
        """Move new connections away from a disabled/excluded source safely."""
        for candidate in self.standbys(current):
            samples = []
            required = int(self.config.get('replacement_confirmations', 2))
            for attempt in range(required):
                if attempt:
                    self.sleep(float(self.config.get('replacement_interval_seconds', 2)))
                delay = self.probe(candidate)
                if delay is None:
                    break
                samples.append(delay)
            if len(samples) != required:
                self.quarantine(candidate)
                continue
            if self.controller.current().get('now') != current:
                return {'status': 'selection_changed_externally'}
            self.state['last_switch_at'] = self.clock()
            self.state.setdefault('switch_history', []).append(
                {'time': self.clock(), 'from': current, 'to': candidate, 'reason': 'policy'})
            self.persist(self.state)
            self.controller.select(candidate)
            self.state['nodes'][candidate]['status'] = 'active'
            return {'status': 'policy_switched_new_connections_only', 'from': current,
                    'to': candidate, 'delay_ms': round(sum(samples) / len(samples), 1)}
        return {'status': 'policy_no_healthy_candidate', 'node': current, 'changed': False}

    def probe(self, node):
        delay = self.controller.probe(node)
        record = self.state.setdefault('nodes', {}).setdefault(node, {})
        record['last_probe_at'] = self.clock()
        record['checks'] = record.get('checks', 0) + 1
        if delay is None:
            record['consecutive_successes'] = 0
            record['failures'] = record.get('failures', 0) + 1
        else:
            record['last_success_at'] = self.clock()
            record['delay_ms'] = delay
            record['consecutive_successes'] = record.get('consecutive_successes', 0) + 1
        return delay

    def quarantine(self, node):
        self.state.setdefault('quarantine_until', {})[node] = (
            self.clock() + float(self.config.get('quarantine_seconds', 600)))
        record = self.state.setdefault('nodes', {}).setdefault(node, {})
        record.update(status='quarantined', consecutive_successes=0)
        record['failure_episodes'] = record.get('failure_episodes', 0) + 1

    def recover_standbys(self, current):
        """Round-robin rechecks retain failed nodes without switching a healthy primary."""
        budget = int(self.config.get('standby_probes_per_cycle', 0))
        if budget <= 0:
            return []
        now = self.clock()
        records = self.state.setdefault('nodes', {})
        # Probe every eligible candidate, including nodes beyond the failover
        # pool size. Otherwise those candidates can remain "unknown" forever.
        # standbys() still limits which nodes may be selected on failover.
        alternatives = [n for n in self.eligible_nodes() if n != current
                        if records.get(n, {}).get('next_probe_at', 0) <= now]
        alternatives.sort(key=lambda n: records.get(n, {}).get('last_probe_at', 0))
        results = []
        for node in alternatives[:budget]:
            delay = self.probe(node)
            record = records[node]
            if delay is None:
                self.quarantine(node)
                interval = min(float(self.config.get('recovery_max_interval_seconds', 900)),
                               float(self.config.get('recovery_interval_seconds', 60))
                               * 2 ** min(record['failure_episodes'] - 1, 4))
            else:
                interval = float(self.config.get('standby_interval_seconds', 180))
                if record['consecutive_successes'] >= int(self.config.get('recovery_successes', 3)):
                    record.update(status='ready', failure_episodes=0)
                    self.state.setdefault('quarantine_until', {}).pop(node, None)
                else:
                    record['status'] = 'probation'
            record['next_probe_at'] = self.clock() + interval
            results.append({'node': node, 'status': record['status'], 'delay_ms': delay})
        return results

    def activity(self, node):
        if not self.config.get('protect_active_traffic', False):
            return {}
        try:
            return self.controller.activity(node)
        except (OSError, RuntimeError, http.client.HTTPException, ValueError):
            return {}

    def switch_wait(self):
        now = self.clock()
        window = float(self.config.get('switch_window_seconds', 1800))
        history = [event for event in self.state.get('switch_history', [])
                   if event['time'] > now - window]
        self.state['switch_history'] = history
        deadlines = []
        maximum = int(self.config.get('max_switches_per_window', 0))
        if maximum > 0 and len(history) >= maximum:
            deadlines.append(history[-maximum]['time'] + window)
        gap = float(self.config.get('minimum_switch_interval_seconds', 0))
        if gap > 0 and self.state.get('last_switch_at') is not None:
            deadlines.append(self.state['last_switch_at'] + gap)
        return max(0, max(deadlines, default=now) - now)

    def step(self):
        info = self.controller.current()  # Controller failure is NOT node failure.
        if info.get('type') != 'Selector':
            raise RuntimeError('Pool must be a manual Selector; automatic switching is disabled')
        current = info.get('now')
        available = set(info.get('all', []))
        if current not in self.config['nodes'] or not set(self.config['nodes']).issubset(available):
            raise RuntimeError('Active pool differs from approved configuration; refusing to switch')
        eligible = self.eligible_nodes()
        if not eligible:
            raise RuntimeError('No eligible nodes remain after country and subscription filters')
        if current not in eligible:
            return self.select_policy_replacement(current)
        activity_before = self.activity(current)
        for attempt in range(int(self.config.get('failure_confirmations', 3))):
            if attempt:
                self.sleep(float(self.config.get('failure_interval_seconds', 10)))
            delay = self.probe(current)
            if delay is not None:
                self.state['nodes'][current].update(status='active', failure_episodes=0)
                self.state.setdefault('quarantine_until', {}).pop(current, None)
                return {'status': 'healthy', 'node': current, 'delay_ms': delay,
                        'recovered_after_failures': attempt,
                        'standbys': self.recover_standbys(current)}
        activity_after = self.activity(current)
        if any(activity_after.get(key, value) > value for key, value in activity_before.items()):
            return {'status': 'probe_failed_but_stream_active', 'node': current,
                    'changed': False, 'standbys': self.recover_standbys(current)}
        now = self.clock()
        quarantine = self.state.setdefault('quarantine_until', {})
        quarantine_seconds = float(self.config.get('quarantine_seconds', 600))
        self.quarantine(current)
        alternatives = self.standbys(current)
        eligible = [node for node in alternatives if float(quarantine.get(node, 0)) <= now]
        # If ordinary backups fail too, periodically retry quarantined nodes.
        # Never impose a ten-minute blackout after a shared network outage.
        recheck_after = float(self.config.get('quarantine_recheck_seconds', 60))
        recovery = [node for node in alternatives if node not in eligible
                    and now - (float(quarantine[node]) - quarantine_seconds) >= recheck_after]
        for candidate in eligible + recovery:
            samples = []
            required = int(self.config.get('replacement_confirmations', 2))
            if candidate in quarantine:
                required = max(required, int(self.config.get('recovery_successes', 2)))
            for attempt in range(required):
                if attempt:
                    self.sleep(float(self.config.get('replacement_interval_seconds', 2)))
                delay = self.probe(candidate)
                if delay is None:
                    break
                samples.append(delay)
            if len(samples) != required:
                self.quarantine(candidate)
                continue
            self.state['nodes'][candidate].update(status='ready', failure_episodes=0)
            quarantine.pop(candidate, None)
            wait = self.switch_wait()
            if wait > 0:
                return {'status': 'switch_rate_limited', 'node': current, 'ready_backup': candidate,
                        'retry_after_seconds': round(wait), 'changed': False}
            # A human or another manager may have changed selection during tests.
            if self.controller.current().get('now') != current:
                return {'status': 'selection_changed_externally'}
            # Persist the attempt BEFORE the side effect. A lost controller reply
            # or process crash must not reset the anti-flap allowance on restart.
            self.state['last_switch_at'] = self.clock()
            self.state.setdefault('switch_history', []).append(
                {'time': self.clock(), 'from': current, 'to': candidate})
            self.persist(self.state)
            self.controller.select(candidate)
            self.state['nodes'][candidate]['status'] = 'active'
            return {'status': 'switched_new_connections_only', 'from': current, 'to': candidate,
                    'delay_ms': round(sum(samples) / len(samples), 1)}
        return {'status': 'all_unavailable', 'node': current, 'changed': False}


def atomic_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def load_state(path: Path):
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'quarantine_until': {}}
    if not isinstance(state, dict):
        raise ValueError('Invalid monitor state; restore backup instead of resetting switch history')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--status', action='store_true', help='Read status without probing or switching')
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    state_path = config_path.with_name('state.json')
    if args.status:
        state = load_state(state_path)
        current = Controller(config).current()
        print(json.dumps({'current': current.get('now'), 'pool': current.get('all'),
                          'policy': {key: config.get(key) for key in (
                              'standby_pool_size', 'interval_seconds', 'failure_confirmations',
                              'replacement_confirmations', 'quarantine_seconds',
                              'standby_probes_per_cycle', 'recovery_successes')},
                          'last_check': state.get('last_check'),
                          'nodes': state.get('nodes', {}),
                          'switch_history': state.get('switch_history', [])}, ensure_ascii=False, indent=2))
        return
    import fcntl  # macOS/Linux only; keep policy unit tests portable.
    with config_path.with_name('monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A pool monitor is already running')
        state = load_state(state_path)
        monitor = Monitor(config, Controller(config), state=state,
                          persist=lambda value: atomic_json(state_path, value))
        while True:
            try:
                # Policy edits made by the administrator take effect on the next
                # cycle without restarting the proxy core or interrupting streams.
                fresh = json.loads(config_path.read_text(encoding='utf-8'))
                monitor.config = fresh
                monitor.controller.config = fresh
                result = monitor.step()
                monitor.state['last_check'] = {'time': time.time(), **result}
                atomic_json(state_path, monitor.state)
            except Exception as exc:
                result = {'status': 'monitor_error', 'error': str(exc)}
                monitor.state['last_check'] = {'time': time.time(), **result}
                atomic_json(state_path, monitor.state)
            print(json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), **result}, ensure_ascii=False), flush=True)
            if args.once:
                return
            time.sleep(max(10, float(monitor.config.get('interval_seconds', 60))))


if __name__ == '__main__':
    main()
