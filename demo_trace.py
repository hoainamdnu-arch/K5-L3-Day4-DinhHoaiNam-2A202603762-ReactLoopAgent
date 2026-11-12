"""Đọc lại trace từ artifact để demo offline, không gọi API hoặc đọc .env."""
import argparse
import json
from pathlib import Path

from config import configure_console


def main():
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='eval_after.json')
    parser.add_argument('--case', default='H1')
    args = parser.parse_args()
    try:
        report = json.loads(Path(args.report).read_text(encoding='utf-8'))
        case = next(c for c in report['cases'] if c['id'] == args.case.upper())
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        parser.error(f'Không đọc được report/case: {type(exc).__name__}')
    result = case['result']
    print(f"{case['id']}: {case['question']}")
    print(f"Status: {result['status']}; điểm: {case['grade'].get('score')}")
    for event in result['trace']:
        step = event.get('step', '-')
        if event['event'] == 'tool':
            observation = event['result']
            print(f"  {step}: {event['name']} {json.dumps(event['arguments'], ensure_ascii=False)}")
            print('     ' + json.dumps({k: observation[k] for k in
                  ('provider', 'id', 'status', 'captured_at', 'url', 'error', 'offset',
                   'next_offset', 'total_updates', 'updates_total', 'retriable', 'retry_exhausted') if k in observation},
                  ensure_ascii=False))
        else:
            print(f"  {step}: {event['event']} {event.get('finish', '')}")
            if 'incidents' in event:
                print('     ' + json.dumps(event['incidents'], ensure_ascii=False))
    print('\n' + result['answer'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
