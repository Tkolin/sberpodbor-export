# СберПодбор — карта внутреннего API (api.sberpodbor.ru)

Хост: `https://api.sberpodbor.ru`. Все data-запросы требуют заголовок
`Authorization: Bearer <access_token>`.


## Авторизация
- **Логин (канонический, UI):** `POST /v2/auth/login` → 201
  Body (application/json): `{"data":{"attributes":{"login":"<email>","password":"<pass>"}}}`
  Ответ: `data.attributes` → `accessToken` (~8ч), `accessTokenExpiryAt`,
  `refreshToken` (~3 дня), `refreshTokenExpiryAt`.
  **Именно этот эндпоинт снимает анти-абуз 401-блок** (полный ре-логин). Использовать его.
- Альт. логин: `POST /v1/auth/login.json` body `{"request":{"auth":{"login","password"}}}`
  (отдаёт токены в `response.data.attributes.access_token`, snake_case) — токены мятые им
  в разведке блок НЕ снимали; предпочтителен `/v2/auth/login`.
- **ВАЖНО:** новый логин **инвалидирует предыдущий токен** — один активный токен на пользователя.
  Нельзя логиниться параллельно; браузерная сессия человека при этом разлогинивается.

## Данные
| Что | Метод / URL | Ключевые поля |
|---|---|---|
| Профили (база, 266 336) | `POST /v2/applicant_profiles/list` body `{"data":{"attributes":{"page":N,"itemsPerPage":50}}}` (vnd.api+json) | `meta.totalItems`; `data[].attributes`: `_id, firstName, lastName, middleName, phone, email, city, avatar, currentWork{position,company,startedAt,finishedAt,experience}, vacancies:[{id,title,candidateId}]` |
| Вакансии (1117) | `GET /v2/vacancies?page=N&itemsPerPage=50` | `_id, title, status, city, personsCount, desiredClosingAt, createdAt, comment` |
| Пользователи (авторы) | `GET /v2/users?page=N&itemsPerPage=50` | `_id, firstName, lastName, middleName, status, position, role, email` |
| Резюме кандидата | `GET /v2/applicant_profiles/{profileId}/resumes/last?include=source` | `attributes.body` (HTML), `included` → Source (linkedin/hh/...) |
| Заявки профиля (статус/рекрутеры) | `GET /v2/applicant_profiles/{profileId}/candidates_list?page=1&itemsPerPage=50` | `candidateId, candidateStatusId, candidateStatusTitle, candidateCreatedAt, vacancyId, vacancyTitle, recruiters[], managers[]` |
| Кандидат → контакты | `GET /v2/candidates/{candidateId}` | `relationships.profileContacts.data[].id` (напр. `/v2/profile_contacts/33744776`) |
| Контакт/соцсеть | `GET /v2/profile_contacts/{id}` | `type` (linkedin/telegram/...), `value`, `isMain` |
| **Лог заявки (п.3)** | `GET /v2/candidates/{candidateId}/logs` | `data[].attributes`: `dateTimeAt, message` («Прикреплён к вакансии…», «Изменён статус: …»), **`userFullName` — автор строкой** |
| **Комментарии (п.4)** | `GET /v2/candidates/{candidateId}/comments?include=user` | `attributes`: `comment, createdAt, changedAt, files, mentions`; `relationships.user.data.id`; `included[]` → User (firstName/lastName автора) |

## Ограничения (обнаружено на практике)
- **Троттлинг латентностью, НЕ счётчиком (измерено).** 2300+ data-запросов подряд при 4–5 rps —
  ноль 401. Сервер держит ~1.3 с/запрос (при concurrency 20 — ~2.2 с). Потолок ~**4–5 rps**;
  выше параллельность лишь растит задержку. Оптимум: concurrency ~8.
- **Анти-абуз 401-блок = злоупотребление ЛОГИНАМИ, не data-объёмом.** Прилетает после серии
  быстрых логинов подряд; ответ 401 на ВСЕ data-эндпоинты (на уровне приложения — `x-request-id`
  бэкенда, без rate-limit/`retry-after`/WAF). Свежий токен из `/v1/auth/login.json` блок НЕ снимал.
  **Снимается полным ре-логином через `POST /v2/auth/login`** (проверено: после него UI и API 200).
  → логиниться редко (1 токен на 8ч), НЕ крутить логины в цикле.
- **Глубина пагинации `applicant_profiles/list`: предела НЕТ (проверено).**
  Страницы 60, 61, 100, 500, 1000 и последняя **5327** отдают 200 с полными данными, `total≈266347`.
  Вся база достаётся по номерам страниц; обход по `id` не требуется.

## Единицы объёма (пример: компания с ~266 тыс. профилей)
- Профили: 266 336 → 5 327 страниц по 50.
- Заявок (candidateId) ~ сопоставимо/больше числа профилей → логи+комментарии по каждой = основная масса запросов.
- Оценка полного прогона: ~0.6–0.8 млн запросов. При 4 rps ≈ 40–55 часов чистого времени.
