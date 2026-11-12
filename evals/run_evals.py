"""Chạy và chấm eval. expected chỉ cung cấp cho judge, không gửi cho agent."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from config import configure_console, load_env
from providers import CONFIG, Provider
from scenarios import ScenarioTools
from grading import grade_case, summarize, bonus_points
from bonus.tools import BonusTools


def get_provenance(bonus=False):
    """Xác nhận resume cùng code/data/generation settings; không lưu credentials."""
    root = Path(__file__).resolve().parents[1]
    try:
        agent_source = Path(inspect.getsourcefile(run_agent))
    except (TypeError, OSError):
        agent_source = root / 'agent.py'
    files = [agent_source, root / 'providers.py', root / 'status_tools.py',
             root / 'config.py', root / 'evals/scenarios.py', root / 'evals/grading.py',
             root / ('bonus/cases.json' if bonus else 'evals/cases.json')]
    files.extend(sorted((root / 'fixtures').glob('*.json')))
    if bonus:
        files += [root / 'bonus/tools.py', *sorted((root / 'bonus/fixtures').glob('*.json'))]
    try:
        case_source = inspect.getsource(run_case)
    except (TypeError, OSError):
        case_source = 'test-double'
    return {
        'files': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'runner_case_sha256': hashlib.sha256(case_source.encode()).hexdigest(),
        'generation': {key: (hashlib.sha256(os.getenv(key, '').encode()).hexdigest()
                            if key == 'GROQ_OUTPUT_HINT' else os.getenv(key, '')) for key in (
            'PROVIDER_TIMEOUT', 'PROVIDER_MAX_RETRIES', 'PROVIDER_RATE_RETRIES',
            'GEMINI_REASONING_EFFORT', 'GROQ_MAX_OUTPUT_TOKENS', 'GROQ_OUTPUT_HINT',
            'GROQ_DISABLE_TOOL_VALIDATION', 'GROQ_TOOL_CALL_RETRIES',
        )},
    }


def load_resume(path, config, provenance):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    if report.get('config') != config or report.get('provenance') != provenance:
        raise ValueError('Không resume: cấu hình, code hoặc snapshot khác report đã lưu.')
    cases = {case['id']: case for case in report['cases']}
    if len(cases) != len(report['cases']):
        raise ValueError('Không resume: report có ID case trùng nhau.')
    return cases, report.get('seed_reports', [])


def run_case(case, args):
    try:
        result = run_agent(case['question'], Provider(args.provider, args.model),
                           (BonusTools('snapshot', scenario=case.get('scenario', 'normal')) if getattr(args, 'bonus', False) else ScenarioTools(case.get('scenario', 'normal'), case.get('fail', False))), max_steps=args.max_steps)
    except Exception as exc:
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [], 'error': f'{type(exc).__name__}: {str(exc)[:200]}'}
    if not isinstance(result, dict) or not isinstance(result.get('trace', []), list):
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [], 'error': 'run_agent trả kết quả không hợp lệ.'}
    try:
        judge = Provider(args.judge_provider, args.judge_model)
        grade = grade_case(case, result, judge)
    except Exception as exc:
        grade = {'score': None, 'status': 'unscored', 'reason': str(exc), 'judge_usage': {}}
    return {'id': case['id'], 'question': case['question'], 'expected': case['expected'],
            'scenario': case.get('scenario', 'normal'), 'provider': args.provider, 'model': args.model,
            'judge_provider': args.judge_provider, 'judge_model': args.judge_model, 'result': result, 'grade': grade}


def main():
    configure_console()
    api_key_from_shell = bool(os.getenv('API_KEY'))
    load_env()
    parser = argparse.ArgumentParser(description='Chạy agent và chấm eval 0–100')
    choices = list(CONFIG)
    parser.add_argument('--provider', choices=choices, default=os.getenv('PROVIDER', 'openai'))
    parser.add_argument('--model', default=os.getenv('MODEL') or os.getenv('API_MODEL'))
    parser.add_argument('--judge-provider', choices=choices, default=os.getenv('JUDGE_PROVIDER'))
    parser.add_argument('--judge-model', default=os.getenv('JUDGE_MODEL'))
    parser.add_argument('--bonus', action='store_true', help='Chạy bộ bonus riêng; không ảnh hưởng điểm chính')
    parser.add_argument('--case', help='Ví dụ E2 hoặc H1; bỏ trống để chạy toàn bộ')
    parser.add_argument('--max-steps', type=int, default=None, help='Giới hạn model calls mỗi case: mặc định 8, bonus 16')
    parser.add_argument('--workers', type=int, default=1, help='Số case chạy đồng thời (mặc định 1)')
    parser.add_argument('--output', default='eval_results.json')
    parser.add_argument('--resume', action='store_true', help='Giữ mọi case đã chấm (kể cả 0); chỉ chạy lại case thiếu/unscored, cùng code/cấu hình')
    args = parser.parse_args()
    args.judge_provider = args.judge_provider or args.provider
    args.judge_model = args.judge_model or args.model
    if not args.model or not args.judge_model:
        parser.error('Thiếu --model, MODEL hoặc API_MODEL (có thể đặt trong .env ở gốc repo)')
    args.max_steps = args.max_steps if args.max_steps is not None else (16 if args.bonus else 8)
    if args.max_steps < 1 or not 1 <= args.workers <= 8:
        parser.error('--max-steps phải >= 1 và --workers phải từ 1 đến 8')
    cases_path = (Path(__file__).resolve().parents[1] / 'bonus' / 'cases.json') if args.bonus else (Path(__file__).parent / 'cases.json')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    selected = [c for c in cases if not args.case or c['id'] == args.case.upper()]
    if not selected:
        parser.error('Không tìm thấy case')
    config = {'provider': args.provider, 'model': args.model,
              'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
              'max_steps': args.max_steps, 'dataset': 'bonus' if args.bonus else 'core'}
    provenance = get_provenance(args.bonus)
    collected, seeds = {}, []
    if args.resume:
        try:
            collected, seeds = load_resume(args.output, config, provenance)
        except (OSError, ValueError, KeyError) as exc:
            parser.error(str(exc))
        selected_ids = {c['id'] for c in selected}
        if not set(collected).issubset(selected_ids):
            parser.error('Không resume: report chứa case ngoài tập đang chọn.')

    def save_report():
        ordered = [collected[c['id']] for c in selected if c['id'] in collected]
        summary = summarize(ordered)
        if len(ordered) < len(selected):
            partial = summary['score_0_100'] if summary['score_0_100'] is not None else summary.get('partial_mean_0_100')
            summary.update(total_cases=len(selected), unscored_cases=len(selected) - summary['scored_cases'],
                           score_0_100=None, partial_mean_0_100=partial)
        if args.bonus:
            summary['bonus_points_0_15'] = bonus_points(summary['score_0_100'], not args.case and len(selected) == len(cases))
        report = {'summary': summary, 'config': config, 'cases': ordered, 'provenance': provenance,
                  'progress': {'finished': len(ordered), 'requested': len(selected)}}
        if seeds:
            report['seed_reports'] = seeds
        output = Path(args.output)
        temporary = output.with_name(output.name + '.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(output)
        return report

    def run_and_print(case):
        cached = collected.get(case['id'])
        if cached and cached.get('grade', {}).get('status') == 'scored':
            print(f"{case['id']}: {cached['grade'].get('score')} (scored, resume)", flush=True)
            return cached
        print(f"Đang chạy {case['id']}", flush=True)
        item = run_case(case, args)
        print(f"{case['id']}: {item['grade'].get('score')} ({item['grade']['status']})", flush=True)
        if item['grade']['status'] == 'unscored':
            reason = item.get('result', {}).get('error') or item['grade'].get('reason', 'Chưa rõ nguyên nhân.')
            print(f"{case['id']} lỗi: {reason}", flush=True)
            if args.provider == 'openai' and os.getenv('API_KEY'):
                source = 'biến môi trường shell (ưu tiên hơn .env)' if api_key_from_shell else '.env'
                print(f"Nguồn API_KEY: {source}", flush=True)
        return item
    save_report()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for item in pool.map(run_and_print, selected):
            collected[item['id']] = item
            save_report()
    report = save_report()
    summary = report['summary']
    if args.bonus:
        full_run = not args.case and len(selected) == len(cases)
        report['summary']['bonus_points_0_15'] = bonus_points(summary['score_0_100'], full_run)
        print(f"Điểm bonus tham khảo: {summary['bonus_points_0_15']}/15 (chỉ tính khi chấm đủ toàn bộ case).")
    print(f"Điểm: {summary['score_0_100']}/100; đã chấm {summary['scored_cases']}/{summary['total_cases']} case. File: {args.output}")
    if summary['score_0_100'] is None:
        print('Chưa có điểm tổng do có case chưa được chấm; xem reason trong file.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
