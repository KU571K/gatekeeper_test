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
