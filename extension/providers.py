"""Explicit, independently configured LLM providers with SDK retries disabled."""
import os


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
        self.client = genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)))
        self.types, self.api_error = types, errors.APIError
        self.network_errors = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                               httpx.RemoteProtocolError)

    def request(self, model, temperature, prompt):
        response = self.client.models.generate_content(model=model, contents=prompt,
                    config=self.types.GenerateContentConfig(temperature=temperature))
        text = response.text or ''
        usage = response.usage_metadata.model_dump(mode='json', exclude_none=True) if response.usage_metadata else None
        return text, usage

    def retryable(self, error):
        return (isinstance(error, self.network_errors) or
                (isinstance(error, self.api_error) and (error.code in (408, 429) or 500 <= error.code < 600)))


def make_provider(provider):
    try:
        if provider == 'openai':
            return OpenAIProvider()
        if provider == 'gemini':
            return GeminiProvider()
    except ImportError as e:
        raise RuntimeError('Provider SDK missing: install requirements-extension.txt') from e
    raise ValueError(f'Unknown provider: {provider}')
