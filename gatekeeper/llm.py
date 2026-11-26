import json
import os
from typing import Any

import httpx

from gatekeeper.errors import PolzaError

_DEFAULT_BASE_URL = 'https://polza.ai/api/v1'
_DEFAULT_MODEL = 'qwen/qwen3.8-27b'
_TIMEOUT_SEC = 120.0


class PolzaModel:
    """Клиент chat/completions Polza. Ключ читается из окружения, в код не пишется."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = _DEFAULT_MODEL,
        timeout: float = _TIMEOUT_SEC,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip('/')
        self._model = model
        self._timeout = timeout

    def complete(self, *, system: str, user: str) -> str:
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        payloads = (
            _payload(self._model, messages, reasoning=True, json_object=True),
            _payload(self._model, messages, reasoning=False, json_object=True),
            {
                'model': self._model,
                'messages': messages,
                'max_tokens': 4000,
            },
        )
        last_error: PolzaError | None = None
        for index, payload in enumerate(payloads):
            try:
                return self._post(payload)
            except PolzaError as exc:
                last_error = exc
                if index == len(payloads) - 1 or not exc.user_message.startswith('Polza отклонила запрос'):
                    raise
        raise last_error or PolzaError('Polza не ответила')

    def _post(self, payload: dict[str, Any]) -> str:
        try:
            response = httpx.post(
                f'{self._base_url}/chat/completions',
                headers={'Authorization': f'Bearer {self._api_key}'},
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise PolzaError('Polza не ответила за отведённое время') from exc
        except httpx.HTTPError as exc:
            raise PolzaError('Не удалось связаться с Polza') from exc
        if response.status_code == 401:
            raise PolzaError('Polza не приняла ключ. Проверьте POLZA_API_KEY в .env')
        if response.status_code == 429:
            raise PolzaError('Polza временно ограничила запросы. Подождите и повторите')
        if response.status_code >= 400:
            raise PolzaError(f'Polza отклонила запрос ({response.status_code}): {_error_text(response)}')
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise PolzaError('Polza вернула не JSON') from exc
        content = _message_content(body)
        if not content.strip():
            raise PolzaError('Polza вернула пустой ответ')
        return content


def model_from_env() -> PolzaModel | None:
    api_key = os.environ.get('POLZA_API_KEY', '').strip()
    if not api_key:
        return None
    return PolzaModel(
        api_key,
        base_url=os.environ.get('POLZA_BASE_URL', _DEFAULT_BASE_URL),
        model=os.environ.get('POLZA_MODEL', _DEFAULT_MODEL),
    )


def _payload(
    model: str,
    messages: list[dict[str, str]],
    *,
    reasoning: bool,
    json_object: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'model': model,
        'messages': messages,
        'temperature': 0.2,
        'max_tokens': 4000,
    }
    if json_object:
        payload['response_format'] = {'type': 'json_object'}
    if reasoning:
        payload['reasoning'] = {'enabled': False, 'effort': 'none'}
    return payload


def _message_content(body: dict[str, Any]) -> str:
    try:
        message = body['choices'][0]['message']
    except (KeyError, IndexError, TypeError) as exc:
        raise PolzaError('В ответе Polza нет текста') from exc
    if not isinstance(message, dict):
        raise PolzaError('В ответе Polza нет текста')
    content = message.get('content')
    if not content:
        content = message.get('reasoning_content') or message.get('reasoning')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and isinstance(item.get('text'), str):
                chunks.append(item['text'])
        return '\n'.join(chunks)
    raise PolzaError('В ответе Polza нет текста')


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get('error')
        if isinstance(error, dict) and error.get('message'):
            return str(error['message'])[:300]
        if body.get('message'):
            return str(body['message'])[:300]
    return response.text[:300]
