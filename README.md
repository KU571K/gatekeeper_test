# Привратник

Отдельный модуль: по свободному описанию проекта и одной роли собирает теги, предметную область, образ результата с критериями, нужные роли и промокод. Правила — в [GATEKEEPER_INFO.md](GATEKEEPER_INFO.md).

## Запуск

В PowerShell из этой папки:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

В `.env` впишите `POLZA_API_KEY`, затем:

```powershell
streamlit run app.py
```

Модель по умолчанию: `qwen/qwen3.8-27b`, адрес API: `https://polza.ai/api/v1`.

## Публикация на Streamlit Community Cloud

Репозиторий нужно отдать на GitHub. В корне уже есть `requirements.txt` и `runtime.txt` (Python 3.12). Файл приложения — `app.py`.

1. Создайте репозиторий и запушьте код. `.env` и `.streamlit/secrets.toml` в git не входят.
2. На [share.streamlit.io](https://share.streamlit.io) выберите этот репозиторий, ветку и файл `app.py`.
3. В Settings → Secrets вставьте содержимое [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example) и подставьте свой `POLZA_API_KEY`.

Локальный `.env` на сервере не нужен: приложение читает те же три переменные из секретов Streamlit.
