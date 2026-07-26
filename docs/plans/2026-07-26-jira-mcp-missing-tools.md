# План внедрения: закрыть подтвержденные обходы Jira API

## Цель

- Добавить типизированные jira-mcp tools для операций, ради которых агенты обращались к Jira REST API напрямую.
- Добавить постраничное чтение результатов поиска, журнала изменений и трудозатрат без неограниченной выгрузки данных.
- Дать агенту безопасный способ находить поля, проекты и доски Jira перед созданием или обновлением задач.
- Добавить безопасное удаление вложения с проверкой задачи, whitelist и `confirm=true`.

## Не-цель

- Не добавлять универсальный tool для произвольных Jira REST-запросов.
- Не добавлять автоматический `fetch_all`: каждая страница остается ограниченной 200 элементами.
- Не менять существующую auth/recovery-модель, write whitelist и правила создания задач.
- Не добавлять новые env-настройки и отдельные слои абстракции без необходимости.
- Не заниматься очисткой cookies и паролей из старых сессий OpenCode.

## Критерии готовности

- `jira_search_issues` принимает `start_at` и корректно разделяет страницы в read cache.
- Доступны read tools для worklogs, changelog, списка полей, edit metadata, списка и конфигурации досок, сведений о проекте.
- Read tools нормализуют пагинацию и ограничивают `limit` диапазоном 1–200.
- `jira_delete_attachment` не работает без `confirm=true`, проверяет issue whitelist и принадлежность вложения указанной задаче до удаления.
- README содержит список новых tools и примеры вызова.
- Полный набор unit/integration-тестов проходит.
- MCP smoke `initialize` + `tools/list` видит обновленный server без ошибок.

## Предпосылки и ограничения

- Общий request path уже реализует auth fallback, browser recovery, retry и обработку ошибок Jira.
- `jira_get_issue` поддерживает `expand`, но встроенные worklog/changelog могут быть усечены, поэтому нужны отдельные пагинируемые endpoints.
- Текущий поиск ограничивает `limit` 200 и жестко задает `startAt=0`; достаточно добавить явный `start_at` вместо неограниченной агрегации.
- Jira `/field` возвращает весь каталог без серверной пагинации; фильтрация по строке и custom fields выполняется в клиенте.
- Jira edit metadata может быть объемной; tool поддерживает фильтр `field_ids`.
- Для удаления вложения недостаточно знать attachment id: перед `DELETE` клиент свежим Jira-запросом, минуя read cache, читает attachments задачи и проверяет точное совпадение id.
- Не все Jira Server/Data Center версии поддерживают отдельный changelog endpoint; первая страница читается через `expand=changelog` как fallback.
- Пагинация поиска сохраняет существующий для этой инсталляции контракт `startAt`; Jira Cloud `nextPageToken` не входит в scope.

## Подход

- Расширить существующие методы `JiraClient`, не вводя новый сервисный слой.
- Для пагинируемых ответов возвращать `count`, `total`, `start_at`, `max_results`, `is_last` и массив элементов.
- Включить `start_at` в ключ search cache, чтобы страницы не пересекались.
- Нормализовать и валидировать входы в `server.py`, а Jira-specific payload оставлять в `jira_client.py`.
- Для read metadata возвращать исходные Jira-объекты после минимальной фильтрации, чтобы не потерять custom schema и allowed values.
- Реализовать удаление вложения по паттерну безопасного удаления issue link: validate, fresh read/verify без cache, delete, invalidate cache.
- При 404 changelog endpoint использовать issue expansion и явно сообщать `source`; последующие страницы в fallback-режиме отклонять понятной ошибкой.
- Для attachment DELETE не продолжать auth fallback после 403, чтобы permission denial не привел к попытке более привилегированными credentials.

## Контракты tools

- `jira_search_issues(jql, fields=None, limit=None, start_at=0)`.
- `jira_list_issue_worklogs(issue_key, limit=None, start_at=0)`.
- `jira_get_issue_changelog(issue_key, limit=None, start_at=0)`.
- `jira_list_fields(query=None, custom_only=False)`.
- `jira_get_issue_edit_metadata(issue_key, field_ids=None)`.
- `jira_list_boards(project_key_or_id=None, name=None, board_type=None, limit=None, start_at=0)`.
- `jira_get_board_configuration(board_id)`.
- `jira_get_project(project_key, expand=None)`.
- `jira_delete_attachment(issue_key, attachment_id, confirm=False)`.

## Задачи

- [x] Добавить `start_at` в search client, server tool и cache key.
- [x] Добавить client methods для worklogs и changelog с нормализованной пагинацией.
- [x] Добавить client methods для fields, edit metadata и project details.
- [x] Добавить client methods для board discovery и board configuration через Agile API.
- [x] Зарегистрировать новые read tools и валидацию аргументов в `server.py`.
- [x] Реализовать проверяемое удаление attachment в client и защищенный write tool.
- [x] Расширить mock Jira server и client tests для всех новых endpoints.
- [x] Добавить server-level тесты валидации, делегирования и write guard.
- [x] Обновить README со списком и примерами новых tools.
- [x] Выполнить полный тестовый прогон и MCP smoke.

## Результат проверки

- `python -m unittest discover -s tests`: 103 теста, успешно.
- `python -m compileall -q jira_mcp tests`: успешно.
- jira-mcp проходит MCP `initialize` + `tools/list`, зарегистрировано 36 tools.
- Проверена схема `jira_search_issues.start_at` и наличие восьми новых tools.
- Read-only smoke на настроенной Jira успешно проверил fields, board list/configuration, search, project, edit metadata, worklogs и changelog fallback через issue expansion.
- Независимое повторное ревью не выявило блокирующих замечаний.

## Затронутые файлы/модули

- `jira_mcp/cache.py`
- `jira_mcp/jira_client.py`
- `jira_mcp/server.py`
- `tests/test_jira_client.py`
- `tests/test_server.py`
- `README.md`
- `docs/plans/2026-07-26-jira-mcp-missing-tools.md`

## Тест-план

### Unit и integration

- Search передает `startAt`, возвращает метаданные страницы и не смешивает cache entries разных страниц.
- Worklogs и changelog передают `startAt`/`maxResults`, возвращают элементы и корректный `is_last`.
- Fields фильтруются по id/name/clauseNames и флагу `custom_only`.
- Edit metadata возвращает все поля без фильтра и только запрошенные `field_ids` с фильтром.
- Board list передает только заданные фильтры; board configuration использует корректный Agile endpoint.
- Project details передает `expand` и возвращает payload Jira.
- Delete attachment свежим запросом читает attachments задачи, удаляет только совпавший id и инвалидирует cache.

### Server tools

- Отрицательный `start_at` отклоняется до Jira-вызова.
- Пустые issue/project/attachment identifiers отклоняются.
- `jira_delete_attachment` требует `confirm=true` и разрешенную задачу/проект.
- Валидные параметры нормализуются и без потерь передаются клиенту.

### E2E / smoke

- `python -m unittest discover -s tests` проходит полностью.
- MCP server успешно отвечает на `initialize` и `tools/list`.
- В списке tools присутствуют новые имена и обновленная схема `jira_search_issues`.

### Негативные кейсы

- Jira возвращает пустую страницу worklogs/changelog.
- Запрошенный field id отсутствует в edit metadata.
- Attachment id отсутствует в указанной задаче: удаление не выполняется.
- Jira возвращает другой issue key после lookup: удаление не выполняется.
- Jira отклоняет удаление attachment по правам: наружу возвращается существующая понятная Jira error.
- Changelog endpoint отсутствует: первая страница читается через issue expansion, а `start_at > 0` отклоняется.
- Cache включен, но запрашиваются разные `start_at`: ответы не пересекаются.

## Риски и откаты

- Риск: Jira Server разных версий не имеет отдельного changelog endpoint или использует `values`/`histories`.
  - Решение: нормализовать оба payload-варианта и fallback на `expand=changelog` для первой страницы.
- Риск: ответы `/field` и `/editmeta` могут быть большими.
  - Решение: поддержать `query`, `custom_only` и `field_ids`; не добавлять автоматическую массовую выгрузку.
- Риск: неправильный attachment id приведет к удалению чужого вложения.
  - Решение: строго валидировать issue key, сверять resolved issue key и id по свежему списку attachments до DELETE, требовать whitelist с `confirm=true` и не эскалировать 403 на другой auth source.
- Риск: добавление `start_at` изменит ключ существующего search cache.
  - Решение: новый ключ включает страницу; старые записи естественно истекут по TTL и не требуют миграции.
- Rollback: удалить новые tools/client methods и вернуть прежнюю сигнатуру поиска/cache key; write-функцию можно немедленно исключить из server tools без изменения env.
