"""Explicit, independently configured LLM providers with SDK retries disabled."""
import os
import json
from urllib.parse import urlsplit

GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/'
GEMINI_API_VERSION = 'v1beta'
GEMINI_MODEL = 'gemini-3.1-flash-lite'


class GeminiRequestError(RuntimeError):
    """Error carrying only an allowlisted diagnostic, never raw request data."""
    def __init__(self, diagnostic, retryable=False):
        super().__init__(diagnostic)
        self.is_retryable = retryable


def proxy_environment():
    names = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')
    present = [name for name in names if os.environ.get(name) or os.environ.get(name.lower())]
    return ', '.join(present) if present else 'none detected (system proxies may still apply)'


def safe_endpoint(request):
    """Only report allowlisted URL components, never query, user info or unknown hosts."""
    if request is None:
        return 'unavailable (no request attached)'
    try:
        u = urlsplit(str(request.url))
        if u.scheme == 'https' and u.hostname == 'generativelanguage.googleapis.com' and u.port in (None, 443):
            path = u.path
            expected = f'/{GEMINI_API_VERSION}/models/{GEMINI_MODEL}:generateContent'
            if path == expected:
                return f'{GEMINI_BASE_URL.rstrip("/")}{expected}'
            return GEMINI_BASE_URL.rstrip('/') + '/[other path redacted]'
        return 'nonstandard endpoint (host and path redacted)'
    except (AttributeError, ValueError):
        return 'unavailable (request URL could not be safely parsed)'


def sanitized_error_body(error, response):
    """Report safe classifications only: response content can contain secrets."""
    body = ''
    if response is not None:
        try:
            body = response.text
        except Exception:
            pass
    if not body:
        message = getattr(error, 'message', None)
        body = message if isinstance(message, str) else ''
    if not isinstance(body, str):
        body = ''
    data = None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        details = getattr(error, 'details', None)
        if isinstance(details, dict) and isinstance(details.get('error'), dict):
            data = details
    if isinstance(data, dict):
        obj = data.get('error', data)
        if not isinstance(obj, dict):
            return 'JSON error (details redacted)'
        allowed = {'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'PERMISSION_DENIED',
                   'UNAUTHENTICATED', 'RESOURCE_EXHAUSTED', 'NOT_FOUND', 'INTERNAL',
                   'UNAVAILABLE', 'DEADLINE_EXCEEDED'}
        status = obj.get('status')
        result = 'JSON error; status=' + (status if isinstance(status, str) and status in allowed else 'other/unknown')
        message = obj.get('message')
        if isinstance(message, str) and 'API key not valid' in message:
            result += '; message indicates invalid API key'
        return result + '; other content redacted'
    if '<html' in body.lower() or '<!doctype html' in body.lower():
        return 'HTML error page (content redacted; origin cannot be established from body alone)'
    return 'non-JSON or unavailable response (content redacted)'


def gemini_error_diagnostic(error):
    response = getattr(error, 'response', None)
    request = None
    if response is not None:
        try:
            request = response.request
        except (AttributeError, RuntimeError):
            pass
    if request is None:
        try:
            request = getattr(error, 'request', None)
        except RuntimeError:
            request = None
    code = getattr(error, 'code', None)
    if not isinstance(code, int) and response is not None:
        code = getattr(response, 'status_code', None)
    code = str(code) if isinstance(code, int) and 100 <= code <= 599 else 'unavailable'
    headers = (getattr(response, 'headers', None) or {}) if response is not None else {}
    # A forwarding header is suggestive, never proof of origin; values are not printed.
    forwarded = 'present' if any(h in headers for h in ('via', 'x-proxy-id', 'x-cache')) else 'not detected'
    return (f'HTTP status: {code}; endpoint: {safe_endpoint(request)}; '
            f'sanitized response body: {sanitized_error_body(error, response)}; '
            f'forwarding headers: {forwarded}; proxy environment variable names: {proxy_environment()}. '
            'These hints cannot prove whether Google or an intermediary generated the response.')


def detected_https_proxy():
    """Check Python's system/environment proxy discovery without exposing values."""
    from urllib.request import getproxies
    try:
        proxies = getproxies()
        return 'yes' if proxies.get('https') or proxies.get('all') else 'no'
    except Exception:
        # Registry/environment parsing errors can contain sensitive settings.
        return 'unavailable'


# Used only by the offline diagnostic subprocess, never by the live provider.
_MOCK_KEY = 'offline-diagnostic-fake-key'
_MOCK_PROMPT = 'For a connectivity test, output exactly: INTEREST RATE VOTE: 0.00%'
_MOCK_CHECKS = ('method_post', 'expected_endpoint', 'json_content_type', 'payload_shape',
                'api_key_header_present', 'fake_key_used', 'authorization_header_present')


def _gemini_mock_worker():
    """Construct an SDK request with a fake key and a socket-blocked mock transport."""
    import socket
    from unittest.mock import patch
    import httpx
    from google import genai
    from google.genai import types

    checks = {}
    expected_url = f'{GEMINI_BASE_URL}{GEMINI_API_VERSION}/models/{GEMINI_MODEL}:generateContent'
    expected_body = {'contents': [{'parts': [{'text': _MOCK_PROMPT}], 'role': 'user'}],
                     'generationConfig': {'temperature': 0.7}}

    def handle(request):
        checks.update({
            'method_post': request.method == 'POST',
            'expected_endpoint': str(request.url) == expected_url,
            'json_content_type': request.headers.get('content-type', '').split(';')[0].strip().lower() == 'application/json',
            'payload_shape': json.loads(request.content) == expected_body,
            'api_key_header_present': 'x-goog-api-key' in request.headers,
            'fake_key_used': request.headers.get('x-goog-api-key') == _MOCK_KEY,
            'authorization_header_present': 'authorization' in request.headers,
        })
        return httpx.Response(200, request=request, json={
            'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'mock response'}]},
                            'finishReason': 'STOP'}]})

    def no_socket(*args, **kwargs):
        raise RuntimeError('network disabled for offline diagnostic')

    # Inject a transport for both sync and async clients. The socket guard is
    # defense in depth: an accidental SDK transport change must not contact a server.
    with patch.object(socket.socket, 'connect', no_socket), \
         patch.object(socket.socket, 'connect_ex', no_socket), \
         patch.object(socket, 'create_connection', no_socket), \
         httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as sync_client:
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
        client = genai.Client(api_key=_MOCK_KEY, vertexai=False, http_options=types.HttpOptions(
            base_url=GEMINI_BASE_URL, api_version=GEMINI_API_VERSION,
            retry_options=types.HttpRetryOptions(attempts=1),
            httpx_client=sync_client, httpx_async_client=async_client))
        try:
            client.models.generate_content(model=GEMINI_MODEL, contents=_MOCK_PROMPT,
                config=types.GenerateContentConfig(temperature=0.7))
        finally:
            client.close()
    # This output is consumed by the parent and never forwarded verbatim.
    print(json.dumps(checks))


def offline_sdk_check():
    """Run the mock in an isolated process, returning only allowlisted booleans."""
    import subprocess
    import sys
    # Pass only basic OS/Python settings to the subprocess, not the caller's
    # credentials or proxy settings. This does not modify the caller's environment.
    allowed_env = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR',
                   'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed_env}
    backend = os.environ.get('GOOGLE_GENAI_HTTP_CLIENT', '').lower().strip()
    if backend in ('httpx', 'httpx2', 'auto'):
        env['GOOGLE_GENAI_HTTP_CLIENT'] = backend
    env['GEMINI_API_KEY'] = _MOCK_KEY
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        result = subprocess.run(
            [sys.executable, '-c', 'from extension.providers import _gemini_mock_worker; _gemini_mock_worker()'],
            env=env, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            return None
        checks = json.loads(result.stdout)
        if not isinstance(checks, dict) or set(checks) != set(_MOCK_CHECKS):
            return None
        if any(type(checks[name]) is not bool for name in _MOCK_CHECKS):
            return None
        return checks
    except Exception:
        # Child output and exception text may contain secrets; never relay them.
        return None


def local_gemini_diagnostics(model):
    """Print non-secret local configuration; no network requests are made."""
    from importlib.metadata import version, PackageNotFoundError
    try:
        sdk = version('google-genai')
    except PackageNotFoundError:
        sdk = 'not installed'
    key = os.environ.get('GEMINI_API_KEY')
    key_state = 'present' if key else 'not set'
    whitespace = 'yes' if key and any(c.isspace() for c in key) else 'no'
    safe_model = model if model == GEMINI_MODEL else '[other model redacted]'
    print('Local diagnostics only; no network request was made.')
    print(f'Google GenAI SDK: {sdk}; model: {safe_model}; API version: {GEMINI_API_VERSION}')
    print(f'Configured base endpoint: {GEMINI_BASE_URL}; authentication: GEMINI_API_KEY via x-goog-api-key header')
    print(f'GEMINI_API_KEY: {key_state}; contains whitespace: {whitespace} (value not shown)')
    print(f'Proxy environment variable names: {proxy_environment()} (values not shown)')
    print(f'Python-detected HTTPS or ALL system/environment proxy: {detected_https_proxy()} (values not shown)')
    print('Detection does not establish proxy use for this host or rule out transparent interception.')
    checks = offline_sdk_check()
    if checks is None:
        print('Offline SDK mock check: unavailable (details redacted).')
    else:
        yn = lambda value: 'yes' if value else 'no'
        print('Offline SDK mock check for gemini-3.1-flash-lite (fake key; httpx mock transport):')
        print(f"  POST: {yn(checks['method_post'])}; expected endpoint: {yn(checks['expected_endpoint'])}; "
              f"JSON content type: {yn(checks['json_content_type'])}; expected payload: {yn(checks['payload_shape'])}")
        print(f"  API-key header present: {yn(checks['api_key_header_present'])}; "
              f"fake key used: {yn(checks['fake_key_used'])}; "
              f"Authorization header present: {yn(checks['authorization_header_present'])}")
        print('This checks local SDK serialization, not the live network or account access.')


class OpenAIRequestError(RuntimeError):
    """Allowlisted failure details, without raw provider exceptions or bodies."""
    def __init__(self, diagnostic, retryable=False):
        super().__init__(diagnostic)
        self.is_retryable = retryable


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError, InternalServerError
        # Explicit official endpoint prevents environment base-URL overrides from
        # silently mixing another provider into the OpenAI cache namespace.
        try:
            self.client = OpenAI(max_retries=0, base_url='https://api.openai.com/v1')
        except Exception:
            raise RuntimeError('Could not initialize OpenAI client; details redacted') from None
        self.retryable_types = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)

    def request(self, model, temperature, prompt):
        try:
            response = self.client.chat.completions.create(model=model, temperature=temperature,
                        messages=[{'role': 'user', 'content': prompt}])
            text = response.choices[0].message.content or ''
            usage = response.usage.model_dump(mode='json') if response.usage else None
            return text, usage
        except Exception as error:
            status = getattr(error, 'status_code', None)
            status = str(status) if isinstance(status, int) and 100 <= status <= 599 else 'unavailable'
            raise OpenAIRequestError(f'OpenAI request failed; HTTP status: {status}; details redacted',
                                     self.retryable(error)) from None

    def retryable(self, error):
        if isinstance(error, OpenAIRequestError):
            return error.is_retryable
        return isinstance(error, self.retryable_types)


class GeminiProvider:
    def __init__(self):
        from google import genai
        from google.genai import types, errors
        import httpx
        key = os.environ.get('GEMINI_API_KEY')
        if not key:
            raise ValueError('GEMINI_API_KEY is not set in the current terminal')
        # SDK default is up to 5 attempts. One attempt is essential: all
        # provider requests, including our retries, must consume CallBudget.
        try:
            self.client = genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(
                base_url=GEMINI_BASE_URL, api_version=GEMINI_API_VERSION,
                retry_options=types.HttpRetryOptions(attempts=1)))
        except Exception:
            # SDK initialization errors may contain supplied configuration.
            raise RuntimeError('Could not initialize Gemini client; details redacted') from None
        self.types, self.api_error = types, errors.APIError
        self.network_errors = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                               httpx.RemoteProtocolError)
        # Newer SDK releases can use httpx2; its exception classes are not
        # subclasses of httpx's, even though the APIs look alike.
        try:
            import httpx2
        except ImportError:
            pass
        else:
            self.network_errors += (httpx2.TimeoutException, httpx2.ConnectError,
                                    httpx2.ReadError, httpx2.RemoteProtocolError)

    def request(self, model, temperature, prompt):
        try:
            response = self.client.models.generate_content(model=model, contents=prompt,
                        config=self.types.GenerateContentConfig(temperature=temperature))
            text = response.text or ''
            usage = response.usage_metadata.model_dump(mode='json', exclude_none=True) if response.usage_metadata else None
            return text, usage
        except Exception as error:
            # Never pass raw exception strings, response headers, URLs with queries,
            # or bodies to the CLI; they may include credentials or proxy secrets.
            raise GeminiRequestError(gemini_error_diagnostic(error), self.retryable(error)) from None

    def retryable(self, error):
        if isinstance(error, GeminiRequestError):
            return error.is_retryable
        return (isinstance(error, self.network_errors) or
                (isinstance(error, self.api_error) and isinstance(error.code, int) and
                 (error.code in (408, 429) or 500 <= error.code < 600)))


def make_provider(provider):
    try:
        if provider == 'openai':
            return OpenAIProvider()
        if provider == 'gemini':
            return GeminiProvider()
    except ImportError as e:
        raise RuntimeError('Provider SDK missing: install requirements-extension.txt') from e
    raise ValueError(f'Unknown provider: {provider}')
