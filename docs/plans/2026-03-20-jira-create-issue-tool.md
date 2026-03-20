# План внедрения: добавить MCP tool для создания задач Jira

## Цель

- Добавить в `jira-mcp` новую write-tool для создания задач в Jira.
- Сделать создание задач безопасным: требовать `confirm=true` и отдельный feature flag на уровне конфигурации.
- Ограничить создание задач только явно разрешенным проектом Jira из env-настроек.

## Не-цель

- Не добавлять массовое создание задач.
- Не расширять write-права на все проекты по умолчанию.
- Не строить универсальный schema-discovery для всех Jira create screens в первой итерации.

## Критерии готовности

- В MCP появляется tool вида `jira_create_issue`.
- Tool не работает без `confirm=true`.
- Tool не работает, если выключен отдельный feature flag на создание задач.
- Tool не работает, если целевой проект не совпадает с разрешенным project key из настроек.
- `README.md`, `.env.example` и тесты покрывают новую механику.

## Предпосылки и ограничения

- В текущем коде write-операции уже защищены через `confirm=true` и whitelist в `jira_mcp/server.py`.
- Сейчас whitelist привязан к существующим issue/project key и не покрывает кейс создания новой задачи, где issue key еще не существует.
- Значит для create-flow нужен отдельный guard, который проверяет проект до вызова Jira API.
- В первой версии разумно опираться на минимальный обязательный payload: `project`, `issuetype`, `summary`, опционально `description` и дополнительные `fields`.

## Предлагаемый контракт tool

- Название: `jira_create_issue`.
- Аргументы MVP:
  - `project_key: str`
  - `summary: str`
  - `issue_type: str = "Task"`
  - `description: str | None = None`
  - `fields: dict[str, Any] | None = None`
  - `confirm: bool = False`
- Ответ MVP:
  - `status`
  - `issue_key`
  - `issue_id`
  - `issue_url` или хотя бы self/link если Jira его вернет

## Новая схема безопасности

- Добавить env-флаг `JIRA_ENABLE_CREATE_ISSUE=false`.
- Добавить env-параметр `JIRA_CREATE_ISSUE_PROJECT=<KEY>`.
- Guard для create должен проверять по порядку:
  - `confirm=true`;
  - `JIRA_ENABLE_CREATE_ISSUE=true`;
  - `project_key` не пустой;
  - `project_key` совпадает с `JIRA_CREATE_ISSUE_PROJECT`.
- Ошибки должны быть явными: выключен feature flag, не задан разрешенный проект, проект не разрешен.

## Подход

- Расширить `Settings` в `jira_mcp/config.py` новыми полями для feature flag и project key.
- Добавить валидацию конфигурации: если create включен, разрешенный project key должен быть задан.
- Реализовать в `JiraClient` новый метод `create_issue(...)`, который делает `POST /issue` с телом `{"fields": ...}`.
- В `jira_mcp/server.py` добавить отдельный helper для create-guard, не смешивая его с `_ensure_write_allowed(issue_key, confirm)`.
- Обновить документацию и примеры вызова.

## Задачи

- [ ] Зафиксировать окончательный контракт `jira_create_issue` для MVP и список поддерживаемых полей.
- [ ] Добавить в `jira_mcp/config.py` поля `enable_create_issue` и `create_issue_project`.
- [ ] Добавить валидацию env: нельзя включить create без `JIRA_CREATE_ISSUE_PROJECT`.
- [ ] Добавить в `jira_mcp/jira_client.py` метод `create_issue`, собирающий payload для Jira REST API.
- [ ] Добавить в `jira_mcp/server.py` новый MCP tool и отдельный guard для create-операции.
- [ ] Обновить `README.md` и `.env.example` с описанием feature flag, project restriction и примеров использования.
- [ ] Добавить unit-тесты на config validation, guard-логику и успешный/неуспешный create-flow.

## Порядок работ

- 1. Определить MVP-контракт tool и env-настройки.
- 2. Расширить config и валидацию.
- 3. Реализовать client method для `POST /issue`.
- 4. Подключить tool и safety guard в `server.py`.
- 5. Добавить тесты.
- 6. Обновить docs и примеры.

## Затронутые файлы/модули

- `jira_mcp/config.py`
- `jira_mcp/jira_client.py`
- `jira_mcp/server.py`
- `tests/test_config.py`
- `tests/test_jira_client.py`
- Возможно новый тест для server-level guard
- `README.md`
- `.env.example`

## Тест-план

- Unit:
  - create включен без `JIRA_CREATE_ISSUE_PROJECT` -> явная ошибка валидации;
  - create guard отклоняет вызов без `confirm=true`;
  - create guard отклоняет вызов при выключенном feature flag;
  - create guard отклоняет вызов для проекта, отличного от разрешенного;
  - `create_issue` формирует корректный Jira payload для минимального и расширенного набора полей.
- Integration:
  - mock Jira server принимает `POST /issue` и возвращает `key/id/self`;
  - tool успешно создает задачу в разрешенном проекте;
  - tool получает понятную ошибку Jira при невалидном payload.

## Риски и решения

- Риск: create-screen в Jira может требовать нестандартные обязательные поля.
  - Решение: оставить `fields` для расширения payload сверх MVP-полей.
- Риск: смешение existing write whitelist и create guard усложнит правила.
  - Решение: держать отдельную проверку для create-flow, потому что issue key еще не существует.
- Риск: слишком широкий feature flag даст случайное создание задач в чужих проектах.
  - Решение: требовать один явный `JIRA_CREATE_ISSUE_PROJECT` в MVP.

## Rollback

- Откат делается удалением tool и возвратом конфигурации к предыдущей ревизии.
- На runtime-уровне достаточно выставить `JIRA_ENABLE_CREATE_ISSUE=false`, чтобы быстро отключить создание задач без отката всего сервиса.
