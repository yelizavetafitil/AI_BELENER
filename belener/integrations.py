"""Registry of external integrations configurable in admin."""

from __future__ import annotations

from typing import Any

INTEGRATIONS: list[dict[str, Any]] = [
    {
        "id": "stn",
        "name": "Стройдок (normy.stn.by)",
        "description": "Проверка актуальности нормативов в фонде ИПС.",
        "prefix": "stn",
        "fields": [
            {
                "key": "base_url",
                "label": "Адрес портала",
                "secret": False,
                "placeholder": "https://normy.stn.by",
            },
            {
                "key": "login",
                "label": "Логин ИПС",
                "secret": False,
                "placeholder": "user@company.by",
            },
            {
                "key": "password",
                "label": "Пароль ИПС",
                "secret": True,
                "placeholder": "••••••••",
            },
        ],
    },
]


def integration_by_id(integration_id: str) -> dict[str, Any] | None:
    for item in INTEGRATIONS:
        if item["id"] == integration_id:
            return item
    return None


def settings_key(prefix: str, field_key: str) -> str:
    return f"{prefix}.{field_key}"
