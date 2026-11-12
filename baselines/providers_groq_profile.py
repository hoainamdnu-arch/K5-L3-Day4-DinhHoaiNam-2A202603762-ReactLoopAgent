"""Các provider dùng chung một interface. Không chứa agent loop."""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request
import uuid

CONFIG = {
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/chat/completions'),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions'),
    'anthropic': ('ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1/messages'),
    'groq': ('GROQ_API_KEY', 'https://api.groq.com/openai/v1/chat/completions'),
}


class ProviderHTTPError(RuntimeError):
    """Mã HTTP, hướng dẫn và thông báo API đã được che thông tin xác thực."""
    def __init__(self, http_status, edge_code=None, api_message=None, retry_after_seconds=None):
        self.http_status = http_status
        self.edge_code = edge_code
        self.api_message = api_message
        self.retry_after_seconds = retry_after_seconds
        hint = {
            400: 'request bị từ chối; xem chi tiết API nếu có.',
            401: 'kiểm tra API key của provider.',
            402: 'kiểm tra billing hoặc gói dịch vụ.',
            403: 'kiểm tra API key và quyền truy cập model hoặc project của provider.',
            429: 'đã bị giới hạn tần suất hoặc quota; chờ trước khi thử lại.',
        }.get(http_status, 'kiểm tra endpoint, model và trạng thái provider.')
        if edge_code == 1010:
            hint = 'Cloudflare 1010 chặn nhận dạng client; kiểm tra User-Agent hoặc liên hệ OpenCode.'
        if api_message:
            hint += f' API: {api_message}'
        super().__init__(f'Provider trả HTTP {http_status}; {hint}')


class ProviderTimeoutError(TimeoutError):
    """Thông báo timeout không chứa URL, key hoặc nội dung request."""
    def __init__(self, timeout):
        self.timeout = timeout
        super().__init__(f'Provider không phản hồi trong {timeout:g} giây; kiểm tra kết nối hoặc tải của dịch vụ.')


def _safe_api_message(body, headers):
    """Chỉ lấy trường thông báo lỗi JSON, che key trước khi giới hạn độ dài."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and 'error' in data[0]:
        data = data[0]
    if not isinstance(data, dict):
        return None
    error = data.get('error', data)
    message = error.get('message') if isinstance(error, dict) else error
    if not isinstance(message, str):
        return None
    secrets = [os.getenv(name) for name in ['API_KEY'] + [entry[0] for entry in CONFIG.values()]]
    for name, value in headers.items():
        if name.lower() in ('authorization', 'x-api-key'):
            secrets.extend([value, value.removeprefix('Bearer ')])
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        message = message.replace(secret, '[REDACTED]')
    message = re.sub(r'(?:oc_sk_|gsk_)[A-Za-z0-9_.*-]+', '[REDACTED]', message)
    message = re.sub(r'(?i)Bearer\s+[^\s,;"\'<>]+', 'Bearer [REDACTED]', message)
    return ' '.join(message.split())[:500] or None


def retry_delay(value, message=None):
    """Đọc Retry-After (giây hoặc HTTP date), rồi gợi ý thời gian của Groq."""
    if value:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = None
        if seconds is not None and 0 <= seconds <= 120:
            return seconds
        return None
    match = re.search(r'Please try again in (\d+(?:\.\d+)?)(ms|s)', message or '', re.IGNORECASE)
    if match:
        seconds = float(match[1]) / (1000 if match[2].lower() == 'ms' else 1)
        if 0 <= seconds <= 120:
            return seconds
    return None


def _post_json_once(url, headers, payload, *, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'User-Agent': 'StatusAgentLab/1.0', **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Không ghi nguyên body hoặc headers; chỉ lấy thông báo lỗi đã che key.
        edge_code = None
        api_message = None
        try:
            body = exc.read(16384)
            if exc.code == 403 and b'error code: 1010' in body.lower():
                edge_code = 1010
            api_message = _safe_api_message(body, headers)
        except OSError:
            pass
        finally:
            exc.close()
        delay = retry_delay(exc.headers.get('Retry-After') if exc.headers else None, api_message)
        raise ProviderHTTPError(exc.code, edge_code, api_message, delay) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ProviderTimeoutError(timeout) from exc
        raise RuntimeError(f'Không kết nối được provider: {exc.reason}') from exc
    except TimeoutError as exc:
        raise ProviderTimeoutError(timeout) from exc


def post_json(url, headers, payload, *, timeout=60):
    """Retry HTTP 429 có thời gian chờ rõ ràng; mặc định tắt để giữ contract cũ."""
    try:
        retries = int(os.getenv('PROVIDER_RATE_RETRIES', '0'))
    except ValueError:
        raise ValueError('PROVIDER_RATE_RETRIES phải là số nguyên từ 0 đến 4.') from None
    if not 0 <= retries <= 4:
        raise ValueError('PROVIDER_RATE_RETRIES phải từ 0 đến 4.')
    for attempt in range(retries + 1):
        try:
            return _post_json_once(url, headers, payload, timeout=timeout)
        except ProviderHTTPError as exc:
            if exc.http_status != 429 or exc.retry_after_seconds is None or attempt == retries:
                raise
            delay = max(1.0, exc.retry_after_seconds + 0.5)
            print(f'HTTP 429: waiting {delay:.1f}s; retry {attempt + 1}/{retries}.', file=sys.stderr, flush=True)
            time.sleep(delay)


class Provider:
    """complete(messages, schemas) -> {message, calls, finish, usage}.

    messages: system/user/assistant/tool theo format Chat Completions. message trả về
    cần được append nguyên vẹn; tool result có role, tool_call_id, content (JSON string).
    """
    def __init__(self, name, model):
        if name not in CONFIG:
            raise ValueError(f'Provider không hợp lệ: {name}')
        if not model:
            raise ValueError('Cần đặt MODEL là model có hỗ trợ tool calling.')
        self.name, self.model = name, model
        try:
            self.timeout = float(os.getenv('PROVIDER_TIMEOUT', '60'))
            self.max_retries = int(os.getenv('PROVIDER_MAX_RETRIES', '0'))
        except ValueError:
            raise ValueError('PROVIDER_TIMEOUT phải là số, PROVIDER_MAX_RETRIES phải là số nguyên.') from None
        if not 0 < self.timeout <= 300 or not 0 <= self.max_retries <= 2:
            raise ValueError('PROVIDER_TIMEOUT phải trong (0, 300]; PROVIDER_MAX_RETRIES phải từ 0 đến 2.')
        self.reasoning_effort = os.getenv('GEMINI_REASONING_EFFORT') if name == 'gemini' else None
        if self.reasoning_effort not in (None, '', 'default', 'low', 'medium', 'high'):
            raise ValueError('GEMINI_REASONING_EFFORT phải là default, low, medium hoặc high.')
        self.max_output_tokens = None
        self.output_hint = None
        self.disable_tool_validation = False
        self.tool_call_retries = 0
        if name == 'groq':
            output_limit = os.getenv('GROQ_MAX_OUTPUT_TOKENS')
            if output_limit:
                try:
                    self.max_output_tokens = int(output_limit)
                except ValueError:
                    raise ValueError('GROQ_MAX_OUTPUT_TOKENS phải là số nguyên từ 64 đến 32768.') from None
                if not 64 <= self.max_output_tokens <= 32768:
                    raise ValueError('GROQ_MAX_OUTPUT_TOKENS phải từ 64 đến 32768.')
            self.output_hint = os.getenv('GROQ_OUTPUT_HINT')
            validation = os.getenv('GROQ_DISABLE_TOOL_VALIDATION', '0')
            if validation not in ('0', '1'):
                raise ValueError('GROQ_DISABLE_TOOL_VALIDATION phải là 0 hoặc 1.')
            self.disable_tool_validation = validation == '1'
            try:
                self.tool_call_retries = int(os.getenv('GROQ_TOOL_CALL_RETRIES', '0'))
            except ValueError:
                raise ValueError('GROQ_TOOL_CALL_RETRIES phải là số nguyên từ 0 đến 2.') from None
            if not 0 <= self.tool_call_retries <= 2:
                raise ValueError('GROQ_TOOL_CALL_RETRIES phải từ 0 đến 2.')
        key_name, self.url = CONFIG[name]
        self.key = os.getenv(key_name)
        if name == 'openai':
            endpoint = os.getenv('API_ENDPOINT')
            if endpoint:
                self.url = endpoint.rstrip('/')
                if not self.url.endswith('/chat/completions'):
                    self.url += '/chat/completions'
                self.key = os.getenv('API_KEY') or self.key
            else:
                self.key = self.key or os.getenv('API_KEY')
        if not self.key:
            key_hint = f'{key_name} hoặc API_KEY' if name == 'openai' else key_name
            raise ValueError(f'Thiếu {key_hint}; xem README.md.')
        self.session_id = uuid.uuid4().hex if urllib.parse.urlsplit(self.url).hostname == 'opencode.ai' else None

    def complete(self, messages, schemas):
        if self.name == 'anthropic':
            return self._anthropic(messages, schemas)
        request_messages = messages
        if self.output_hint:
            # Gợi ý generation dùng chung cho cả hai bản; không sửa message đầu vào.
            request_messages = [dict(m) for m in messages]
            if request_messages and request_messages[0]['role'] == 'system':
                request_messages[0]['content'] = (request_messages[0].get('content') or '') + '\n' + self.output_hint
            else:
                request_messages.insert(0, {'role': 'system', 'content': self.output_hint})
        payload = {'model': self.model, 'messages': request_messages}
        if self.max_output_tokens is not None:
            payload['max_completion_tokens'] = self.max_output_tokens
        if self.reasoning_effort and self.reasoning_effort != 'default':
            payload['reasoning_effort'] = self.reasoning_effort
        if schemas:
            payload['tools'] = schemas
            if self.disable_tool_validation:
                # Registry của lab vẫn kiểm tra tên và arguments, trả lỗi observation.
                payload['disable_tool_validation'] = True
        headers = {'Authorization': f'Bearer {self.key}'}
        if self.session_id:
            headers['x-opencode-session'] = self.session_id
        retry_budget = self.tool_call_retries if schemas else 0
        for attempt in range(retry_budget + 1):
            wire_payload = dict(payload)
            if attempt:
                wire_payload['messages'] = [*request_messages, {
                    'role': 'user',
                    'content': 'API đã từ chối định dạng tool call ở lần vừa rồi. '
                               'Chỉ dùng đúng tên tool trong schemas và arguments là JSON object; '
                               'không tạo thêm hàm. Nếu đã đủ dữ liệu, trả lời cuối cùng.',
                }]
            try:
                data = post_json(self.url, headers, wire_payload, timeout=self.timeout)
                break
            except ProviderHTTPError as exc:
                message = (exc.api_message or '').casefold()
                format_error = any(text in message for text in (
                    'failed to call a function', 'tool call validation failed', 'did not match schema',
                ))
                if exc.http_status != 400 or not format_error or attempt == retry_budget:
                    raise
        choice = data['choices'][0]
        message = choice['message']
        calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in message.get('tool_calls', [])]
        usage = dict(data.get('usage', {}))
        if attempt:
            usage['tool_format_retries'] = attempt
        return {'message': message, 'calls': calls, 'finish': choice.get('finish_reason'), 'usage': usage}

    def _anthropic(self, messages, schemas):
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        native = []
        for m in messages:
            if m['role'] == 'system':
                continue
            if m['role'] == 'assistant':
                blocks = []
                if m.get('content'):
                    blocks.append({'type': 'text', 'text': m['content']})
                for call in m.get('tool_calls', []):
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': json.loads(call['function']['arguments'])})
                native.append({'role': 'assistant', 'content': blocks})
            elif m['role'] == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
                if native and native[-1]['role'] == 'user' and isinstance(native[-1]['content'], list):
                    native[-1]['content'].append(block)
                else:
                    native.append({'role': 'user', 'content': [block]})
            else:
                native.append({'role': 'user', 'content': m['content']})
        tools = [{'name': x['function']['name'], 'description': x['function']['description'], 'input_schema': x['function']['parameters']} for x in schemas]
        payload = {'model': self.model, 'max_tokens': 1024, 'system': system, 'messages': native}
        if tools:
            payload['tools'] = tools
        data = post_json(self.url, {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, payload, timeout=self.timeout)
        blocks = data['content']
        calls = [{'id': b['id'], 'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)} for b in blocks if b['type'] == 'tool_use']
        text = '\n'.join(b['text'] for b in blocks if b['type'] == 'text')
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in calls]
        reason = {'end_turn': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        return {'message': message, 'calls': calls, 'finish': reason, 'usage': data.get('usage', {})}
