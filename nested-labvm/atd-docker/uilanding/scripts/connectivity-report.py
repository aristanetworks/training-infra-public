#!/usr/bin/env python3
"""
Connectivity Report Generator for Arista Training Labs

Reads the local connectivity JSONL log file and generates an ASCII report
showing session history, gRPC health, latency, and connectivity analysis.

Usage:
    python3 connectivity-report.py                      # Default log path
    python3 connectivity-report.py /path/to/file.jsonl   # Custom path
    python3 connectivity-report.py --last 2h             # Last 2 hours
    python3 connectivity-report.py --last 30m            # Last 30 minutes
    python3 connectivity-report.py --session abc123      # Single session
"""

import json
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict

DEFAULT_LOG_PATH = '/var/log/atd/connectivity.jsonl'
SPARK_CHARS = '▁▂▃▄▅▆▇█'


def parse_duration(duration_str):
    """Parse a duration string like '2h', '30m', '1d' into timedelta"""
    if not duration_str:
        return None
    unit = duration_str[-1].lower()
    try:
        value = int(duration_str[:-1])
    except ValueError:
        return None
    if unit == 'h':
        return timedelta(hours=value)
    elif unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'd':
        return timedelta(days=value)
    return None


def parse_ts(ts_str):
    """Parse ISO timestamp string to datetime"""
    try:
        # Handle microseconds
        if '.' in ts_str:
            return datetime.strptime(ts_str.replace('Z', ''), '%Y-%m-%dT%H:%M:%S.%f')
        return datetime.strptime(ts_str.replace('Z', ''), '%Y-%m-%dT%H:%M:%S')
    except (ValueError, TypeError):
        return None


def load_events(log_path, time_filter=None, session_filter=None):
    """Load and filter events from JSONL file"""
    events = []
    cutoff = datetime.utcnow() - time_filter if time_filter else None

    if not os.path.exists(log_path):
        print("Error: Log file not found: {}".format(log_path))
        print("Make sure uilanding is running and generating connectivity logs.")
        sys.exit(1)

    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts = parse_ts(entry.get('ts', ''))
                if ts is None:
                    continue
                entry['_ts'] = ts

                if cutoff and ts < cutoff:
                    continue
                if session_filter:
                    sid = entry.get('labels', {}).get('session_id', '')
                    if session_filter not in sid:
                        continue

                events.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue

    return sorted(events, key=lambda e: e['_ts'])


def format_duration(seconds):
    """Format seconds into human-readable duration"""
    if seconds is None:
        return '--'
    seconds = float(seconds)
    if seconds < 60:
        return '{:.0f}s'.format(seconds)
    if seconds < 3600:
        return '{:.0f}m {:.0f}s'.format(seconds // 60, seconds % 60)
    hours = seconds // 3600
    mins = (seconds % 3600) // 60
    return '{:.0f}h {:.0f}m'.format(hours, mins)


def sparkline(values, width=40):
    """Generate an ASCII sparkline from a list of numeric values"""
    if not values:
        return '(no data)'
    filtered = [v for v in values if v is not None and v >= 0]
    if not filtered:
        return '(no valid data)'

    min_val = min(filtered)
    max_val = max(filtered)
    val_range = max_val - min_val if max_val != min_val else 1

    # Resample to fit width
    if len(filtered) > width:
        step = len(filtered) / width
        sampled = []
        for i in range(width):
            idx = int(i * step)
            sampled.append(filtered[idx])
        filtered = sampled

    result = ''
    for v in filtered:
        idx = int((v - min_val) / val_range * (len(SPARK_CHARS) - 1))
        result += SPARK_CHARS[idx]

    return result + '  ({:.0f}-{:.0f}ms)'.format(min_val, max_val)


def grpc_timeline(events, width=60):
    """Generate ASCII timeline of gRPC check results"""
    grpc_events = [e for e in events
                   if e.get('labels', {}).get('action') == 'grpc_check']
    if not grpc_events:
        return '(no gRPC check data)'

    internal = []
    client = []
    for e in grpc_events:
        source = e.get('labels', {}).get('source', '')
        status = e.get('labels', {}).get('status', '')
        if source == 'internal':
            internal.append(status)
        elif source == 'client':
            client.append(status)

    def status_to_char(statuses, w):
        if not statuses:
            return '(no data)'
        if len(statuses) > w:
            step = len(statuses) / w
            sampled = [statuses[int(i * step)] for i in range(w)]
        else:
            sampled = statuses
        result = ''
        for s in sampled:
            if s == 'ok':
                result += '+'
            elif s in ('failed', 'error', 'unavailable', 'unreachable'):
                result += 'X'
            elif s in ('timeout', 'auth_rejected'):
                result += '?'
            else:
                result += '.'
        return result

    lines = []
    lines.append('  Internal: [{}]'.format(status_to_char(internal, width)))
    lines.append('  Client:   [{}]'.format(status_to_char(client, width)))
    lines.append('  Legend: + = ok, X = failed, ? = timeout/auth, . = unknown')
    return '\n'.join(lines)


def build_sessions(events):
    """Group events by session ID and extract session data"""
    sessions = defaultdict(lambda: {
        'start': None, 'end': None, 'duration': None,
        'reconnects': 0, 'missed_pongs': 0, 'rtt_values': [],
        'client_ip': '', 'user_agent': '', 'client_id': '',
        'grpc_internal': {'ok': 0, 'fail': 0},
        'grpc_client': {'ok': 0, 'fail': 0},
        'external_checks': {'ok': 0, 'fail': 0},
        'events': []
    })

    for e in events:
        labels = e.get('labels', {})
        sid = labels.get('session_id', '')
        action = labels.get('action', '')
        if not sid:
            continue

        s = sessions[sid]
        s['events'].append(e)

        if action == 'session_start':
            s['start'] = e['_ts']
            s['client_ip'] = labels.get('client_ip', '')
            s['user_agent'] = labels.get('user_agent', '')
            s['client_id'] = labels.get('client_id', '')
            s['reconnects'] = int(labels.get('reconnect_count', 0))

        elif action == 'session_end':
            s['end'] = e['_ts']
            s['duration'] = labels.get('duration_seconds')
            cid = labels.get('client_id', '')
            if cid and not s['client_id']:
                s['client_id'] = cid
            # session_end has the definitive missed_pongs count
            try:
                mp = int(labels.get('missed_pongs', 0))
                if mp > s['missed_pongs']:
                    s['missed_pongs'] = mp
            except (ValueError, TypeError):
                pass

        elif action == 'missed_pongs':
            s['missed_pongs'] = max(s['missed_pongs'], int(labels.get('missed_pongs', 0)))

        elif action == 'session_summary':
            # Pick up client_id from summaries (arrives after hello)
            cid = labels.get('client_id', '')
            if cid and not s['client_id']:
                s['client_id'] = cid
            rtt = labels.get('last_rtt_ms', '')
            if rtt and rtt != 'None' and rtt != '':
                try:
                    s['rtt_values'].append(float(rtt))
                except (ValueError, TypeError):
                    pass
            # session_summary carries running missed_pongs count
            try:
                mp = int(labels.get('missed_pongs', 0))
                if mp > s['missed_pongs']:
                    s['missed_pongs'] = mp
            except (ValueError, TypeError):
                pass

        elif action == 'periodic_summary':
            cid = labels.get('client_id', '')
            if cid and not s['client_id']:
                s['client_id'] = cid
            rtt = labels.get('ws_latency_ms', '')
            if rtt and rtt != 'None' and rtt != '':
                try:
                    s['rtt_values'].append(float(rtt))
                except (ValueError, TypeError):
                    pass

            ext = labels.get('external_check', '')
            if ext == 'ok':
                s['external_checks']['ok'] += 1
            elif ext in ('failed', 'timeout'):
                s['external_checks']['fail'] += 1

        elif action == 'grpc_check':
            cid = labels.get('client_id', '')
            if cid and not s['client_id']:
                s['client_id'] = cid
            source = labels.get('source', '')
            status = labels.get('status', '')
            if source == 'internal':
                if status == 'ok':
                    s['grpc_internal']['ok'] += 1
                else:
                    s['grpc_internal']['fail'] += 1
            elif source == 'client':
                if status == 'ok':
                    s['grpc_client']['ok'] += 1
                else:
                    s['grpc_client']['fail'] += 1

        elif action == 'reconnect':
            s['reconnects'] = int(labels.get('reconnect_count', s['reconnects']))

    return dict(sessions)


def generate_verdict(sessions, events):
    """Generate an overall connectivity verdict"""
    total_sessions = len(sessions)
    total_reconnects = sum(s['reconnects'] for s in sessions.values())
    total_missed = sum(s['missed_pongs'] for s in sessions.values())

    all_rtt = []
    for s in sessions.values():
        all_rtt.extend(s['rtt_values'])

    grpc_internal_fail = sum(s['grpc_internal']['fail'] for s in sessions.values())
    grpc_internal_ok = sum(s['grpc_internal']['ok'] for s in sessions.values())
    grpc_client_fail = sum(s['grpc_client']['fail'] for s in sessions.values())
    grpc_client_ok = sum(s['grpc_client']['ok'] for s in sessions.values())
    ext_fail = sum(s['external_checks']['fail'] for s in sessions.values())
    ext_ok = sum(s['external_checks']['ok'] for s in sessions.values())

    issues = []

    if total_reconnects == 0:
        issues.append('No reconnects detected - stable connections')
    elif total_reconnects <= 3:
        issues.append('{} reconnect(s) - minor instability'.format(total_reconnects))
    else:
        issues.append('{} reconnects - significant instability'.format(total_reconnects))

    if total_missed > 0:
        issues.append('{} missed pong warnings - degraded connections detected'.format(total_missed))

    if all_rtt:
        avg_rtt = sum(all_rtt) / len(all_rtt)
        if avg_rtt > 500:
            issues.append('High average latency: {:.0f}ms'.format(avg_rtt))
        elif avg_rtt > 200:
            issues.append('Elevated latency: {:.0f}ms average'.format(avg_rtt))
        else:
            issues.append('Latency normal: {:.0f}ms average'.format(avg_rtt))

    # gRPC comparison
    if grpc_internal_ok > 0 and grpc_client_fail > 0 and grpc_client_ok == 0:
        issues.append('FIREWALL/VPN LIKELY: Internal gRPC OK but client gRPC failing')
    elif grpc_internal_fail > 0 and grpc_client_fail > 0:
        issues.append('CVP gRPC issue: Both internal and client checks failing')
    elif grpc_client_fail > 0:
        issues.append('Client gRPC intermittent: {} ok, {} failed'.format(grpc_client_ok, grpc_client_fail))

    if ext_fail > 0 and ext_ok == 0:
        issues.append('External connectivity failed - client has no internet')
    elif ext_fail > 0:
        issues.append('External connectivity intermittent: {} ok, {} failed'.format(ext_ok, ext_fail))

    return issues


def print_report(events, sessions):
    """Print the full ASCII report"""
    if not events:
        print("No connectivity events found in the log file.")
        return

    time_range_start = events[0]['_ts'].strftime('%Y-%m-%d %H:%M:%S')
    time_range_end = events[-1]['_ts'].strftime('%Y-%m-%d %H:%M:%S')

    # Try to get hostname from events
    hostname = ''
    for e in events:
        h = e.get('labels', {}).get('lab_hostname', '')
        if h:
            hostname = h
            break

    # ============ HEADER ============
    print('')
    print('=' * 72)
    print('  CONNECTIVITY REPORT')
    print('=' * 72)
    print('  Lab:        {}'.format(hostname or '(unknown)'))
    print('  Time Range: {} to {}'.format(time_range_start, time_range_end))
    print('  Sessions:   {}'.format(len(sessions)))
    print('  Events:     {}'.format(len(events)))
    print('=' * 72)

    # ============ VERDICT ============
    verdict = generate_verdict(sessions, events)
    print('')
    print('  VERDICT')
    print('  ' + '-' * 68)
    for v in verdict:
        print('  * {}'.format(v))
    print('')

    # ============ SESSION TABLE ============
    # Group sessions by client_id for continuity across page refreshes
    client_groups = defaultdict(list)
    ungrouped = []
    for sid, s in sorted(sessions.items(), key=lambda x: x[1]['start'] or datetime.min):
        cid = s.get('client_id', '')
        if cid:
            client_groups[cid].append((sid, s))
        else:
            ungrouped.append((sid, s))

    print('  USERS ({} unique client{})'.format(
        len(client_groups) + len(ungrouped),
        's' if (len(client_groups) + len(ungrouped)) != 1 else ''))
    print('  ' + '-' * 68)
    header = '  {:<10} {:<20} {:<10} {:<6} {:<6} {:<10}'.format(
        'Session', 'Start', 'Duration', 'Recon', 'Miss', 'Avg RTT')

    for cid, group in sorted(client_groups.items(), key=lambda x: x[1][0][1]['start'] or datetime.min):
        first_session = group[0][1]
        ip = first_session.get('client_ip', '?')
        ua = first_session.get('user_agent', '')
        ua_short = (ua[:50] + '...') if len(ua) > 50 else ua or '--'
        total_duration = 0
        total_reconnects = 0
        total_missed = 0
        all_rtt = []
        for _, s in group:
            if s['duration']:
                try:
                    total_duration += float(s['duration'])
                except (ValueError, TypeError):
                    pass
            total_reconnects += s['reconnects']
            total_missed += s['missed_pongs']
            all_rtt.extend(s['rtt_values'])

        # Check if any session in group is still active
        any_active = any(s['duration'] is None for _, s in group)

        print('')
        print('  Client: {}  IP: {}'.format(cid[:16], ip))
        print('  UA: {}'.format(ua_short))
        print('  Total: {} session(s), {} total, {} reconnects, {} missed pongs'.format(
            len(group),
            'active' if any_active else format_duration(total_duration),
            total_reconnects, total_missed))
        if all_rtt:
            print('  Avg RTT: {:.0f}ms'.format(sum(all_rtt) / len(all_rtt)))
        print('  ' + header)
        print('  ' + '-' * 68)

        for sid, s in group:
            short_id = sid[:8]
            start = s['start'].strftime('%Y-%m-%d %H:%M') if s['start'] else '--'
            duration = format_duration(s['duration']) if s['duration'] else 'active'
            reconnects = str(s['reconnects'])
            missed = str(s['missed_pongs'])
            avg_rtt = '{:.0f}ms'.format(sum(s['rtt_values']) / len(s['rtt_values'])) if s['rtt_values'] else '--'
            print('  {:<10} {:<20} {:<10} {:<6} {:<6} {:<10}'.format(
                short_id, start, duration, reconnects, missed, avg_rtt))

    # Show ungrouped sessions (no client_id — old data before this feature)
    if ungrouped:
        print('')
        print('  SESSIONS WITHOUT CLIENT ID (pre-upgrade data)')
        print('  ' + header)
        print('  ' + '-' * 68)
        for sid, s in ungrouped:
            short_id = sid[:8]
            start = s['start'].strftime('%Y-%m-%d %H:%M') if s['start'] else '--'
            duration = format_duration(s['duration']) if s['duration'] else 'active'
            reconnects = str(s['reconnects'])
            missed = str(s['missed_pongs'])
            avg_rtt = '{:.0f}ms'.format(sum(s['rtt_values']) / len(s['rtt_values'])) if s['rtt_values'] else '--'
            print('  {:<10} {:<20} {:<10} {:<6} {:<6} {:<10}'.format(
                short_id, start, duration, reconnects, missed, avg_rtt))
            if s['client_ip']:
                print('             IP: {}'.format(s['client_ip']))

    print('')

    # ============ gRPC HEALTH ============
    print('  gRPC HEALTH TIMELINE')
    print('  ' + '-' * 68)
    print('  (Each character = one check result, left=oldest, right=newest)')
    print(grpc_timeline(events))
    print('')

    # ============ LATENCY ============
    print('  LATENCY')
    print('  ' + '-' * 68)
    for sid, s in sorted(sessions.items(), key=lambda x: x[1]['start'] or datetime.min):
        if s['rtt_values']:
            short_id = sid[:8]
            print('  [{}] {}'.format(short_id, sparkline(s['rtt_values'])))
    if not any(s['rtt_values'] for s in sessions.values()):
        print('  (no latency data)')
    print('')

    # ============ EXTERNAL CONNECTIVITY ============
    print('  EXTERNAL CONNECTIVITY (arista.com)')
    print('  ' + '-' * 68)
    total_ext_ok = sum(s['external_checks']['ok'] for s in sessions.values())
    total_ext_fail = sum(s['external_checks']['fail'] for s in sessions.values())
    total_ext = total_ext_ok + total_ext_fail
    if total_ext > 0:
        pct = (total_ext_ok / total_ext) * 100
        bar_width = 40
        filled = int(pct / 100 * bar_width)
        bar = '[' + '#' * filled + '-' * (bar_width - filled) + ']'
        print('  {} {:.1f}% success ({} ok / {} total)'.format(bar, pct, total_ext_ok, total_ext))
    else:
        print('  (no external check data)')
    print('')

    # ============ RECONNECT ANALYSIS ============
    reconnect_events = [e for e in events
                        if e.get('labels', {}).get('action') == 'reconnect']
    print('  RECONNECT EVENTS')
    print('  ' + '-' * 68)
    if reconnect_events:
        for e in reconnect_events:
            labels = e.get('labels', {})
            ts = e['_ts'].strftime('%H:%M:%S')
            gap = labels.get('reconnect_gap_seconds', '?')
            count = labels.get('reconnect_count', '?')
            ip = labels.get('client_ip', '?')
            print('  {} | gap={:>6}s | count={} | ip={}'.format(ts, gap, count, ip))
    else:
        print('  No reconnect events detected')
    print('')

    # ============ DETAILED SESSION EVENTS ============
    for sid, s in sorted(sessions.items(), key=lambda x: x[1]['start'] or datetime.min):
        short_id = sid[:8]
        print('  SESSION [{}] EVENTS'.format(short_id))
        print('  ' + '-' * 68)

        for e in s['events'][-30:]:  # Last 30 events per session
            ts = e['_ts'].strftime('%H:%M:%S')
            action = e.get('labels', {}).get('action', '?')
            level = e.get('level', '?')
            source = e.get('labels', {}).get('source', '')

            # Build detail string from interesting labels
            detail_parts = []
            labels = e.get('labels', {})
            for key in ('status', 'duration_seconds', 'missed_pongs',
                        'last_rtt_ms', 'reconnect_gap_seconds', 'ws_latency_ms',
                        'grpc_status', 'external_check', 'uptime_percent',
                        'network_type', 'effective_type'):
                val = labels.get(key, '')
                if val and val != 'None' and val != '':
                    detail_parts.append('{}={}'.format(key, val))

            detail = ' '.join(detail_parts[:4])  # Max 4 fields
            source_tag = ' [{}]'.format(source) if source else ''
            print('  {} {:>5} {:<22}{} {}'.format(ts, level, action, source_tag, detail))

        print('')

    print('=' * 72)
    print('  END OF REPORT')
    print('=' * 72)
    print('')


def filter_short_sessions(sessions, min_duration=30):
    """Remove sessions shorter than min_duration seconds (page reload noise)"""
    filtered = {}
    skipped = 0
    for sid, s in sessions.items():
        duration = s.get('duration')
        if duration is not None:
            try:
                if float(duration) < min_duration:
                    skipped += 1
                    continue
            except (ValueError, TypeError):
                pass
        filtered[sid] = s
    return filtered, skipped


def main():
    log_path = DEFAULT_LOG_PATH
    time_filter = None
    session_filter = None
    min_duration = 30  # Default: hide sessions shorter than 30s
    show_all = False

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--last' and i + 1 < len(args):
            time_filter = parse_duration(args[i + 1])
            if not time_filter:
                print("Invalid duration: {}. Use format like 2h, 30m, 1d".format(args[i + 1]))
                sys.exit(1)
            i += 2
        elif args[i] == '--session' and i + 1 < len(args):
            session_filter = args[i + 1]
            i += 2
        elif args[i] == '--min-duration' and i + 1 < len(args):
            try:
                min_duration = int(args[i + 1])
            except ValueError:
                print("Invalid min-duration: {}. Use seconds.".format(args[i + 1]))
                sys.exit(1)
            i += 2
        elif args[i] == '--all':
            show_all = True
            i += 1
        elif args[i] == '--help' or args[i] == '-h':
            print(__doc__)
            print("Additional options:")
            print("  --min-duration N   Hide sessions shorter than N seconds (default: 30)")
            print("  --all              Show all sessions including short-lived ones")
            sys.exit(0)
        elif not args[i].startswith('-'):
            log_path = args[i]
            i += 1
        else:
            print("Unknown option: {}".format(args[i]))
            print("Use --help for usage.")
            sys.exit(1)

    events = load_events(log_path, time_filter, session_filter)
    sessions = build_sessions(events)

    if not show_all:
        sessions, skipped = filter_short_sessions(sessions, min_duration)
        if skipped > 0:
            print("  (Filtered {} short session(s) < {}s — use --all to show)".format(skipped, min_duration))

    print_report(events, sessions)


if __name__ == '__main__':
    main()
