# План внедрения: перенести Jira auth механизм из sample-reporting-app в jira-mcp

## Цель

- Перенести в `jira-mcp` полный механизм Jira-аутентификации по образцу `sample-reporting-app`.
- Сделать новый auth flow основным для `jira-mcp`, включая runtime cookie state, persisted internal cookie source, auth fallback и browser recovery.
- Довести решение до состояния, где `jira_auth_status` и остальные MCP tools используют один и тот же устойчивый auth path.

## Не-цель

- Не сохранять полную обратную совместимость с текущей схемой env/config, если она мешает выровнять `jira-mcp` с `sample-reporting-app`.
- Не расширять функциональность MCP tools вне Jira auth/recovery scope.
- Не поддерживать дополнительные enterprise auth-схемы beyond cookie/basic/basic-with-cookies/bearer + browser recovery.
- Не строить отдельный универсальный auth framework для других сервисов.

## Критерии готовности

- `jira_auth_status` успешно проходит через новый auth layer на рабочем auth source.
- Порядок fallback `cookie -> basic_with_cookies -> basic -> bearer` реализован и покрыт тестами.
- После исчерпания обычного fallback recovery path может обновить cookie и привести повторную проверку к успеху.
- Persisted internal cookie переживает рестарт процесса и используется новым экземпляром клиента.
- `README.md` и `.env.example` отражают новую схему конфигурации и эксплуатационные шаги.

## Предпосылки и ограничения

- Источник референса: `sample-reporting-app`, прежде всего `src/SampleReportingApp/Jira/LoggingJiraGateway.cs`, `src/SampleReportingApp/Jira/JiraRuntimeAuthState.cs`, `src/SampleReportingApp/Jira/ExternalJiraBrowserRecoveryService.cs`, `src/SampleReportingApp/Configuration/JiraOptionsValidator.cs`.
- Текущий `jira-mcp` реализован на Python (`requests`, `python-dotenv`) и сейчас имеет упрощенный auth layer в `jira_mcp/config.py`, `jira_mcp/jira_client.py`, `jira_mcp/server.py`.
- Выбран полный scope переноса: internal cookie storage + fallback + browser recovery.
- Допустимы breaking changes в env/config, если итоговый runtime-поток станет близок к `sample-reporting-app`.
- План должен включать unit и integration/e2e проверку, а не только локальный smoke.
- Отдельный feature flag/compat mode для отката не требуется; откат делаем возвратом на предыдущую ревизию или временным отключением recovery/config на уровне деплоя.

## Подход

- Перенести не побуквенно код, а поведение и порядок orchestration: сначала попытка из runtime/internal cookie source, затем fallback по доступным auth modes, затем browser recovery как remediation path, затем повторная auth-проверка.
- В `jira-mcp` выделить auth слой в отдельные модули: конфиг, runtime state, recovery service, client/orchestrator.
- Сохранить одну точку входа для всех Jira API вызовов через общий request helper, чтобы `jira_auth_status`, read tools и write tools использовали одинаковый auth state и одинаковую обработку `401/403`.
- Browser recovery helper в первой итерации либо копируется в `jira-mcp` как совместимый Python-скрипт, либо подключается из внешнего пути через явный config-параметр; конкретный вариант надо выбрать до начала кодинга и зафиксировать в первой технической задаче.
- Новый auth state должен быть shared для всего процесса `jira_mcp.server`, чтобы singleton client и runtime cookie persistence не расходились по состоянию.

## Задачи

- [ ] Зафиксировать целевую auth-модель для `jira-mcp`: какие режимы поддерживаем, в каком порядке пробуем (`cookie -> basic_with_cookies -> basic -> bearer`), когда именно считаем ответ auth-failure, и как трактуем permission-level `403` в MVP.
- [ ] Выбрать способ поставки browser recovery helper: копируем скрипт в `jira-mcp`, подключаем внешний путь, или выносим в общий артефакт с совместимым JSON-контрактом.
- [ ] Спроектировать новую схему конфигурации `jira-mcp`: обязательные/опциональные env, параметры browser recovery, путь для internal cookie storage, правила валидации, логирование без утечки секретов и ожидаемые breaking changes.
- [ ] Добавить новый config schema и validation в `jira_mcp/config.py` или вынесенный config module.
- [ ] Добавить модуль runtime auth state, который умеет bootstrap internal cookie, переключать source `internal -> configured -> none`, продвигать новую cookie после recovery и переживать рестарт процесса.
- [ ] Добавить cookie-aware session handling для режима `basic_with_cookies`, чтобы session cookies переиспользовались между запросами внутри одного клиента.
- [ ] Перенести request orchestration в `JiraClient`: единый request helper, auth-attempt sequence, переключение источников, единый формат ошибок и повтор после recovery.
- [ ] Добавить browser recovery launcher/service: запуск helper script, cooldown, parsing JSON payload, обновление runtime auth state и `requests.Session` cookie state.
- [ ] Подключить новый auth/recovery path в `jira_auth_status`, read tools и write tools через общий клиент без расхождения поведения.
- [ ] Обновить `jira_mcp/server.py`, чтобы shared auth state и клиент корректно жили в рамках процесса и не создавали рассинхронизации singleton runtime state.
- [ ] Обновить документацию и `.env.example`: новая auth-схема, предварительный список env (`JIRA_AUTH_MODE`, `JIRA_COOKIE`, `JIRA_USERNAME`, `JIRA_PASSWORD`, `JIRA_TOKEN`/новый bearer key, `JIRA_ENABLE_BROWSER_RECOVERY`, `JIRA_BROWSER_RECOVERY_SCRIPT_PATH`, `JIRA_BROWSER_PROFILE_DIR`, `JIRA_INTERNAL_COOKIE_STORAGE_PATH`, `JIRA_BROWSER_RECOVERY_COOLDOWN_MINUTES`), правила эксплуатации и ручного восстановления.
- [ ] Добавить unit-тесты на config validation, auth attempt ordering, runtime cookie state, fallback между источниками, parsing recovery payload и негативные сценарии.
- [ ] Добавить integration-тесты с mock Jira server и subprocess/mock helper.
- [ ] Добавить e2e/smoke сценарий запуска `python -m jira_mcp.server` с новой конфигурацией.

## Порядок работ

- 1. Зафиксировать auth-модель, способ поставки recovery helper и новую config-схему.
- 2. Внедрить config validation и runtime auth state.
- 3. Добавить `basic_with_cookies` и общий request orchestration в `JiraClient`.
- 4. Подключить browser recovery launcher и retry-after-recovery.
- 5. Привязать новый auth path к `server.py` и всем MCP tools.
- 6. Добавить tests.
- 7. Обновить docs и выполнить smoke.

## Затронутые файлы/модули

- `jira_mcp/config.py`
- `jira_mcp/jira_client.py`
- `jira_mcp/server.py`
- `README.md`
- `.env.example`
- `requirements.txt`
- Новый модуль runtime auth state, например `jira_mcp/auth_state.py`
- Новый модуль browser recovery/orchestration, например `jira_mcp/recovery.py`
- Новый тестовый пакет, например `tests/`
- Возможный recovery helper path/скрипт, если его нужно копировать или подключать явно в `jira-mcp`

## Тест-план

- Unit:
  - проверка валидации конфигурации для всех поддерживаемых auth modes;
  - проверка bootstrap логики internal cookie vs configured cookie;
  - проверка переключения источников `internal -> configured -> none`;
  - проверка порядка auth attempts и того, что неподдерживаемые режимы не добавляются;
  - проверка parsing успешного и неуспешного recovery payload;
  - проверка, что write/read tools используют общий client path без расхождения auth behavior.
- Integration:
  - использовать `pytest` + локальный mock HTTP server для Jira API сценариев;
  - mock Jira endpoint возвращает success на первом cookie auth;
  - mock Jira endpoint возвращает `401/403` на cookie и success на `basic_with_cookies`;
  - mock Jira endpoint возвращает `401/403` на все обычные режимы, после чего recovery helper отдает новую cookie и повторный запрос проходит;
  - persisted internal cookie читается новым экземпляром клиента после рестарта процесса;
  - recovery helper возвращает invalid JSON / empty cookie / non-zero exit code и клиент отдает понятную ошибку.
- E2E / smoke:
  - запускать `python -m jira_mcp.server` как subprocess с новой конфигурацией;
  - проверить `jira_auth_status` на реальном или тестовом окружении;
  - проверить хотя бы один read tool после recovery path;
  - при наличии безопасного контура проверить, что write tool не ломает auth path и по-прежнему соблюдает whitelist/confirm.
- Негативные кейсы:
  - отсутствуют credentials для выбранного режима;
  - internal cookie файл битый или недоступен для записи;
  - browser recovery script path отсутствует;
  - cooldown блокирует повторный recovery;
  - Jira отвечает permission-level `403`, и это поведение явно документировано как текущее ограничение/MVP трактовка.

## Риски и откаты

- Риск: слишком буквальный перенос C#-поведения в Python усложнит `jira-mcp` и даст хрупкую архитектуру.
  - Митигировать: переносить контракт и orchestration, а не структуру классов один в один.
- Риск: breaking changes в env/config сломают текущие локальные/CI конфиги.
  - Митигировать: заранее описать новую схему в `README.md` и `.env.example`, добавить явные ошибки валидации на старте.
- Риск: browser recovery потребует дополнительных системных зависимостей и нестабилен в headless-среде.
  - Митигировать: задокументировать зависимости, покрыть helper contract integration-тестом, отделить failure recovery от базового request path.
- Риск: persisted internal cookie станет stale source и будет мешать fallback.
  - Митигировать: реализовать явное переключение `internal -> configured -> none` и логирование выбранного source.
- Риск: новый auth flow начнет трактовать все `403` как auth-failure и маскировать permission errors.
  - Митигировать: зафиксировать это как MVP-поведение, покрыть тестом и отдельно отметить в docs как known limitation.
- Риск: shared runtime state и singleton client в `server.py` рассинхронизируются.
  - Митигировать: явно спроектировать общий mutable auth state и покрыть его тестом/smoke после смены cookie source.
- Roll-back:
  - если внедрение неудачно, откат делается возвратом `jira-mcp` к предыдущему коммиту/ревизии;
  - на уровне runtime можно временно выключить browser recovery и запускать только один проверенный auth mode, если это потребуется для стабилизации до полного отката.
