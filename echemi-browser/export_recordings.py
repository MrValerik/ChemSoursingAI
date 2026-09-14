"""Extract complete accepted gestures from a private manual diagnostic series."""
import argparse
import json
from pathlib import Path

from recorded_motion import load_recordings


def extract(data):
    traces = []
    for sample in data.get('samples', []):
        if not any(v.get('verify_code') == 'T001' and v.get('verify_result') is True
                   for v in sample.get('verification_responses', [])):
            continue
        handle = sample.get('geometry')
        if not handle:
            continue
        for audit in sample.get('captcha', []):
            for recording in audit.get('recordings', []):
                if recording.get('dropped_events') or recording.get('limit_reached'):
                    raise ValueError('Incomplete source recording')
                events = []
                for point in recording.get('events', []):
                    if point.get('delivery') != 'delivered':
                        raise ValueError('Undelivered source event')
                    events.append({'type': point['type'], 'x': point['x'], 'y': point['y'],
                                   't': point['delivered_t_ms'] / 1000})
                    if point['type'] == 'up':
                        break  # Never replay later clicks into a page after success.
                if not events or events[-1]['type'] != 'up':
                    continue
                # Remove human idle time before the first move, keep all inter-event intervals.
                origin = events[0]['t'] - 2
                for event in events:
                    event['t'] = round(event['t'] - origin, 6)
                traces.append({'id': f"manual-{sample['round']}-{len(traces)+1}",
                               'handle': handle, 'track': {**handle, 'width': 320},
                               'events': events})
                break  # First complete gesture of each accepted sample only.
            else:
                continue
            break
    # Shortest recorded approach first; deterministic rotation on subsequent attempts.
    traces.sort(key=lambda trace: trace['events'][-1]['t'])
    return {'version': 1, 'traces': traces}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = extract(json.loads(args.source.read_text(encoding='utf-8')))
    # Exclusive creation avoids overwriting the currently installed library.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, separators=(',', ':'))
    traces, digest = load_recordings(args.output)
    args.output.chmod(0o600)
    print(json.dumps({'traces': len(traces), 'library_id': digest}))


if __name__ == '__main__':
    main()
