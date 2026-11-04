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


def local_gemini_diagnostics(model):
    """Print non-secret local configuration; no HTTP requests are made."""
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
    print(f'Configured base endpoint: {GEMINI_BASE_URL}; authentication: GEMINI_API_KEY header')
    print(f'GEMINI_API_KEY: {key_state}; contains whitespace: {whitespace} (value not shown)')
    print(f'Proxy environment variable names: {proxy_environment()} (values not shown)')
    print('System or network proxies may still be present even when no environment variables are detected.')


class OpenAIProvider:
    def __init__(self):
        from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError, InternalServerError
        # Explicit official endpoint prevents environment base-URL overrides from
        # silently mixing another provider into the OpenAI cache namespace.
        self.client = OpenAI(max_retries=0, base_url='https://api.openai.com/v1')
        self.retryable_types = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)

    def request(self, model, temperature, prompt):
        response = self.client.chat.completions.create(model=model, temperature=temperature,
                    messages=[{'role': 'user', 'content': prompt}])
        text = response.choices[0].message.content or ''
        usage = response.usage.model_dump(mode='json') if response.usage else None
        return text, usage

    def retryable(self, error):
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
