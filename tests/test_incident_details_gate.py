"""Kiểm tra bước đọc bằng chứng bằng provider giả; không chấm năng lực LLM."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from status_tools import StatusTools
from test_lab import FakeProvider, final, tool_call


class IncidentDetailsGateTests(unittest.TestCase):
    def setUp(self):
        self.tools = StatusTools()
        self.status_args = {'provider': 'cloudflare', 'component': 'Cache Purge'}
        status = self.tools.execute('get_status', self.status_args)
        self.incident = next(i for i in status['incidents'] if i['status'] == 'identified')
        self.detail_args = {'provider': 'cloudflare', 'incident_id': self.incident['id']}
        self.details = self.tools.execute('get_incident_details', self.detail_args)

    def status_call(self):
        return tool_call('status', 'get_status', json.dumps(self.status_args))

    def detail_call(self, provider='cloudflare'):
        args = dict(self.detail_args, provider=provider)
        return tool_call('details', 'get_incident_details', json.dumps(args))

    def test_referenced_incident_needs_details_before_completion(self):
        draft = final(self.incident['name'].upper() + ': ảnh hưởng mọi khách hàng.')
        corrected = 'Update đầu tiên loại trừ non-FedRAMP. ' + self.details['url']
        provider = FakeProvider([
            self.status_call(), draft, self.detail_call(), final(corrected),
        ])

        result = run_agent('Điều tra phạm vi ảnh hưởng', provider, self.tools, max_steps=4)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], corrected)
        gate = next(e for e in result['trace'] if e['event'] == 'validation_missing_details')
        self.assertEqual(gate['step'], 2)
        self.assertEqual(gate['incidents'], [self.detail_args])
        self.assertEqual(len(provider.histories), 4)
        self.assertIs(provider.histories[2][-2], draft['message'])
        self.assertEqual(provider.histories[2][-1]['role'], 'user')
        self.assertIn(self.incident['id'], provider.histories[2][-1]['content'])
        detail_message = provider.histories[3][-1]
        self.assertEqual(detail_message['tool_call_id'], 'details')
        self.assertEqual(json.loads(detail_message['content']), self.details)

    def test_unread_incident_at_budget_limit_is_not_completed(self):
        provider = FakeProvider([self.status_call(), final(self.incident['id'])])

        result = run_agent('Điều tra', provider, self.tools, max_steps=2)

        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(len(provider.histories), 2)
        self.assertEqual(result['trace'][-1]['event'], 'validation_missing_details')

    def test_invalid_json_does_not_count_as_reading_details(self):
        provider = FakeProvider([
            self.status_call(),
            tool_call('bad', 'get_incident_details', '{'),
            final(self.incident['id']),
        ])

        result = run_agent('Điều tra', provider, self.tools, max_steps=3)

        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['trace'][-1]['event'], 'validation_missing_details')
        self.assertEqual(json.loads(provider.histories[2][-1]['content'])['error'], 'INVALID_ARGUMENTS')

    def test_failed_detail_attempt_allows_reporting_missing_evidence(self):
        execute = self.tools.execute

        def with_timeout(name, arguments):
            if name == 'get_incident_details':
                raise TimeoutError('timeout giả lập')
            return execute(name, arguments)

        answer = self.incident['name'] + ': chưa đọc được updates do timeout, chưa rõ phạm vi.'
        provider = FakeProvider([self.status_call(), self.detail_call(), final(answer)])
        with patch.object(self.tools, 'execute', side_effect=with_timeout):
            result = run_agent('Điều tra', provider, self.tools, max_steps=3)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['answer'], answer)
        self.assertFalse(any(e['event'].startswith('validation_') for e in result['trace']))
        detail_event = next(e for e in result['trace'] if e.get('name') == 'get_incident_details')
        self.assertEqual(detail_event['result']['error'], 'TimeoutError')

    def test_detail_attempt_for_another_provider_does_not_bypass_gate(self):
        provider = FakeProvider([
            self.status_call(), self.detail_call(provider='github'), final(self.incident['id']),
        ])

        result = run_agent('Điều tra', provider, self.tools, max_steps=3)

        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['trace'][-1]['incidents'], [self.detail_args])

    def test_unmentioned_incidents_do_not_force_additional_tools(self):
        answer = 'Chỉ có dữ liệu tổng quan; chưa xác định phạm vi ảnh hưởng.'
        provider = FakeProvider([self.status_call(), final(answer)])

        result = run_agent('Xem tổng quan', provider, self.tools, max_steps=2)

        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 2)
        self.assertFalse(any(e['event'] == 'validation_missing_details' for e in result['trace']))

    def test_resolved_incident_summary_does_not_force_timeline_reads(self):
        args = {'provider': 'github', 'component': 'Actions'}
        status = self.tools.execute('get_status', args)
        answer = '\n'.join(i['id'] + ': ' + i['status'] for i in status['incidents'])
        provider = FakeProvider([
            tool_call('status', 'get_status', json.dumps(args)), final(answer),
        ])

        result = run_agent('Liệt kê trạng thái các incident gần đây', provider, self.tools, max_steps=2)

        self.assertTrue(all(i['status'] == 'resolved' for i in status['incidents']))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(provider.histories), 2)

    def test_mixed_status_list_requires_only_unread_active_incident(self):
        status = self.tools.execute('get_status', self.status_args)
        self.assertTrue(any(i['status'] == 'resolved' for i in status['incidents']))
        answer = '\n'.join(i['id'] for i in status['incidents'])
        provider = FakeProvider([self.status_call(), final(answer)])

        result = run_agent('Xem incident đang diễn ra', provider, self.tools, max_steps=2)

        self.assertEqual(result['status'], 'max_steps')
        self.assertEqual(result['trace'][-1]['incidents'], [self.detail_args])


if __name__ == '__main__':
    unittest.main()
