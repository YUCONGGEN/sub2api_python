#!/usr/bin/env python3
"""Sticky active/standby proxy pool. Never closes or migrates existing streams.

Only the Selector API is changed after confirmed failure. This process is not
in the data path; it does not buffer, retry or inspect application SSE payloads.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
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


def known_remaining(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        remaining = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return remaining if math.isfinite(remaining) and remaining >= 0 else None


def exhausted_sources(config):
    """Unknown/unlimited quota is not zero. A confirmed zero excludes a source."""
    exhausted = {str(source) for source in config.get('exhausted_sources', [])}
    for source, value in config.get('source_remaining_bytes', {}).items():
        if known_remaining(value) == 0:
            exhausted.add(str(source))
    return exhausted


def load_policy(config_path):
    """Read fresh, credential-free quota metadata without downloading subscriptions.

    The registry may be updated without changing the monitor configuration.
    Read it every cycle so a confirmed zero (or replenishment) takes effect even
    when the proxy profile itself has not changed.
    """
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8'))
    configured = config.get('subscription_registry_path')
    paths = [Path(configured)] if configured else [
        config_path.with_name('subscriptions.json'), config_path.with_name('proxy-subscriptions.json')]
    remaining = dict(config.get('source_remaining_bytes', {}))
    exhausted = exhausted_sources(config)
    disabled = set(config.get('disabled_sources', []))
    for path in paths:
        if not path.is_absolute():
            path = config_path.parent / path
        try:
            registry = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue
        if not isinstance(registry, dict) or not isinstance(registry.get('subscriptions'), list):
            raise ValueError('Invalid subscription registry')
        for item in registry['subscriptions']:
            source = str(item.get('pool_source') or '')
            if not source:
                continue
            if item.get('enabled') is False:
                disabled.add(source)
            if known_remaining(item.get('traffic_remaining_bytes')) is not None:
                remaining[source] = item['traffic_remaining_bytes']
                exhausted.discard(source)
        break
    config['source_remaining_bytes'] = remaining
    config['exhausted_sources'] = sorted(exhausted)
    config['disabled_sources'] = sorted(disabled)
    # Legacy deployments explicitly used zero, leaving backups untested forever.
    config['standby_probes_per_cycle'] = max(1, min(20, int(config.get('standby_probes_per_cycle') or 2)))
    return config


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
        connection = UnixConnection(self.config['controller_socket'], float(self.config.get('controller_timeout_seconds', 10)))
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

    def candidate_nodes(self):
        """Keep failed nodes testable, but never use a disabled/exhausted source."""
        allowed = {str(value).upper() for value in self.config.get(
            'allowed_countries', [*COUNTRY_ALIASES.keys(), 'OTHER'])}
        disabled = {str(value) for value in self.config.get('disabled_sources', [])} | exhausted_sources(self.config)
        sources = self.config.get('sources', {})
        countries = self.config.get('node_countries', {})
        return [node for node in self.config['nodes']
                if str(sources.get(node, '')) not in disabled
                and str(countries.get(node) or country_for_node(node)).upper() in allowed]

    def eligible_nodes(self):
        """Apply per-source caps after quota, country and enablement filters."""
        sources = self.config.get('sources', {})
        limits = self.config.get('source_node_limits', {})
        grouped = {}
        ranks = {'active': 0, 'ready': 1, 'probation': 2, 'unknown': 3, 'quarantined': 4}
        try:
            current = str(self.controller.current().get('now') or '')
        except Exception:
            current = ''
        records = self.state.get('nodes', {})
        for index, node in enumerate(self.candidate_nodes()):
            source = str(sources.get(node, ''))
            recorded = str(records.get(node, {}).get('status') or 'unknown')
            status = 'active' if node == current and recorded != 'quarantined' else recorded
            if self.config.get('verified_standbys_only') and status == 'ready' and not self.verified_backup(node):
                status = 'unknown'
            grouped.setdefault(source, []).append((ranks.get(status, 3), index, node))
        result = []
        for source, candidates in grouped.items():
            limit = max(1, min(int(limits.get(source, 20)), 20))
            result.extend(node for _, _, node in sorted(candidates)[:limit])
        return result

    def standbys(self, current):
        """Return the configured number of ordered failover candidates."""
        alternatives = [node for node in self.eligible_nodes() if node != current]
        records = self.state.get('nodes', {})
        if self.config.get('verified_standbys_only', False):
            alternatives = [node for node in alternatives if self.verified_backup(node)]
        alternatives.sort(key=lambda node: {'ready': 0, 'probation': 1, 'unknown': 2,
                                           'quarantined': 3}.get(records.get(node, {}).get('status'), 2))
        requested = int(self.config.get('standby_pool_size', max(1, len(alternatives))))
        if requested < 1:
            raise ValueError('standby_pool_size must be at least 1')
        return alternatives[:requested]

    def verified_backup(self, node):
        record = self.state.get('nodes', {}).get(node, {})
        if node in self.state.get('quarantine_until', {}) or record.get('status') != 'ready':
            return False
        return (record.get('consecutive_successes', 0) >= max(
                    int(self.config.get('replacement_confirmations', 2)), int(self.config.get('recovery_successes', 2)))
                and record.get('last_success_at') is not None
                and self.clock() - record['last_success_at'] <= float(self.config.get('standby_max_age_seconds', 120)))

    def replacement_samples(self, candidate, required, deadline):
        samples = []
        for attempt in range(required):
            if deadline is not None and self.clock() >= deadline:
                return None
            if attempt:
                interval = float(self.config.get('replacement_interval_seconds', 2))
                if deadline is not None and self.clock() + interval >= deadline:
                    return None
                self.sleep(interval)
            delay = self.probe(candidate)
            if deadline is not None and self.clock() >= deadline:
                return None
            if delay is None:
                break
            samples.append(delay)
        return samples

    def failover_deadline(self):
        budget = float(self.config.get('failover_budget_seconds', 0))
        return self.clock() + budget if budget > 0 else None

    def switch_confirmations(self):
        return max(1, int(self.config.get('switch_probe_confirmations',
                           1 if self.config.get('verified_standbys_only') else self.config.get('replacement_confirmations', 2))))

    def select_policy_replacement(self, current):
        """Move new connections away from a disabled/excluded source safely."""
        deadline = self.failover_deadline()
        for candidate in self.standbys(current):
            required = self.switch_confirmations()
            samples = self.replacement_samples(candidate, required, deadline)
            if samples is None:
                return self.with_standbys({'status': 'failover_budget_exhausted', 'node': current, 'changed': False}, current)
            if len(samples) != required:
                self.quarantine(candidate)
                continue
            if self.controller.current().get('now') != current:
                return {'status': 'selection_changed_externally'}
            self.state['nodes'][candidate].update(status='ready', failure_episodes=0)
            self.state.setdefault('quarantine_until', {}).pop(candidate, None)
            self.state['last_switch_at'] = self.clock()
            self.state.setdefault('switch_history', []).append(
                {'time': self.clock(), 'from': current, 'to': candidate, 'reason': 'policy'})
            self.persist(self.state)
            self.controller.select(candidate)
            self.state['nodes'][candidate]['status'] = 'active'
            return self.with_standbys({'status': 'policy_switched_new_connections_only', 'from': current,
                                      'to': candidate, 'delay_ms': round(sum(samples) / len(samples), 1)}, candidate)
        return self.with_standbys({'status': 'policy_no_healthy_candidate', 'node': current, 'changed': False}, current)

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
        alternatives = [n for n in self.candidate_nodes() if n != current
                        if records.get(n, {}).get('next_probe_at', 0) <= now]
        if self.config.get('quarantine_requires_manual_release', False):
            alternatives = [n for n in alternatives if records.get(n, {}).get('status') != 'quarantined'
                            and n not in self.state.get('quarantine_until', {})]
        # Finish a successful recovery promptly instead of leaving every node
        # with one sample while scanning a large subscription catalogue.
        alternatives.sort(key=lambda n: (records.get(n, {}).get('status') != 'probation',
                                         records.get(n, {}).get('last_probe_at', 0)))
        # Reserve one slot for a verified backup (prefer a different subscription).
        # Otherwise a large catalogue can keep every backup stale while the
        # round-robin scanner spends its entire budget on failed candidates.
        required = int(self.config.get('recovery_successes', 2))
        protected = [node for node in self.standbys(current) if node in alternatives
                     and records.get(node, {}).get('status') == 'ready'
                     and records[node].get('consecutive_successes', 0) >= required]
        if protected and (budget > 1 or not self.state.get('last_standby_protected', False)):
            sources = self.config.get('sources', {})
            protected.sort(key=lambda node: sources.get(node, '') == sources.get(current, ''))
            keeper = protected[0]
            alternatives = [keeper, *[node for node in alternatives if node != keeper]]
            self.state['last_standby_protected'] = True
        else:
            self.state['last_standby_protected'] = False
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
                if record['consecutive_successes'] >= int(self.config.get('recovery_successes', 2)):
                    record.update(status='ready', failure_episodes=0)
                    self.state.setdefault('quarantine_until', {}).pop(node, None)
                    interval = float(self.config.get('standby_interval_seconds', 180))
                else:
                    record['status'] = 'probation'
                    interval = float(self.config.get('recovery_interval_seconds', 60))
            record['next_probe_at'] = self.clock() + interval
            results.append({'node': node, 'status': record['status'], 'delay_ms': delay})
        return results

    def with_standbys(self, result, current):
        result['standbys'] = self.recover_standbys(current)
        now = self.clock()
        max_age = float(self.config.get('standby_max_age_seconds',
                        float(self.config.get('standby_interval_seconds', 180)) + float(self.config.get('interval_seconds', 60))))
        records = self.state.get('nodes', {})
        ready = [node for node in self.standbys(current)
                 if records.get(node, {}).get('status') == 'ready'
                 and records[node].get('consecutive_successes', 0) >= int(self.config.get('recovery_successes', 2))
                 and now - records[node].get('last_success_at', 0) <= max_age]
        result['standby_health'] = {'ready': ready, 'ready_count': len(ready),
                                    'required': 1, 'degraded': not bool(ready),
                                    'other_source_ready_count': sum(
                                        self.config.get('sources', {}).get(node, '') !=
                                        self.config.get('sources', {}).get(current, '') for node in ready)}
        return result

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
        exhausted = exhausted_sources(self.config)
        for node in self.config['nodes']:
            if str(self.config.get('sources', {}).get(node, '')) in exhausted:
                self.state.setdefault('nodes', {}).setdefault(node, {}).update(
                    status='exhausted', consecutive_successes=0)
        eligible = self.eligible_nodes()
        if not eligible:
            return self.with_standbys({'status': 'no_eligible_nodes', 'node': current,
                                      'exhausted_sources': sorted(exhausted_sources(self.config)),
                                      'changed': False}, current)
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
                return self.with_standbys({'status': 'healthy', 'node': current, 'delay_ms': delay,
                                          'recovered_after_failures': attempt}, current)
        activity_after = self.activity(current)
        if any(activity_after.get(key, value) > value for key, value in activity_before.items()):
            return self.with_standbys({'status': 'probe_failed_but_stream_active', 'node': current,
                                      'changed': False}, current)
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
        if self.config.get('verified_standbys_only', False):
            recovery = []
        deadline = self.failover_deadline()
        for candidate in eligible + recovery:
            required = self.switch_confirmations()
            if candidate in quarantine:
                required = max(required, int(self.config.get('recovery_successes', 2)))
            samples = self.replacement_samples(candidate, required, deadline)
            if samples is None:
                return self.with_standbys({'status': 'failover_budget_exhausted', 'node': current, 'changed': False}, current)
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
            return self.with_standbys({'status': 'switched_new_connections_only', 'from': current, 'to': candidate,
                                      'delay_ms': round(sum(samples) / len(samples), 1)}, candidate)
        return self.with_standbys({'status': 'all_unavailable', 'node': current, 'changed': False}, current)


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
    config = load_policy(config_path)
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
                          'exhausted_sources': sorted(exhausted_sources(config)),
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
                fresh = load_policy(config_path)
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
