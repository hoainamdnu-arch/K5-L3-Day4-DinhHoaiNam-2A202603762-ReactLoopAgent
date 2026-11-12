"""Checkpoint không được đổi điểm 0, trộn cấu hình hoặc báo điểm toàn bộ sớm."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'evals'))
import run_evals


class EvalResumeTests(unittest.TestCase):
    def item(self, score):
        return {'id': 'H4', 'result': {'status': 'completed', 'answer': '', 'trace': [], 'usage': []},
                'grade': {'status': 'scored' if score is not None else 'unscored', 'score': score}}

    def run_main(self, path, resume=False):
        argv = ['run_evals.py', '--provider', 'groq', '--model', 'test', '--judge-provider', 'groq',
                '--judge-model', 'test', '--case', 'H4', '--output', str(path)]
        if resume:
            argv.append('--resume')
        with patch.object(sys, 'argv', argv), patch('run_evals.load_env'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_evals.main()

    def test_resume_preserves_scored_zero_without_another_api_call(self):
        with tempfile.TemporaryDirectory() as folder, patch('run_evals.run_case', return_value=self.item(0)) as run:
            path = Path(folder) / 'report.json'
            self.assertEqual(self.run_main(path), 0)
            run.reset_mock()
            self.assertEqual(self.run_main(path, resume=True), 0)
            run.assert_not_called()
            report = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(report['summary']['score_0_100'], 0)

    def test_resume_retries_unscored_case(self):
        with tempfile.TemporaryDirectory() as folder, patch('run_evals.run_case', return_value=self.item(None)) as run:
            path = Path(folder) / 'report.json'
            self.assertEqual(self.run_main(path), 1)
            run.reset_mock()
            run.return_value = self.item(80)
            self.assertEqual(self.run_main(path, resume=True), 0)
            run.assert_called_once()
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['summary']['score_0_100'], 80)

    def test_resume_rejects_changed_config_or_source(self):
        with tempfile.TemporaryDirectory() as folder, patch('run_evals.run_case', return_value=self.item(80)) as run:
            path = Path(folder) / 'report.json'
            self.run_main(path)
            original = path.read_text(encoding='utf-8')
            for field in ('config', 'provenance'):
                with self.subTest(field=field):
                    report = json.loads(original)
                    report[field]['changed'] = True
                    path.write_text(json.dumps(report), encoding='utf-8')
                    run.reset_mock()
                    with self.assertRaises(SystemExit):
                        self.run_main(path, resume=True)
                    run.assert_not_called()

    def test_checkpoint_before_first_case_has_no_overall_score(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'

            def inspect_checkpoint(case, args):
                report = json.loads(path.read_text(encoding='utf-8'))
                self.assertIsNone(report['summary']['score_0_100'])
                self.assertEqual(report['summary']['total_cases'], 1)
                self.assertEqual(report['progress']['finished'], 0)
                return self.item(90)

            with patch('run_evals.run_case', side_effect=inspect_checkpoint):
                self.assertEqual(self.run_main(path), 0)


if __name__ == '__main__':
    unittest.main()
