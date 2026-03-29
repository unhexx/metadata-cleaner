# План разработки веб‑сервиса очистки EXIF/C2PA метаданных

## 1) Цель и критерии готовности
Разработать англоязычный веб‑сервис, который:
- принимает PNG/JPG/JPEG через Web UI и REST API;
- извлекает максимально полный набор метаданных (EXIF/XMP/IPTC/PNG chunks/C2PA при наличии);
- формирует очищенный файл, в котором в метаданных сохранён только URL проекта: `https://exception.expert`;
- сохраняет результат во временное хранилище на 24 часа;
- возвращает ссылку на скачивание очищенного файла;
- поставляется как готовый Docker image, опубликованный в реестре.

Готовность (Definition of Done):
- [ ] API и UI работают в контейнере.
- [ ] Автотесты проходят.
- [ ] Есть интеграционный API smoke test с тестовым изображением.
- [ ] Настроено удаление файлов старше 24 часов.
- [ ] Собран и опубликован Docker image (multi-arch по возможности).
- [ ] README содержит инструкции запуска, тестирования и публикации.

---

## 2) Рекомендуемый стек
- Backend: **Python 3.12 + FastAPI + Uvicorn**.
- HTML UI: Jinja2 (минимальная форма загрузки).
- Извлечение/очистка метаданных:
  - `exiftool` (наиболее полный парсер, включая C2PA/JUMBF),
  - `Pillow`/`piexif` как fallback для базовых EXIF‑операций.
- Хранение временных файлов: локальная папка `storage/` (или S3‑совместимое хранилище при необходимости).
- Очистка TTL: фоновый scheduler внутри сервиса (APScheduler) или отдельный lightweight worker.
- Контейнеризация: Docker + docker-compose.
- Тесты: pytest + httpx (API) + optional playwright (UI smoke).

---

## 3) Архитектура (минимальная)

### Компоненты
1. **Upload API**
   - `POST /api/v1/upload`
   - multipart upload (`file`)
   - валидация MIME/extension/размера
   - запуск пайплайна: анализ → очистка → сохранение → ссылка

2. **Metadata API**
   - `POST /api/v1/analyze`
   - возвращает JSON с полным разбором метаданных (включая C2PA поля и статус верификации, если доступны)

3. **Download API**
   - `GET /api/v1/download/{token}`
   - отдаёт очищенный файл до истечения TTL

4. **Web UI**
   - `GET /` форма загрузки
   - показывает: исходные ключевые метаданные, статус очистки, ссылку на скачивание, время истечения

5. **Retention/Cleanup**
   - периодическая задача удаляет файлы и записи старше 24 часов

### Поток данных
`Upload` → `Temporary Input` → `Metadata Extractor` → `C2PA Verifier` → `Sanitizer` → `Temporary Output` → `Signed/Random Download Token` → `Client`

---

## 4) Threat model и privacy requirements

### 4.1 Активы
- Загружаемые изображения пользователей.
- Метаданные (могут содержать PII: GPS, автора, device IDs, timestamps).
- Очищенные файлы и временные download-токены.
- Логи/трейсы сервиса.

### 4.2 Основные угрозы
- **Malicious upload**: попытки загрузить не-изображение/полиглот/zip bomb.
- **Path traversal / filename injection** через имя файла.
- **Command injection** при вызове `exiftool`.
- **Token guessing** для доступа к чужим файлам.
- **Metadata leakage** (PII) в очищенном файле или в логах.
- **DoS** крупными файлами/частыми запросами.
- **Retention violation**: файл живёт дольше 24 часов.

### 4.3 Обязательные меры защиты
- Разрешённые типы: только PNG/JPEG по MIME+magic bytes.
- Размерный лимит (рекомендуемо 20 MB, финально согласовать).
- Случайные внутренние имена (UUID), без использования исходного имени в path.
- Вызов `exiftool` только как `subprocess` со списком аргументов (без shell).
- Таймауты и ограничение CPU/памяти контейнера.
- Токены скачивания: минимум 128 бит энтропии + HMAC/подпись.
- Rate limit на API и базовая защита от burst.
- Политика логирования: не логировать исходные метаданные целиком и не хранить чувствительные поля.
- Шифрование in-transit (TLS на ingress/reverse proxy).

### 4.4 Privacy requirements
- Принцип минимизации данных: хранить только то, что нужно для работы и удаления через 24 часа.
- По умолчанию не сохранять исходные файлы дольше окна обработки (если не требуется повторный анализ).
- В очищенном файле должно оставаться только поле с URL `https://exception.expert`.
- После истечения TTL удалять файл и запись о токене (hard delete).
- Явно зафиксировать политику хранения в README/Privacy Notice.

---

## 5) Детализация функционала

### 5.1 Валидация входных файлов
- Разрешить только `.png`, `.jpg`, `.jpeg`.
- Проверять extension, фактический MIME и signature.
- Ограничить размер (например, 20MB).
- Генерировать безопасные внутренние имена файлов (UUID).

### 5.2 Извлечение метаданных
- Выполнять `exiftool -j -G1 -a -u -n <file>`.
- Для PNG дополнительно собирать заметные блоки (`IHDR`, `tEXt`, `iTXt`, `zTXt`, C2PA/JUMBF при наличии).
- Формировать нормализованный JSON-ответ с группировкой:
  - file system attrs,
  - technical image attrs,
  - EXIF/XMP/IPTC,
  - C2PA/Content Credentials,
  - verification summary.

### 5.3 C2PA/JUMBF handling и verification (обязательно)
- Детектировать наличие C2PA через поля JUMBF/C2PA (`JUMDLabel`, `ActiveManifest*`, `ClaimSignature*`, `ValidationResults*`).
- Сохранять в ответе сервиса:
  - `c2pa.present` (bool),
  - `c2pa.active_manifest_uri`,
  - `c2pa.actions` (если доступны),
  - `c2pa.generator`, `c2pa.software_agent`, `c2pa.digital_source_type`,
  - `c2pa.validation.success_codes[]`,
  - `c2pa.validation.failure_codes[]`,
  - `c2pa.validation.integrity_ok` (bool),
  - `c2pa.validation.signing_credential_trusted` (bool).
- Нормализовать итог проверки в три статуса:
  1) `valid_and_trusted`,
  2) `valid_but_untrusted_cert`,
  3) `invalid_or_missing`.
- Важно: после санитизации C2PA, как правило, станет недействительным или удалится — это ожидаемое поведение и должно явно возвращаться в ответе API.

### 5.4 Очистка и перезапись метаданных
- Цель: удалить всё, оставить только поле с URL проекта.
- Рекомендуемый алгоритм:
  1) удалить существующие метаданные (`-all=`);
  2) записать безопасное поле (например `XMP-dc:Source` или `XMP-dc:Rights`) со значением `https://exception.expert`;
  3) выполнить повторное чтение и whitelist‑проверку итоговых метаданных.
- Для PNG/JPEG применять единый интерфейс `sanitize_image(file_in) -> file_out`.

### 5.5 Генерация ссылки на скачивание
- Формат: случайный токен (UUIDv4 + HMAC при необходимости).
- Срок жизни: ровно 24 часа с момента обработки.
- Хранить маппинг `token -> file_path, expires_at` (SQLite/JSON/Redis).

### 5.6 Политика хранения 24 часа
- Cleanup job раз в 10–30 минут.
- При скачивании проверять TTL; просроченные удалять/блокировать (410 Gone).

---

## 6) Success metrics и expected load

### 6.1 SLA/SLO (стартовые)
- API availability: **99.5%+** (месячно).
- `POST /api/v1/upload` p95 latency:
  - до 5 MB: **< 2 сек**,
  - до 20 MB: **< 5 сек**.
- `POST /api/v1/analyze` p95 latency:
  - до 5 MB: **< 1.5 сек**,
  - до 20 MB: **< 4 сек**.
- Error rate (5xx): **< 1%**.

### 6.2 Ожидаемая нагрузка (базовый профиль)
- 5–20 RPS средняя, пиковая до 50 RPS.
- Одновременные загрузки: до 100.
- Средний размер файла: 1–5 MB, максимум 20 MB.
- Целевой объём временного хранилища: минимум 50–100 GB (зависит от трафика и TTL).

### 6.3 Бизнес-метрики
- Доля успешных обработок (`upload_success_rate`) > 98%.
- Доля корректно очищенных файлов (`sanitize_verification_pass_rate`) > 99%.
- Доля файлов, удалённых в срок (`ttl_deletion_sla`) > 99.9%.

### 6.4 Наблюдаемость
- Метрики Prometheus: RPS, latency, 4xx/5xx, queue time, cleanup lag.
- Алерты: рост 5xx, рост времени ответа, backlog cleanup, дефицит диска.

---

## 7) Контракты API (черновик)

### `POST /api/v1/upload`
**Request:** multipart/form-data, поле `file`.

**Response 201:**
```json
{
  "file_id": "uuid",
  "download_url": "/api/v1/download/<token>",
  "expires_at": "2026-03-30T12:00:00Z",
  "project_url": "https://exception.expert",
  "sanitized_metadata": {
    "xmp:source": "https://exception.expert"
  },
  "c2pa_after_sanitize": {
    "status": "invalid_or_missing",
    "details": "expected_after_metadata_rewrite"
  }
}
```

### `POST /api/v1/analyze`
**Response 200:** полный JSON с метаданными и C2PA verification summary.

### `GET /api/v1/download/{token}`
- `200` + файл
- `404` если токен неизвестен
- `410` если срок истёк

---

## 8) План работ для локальных Codex-агентов

### Agent A — Scaffold & API
1. Создать структуру проекта (`app/`, `app/api`, `app/services`, `app/templates`, `tests/`).
2. Реализовать FastAPI app, роуты `/`, `/api/v1/upload`, `/api/v1/analyze`, `/api/v1/download/{token}`.
3. Добавить базовую валидацию и схемы Pydantic.

### Agent B — Metadata & C2PA Engine
1. Реализовать сервис вызова `exiftool` с таймаутами и безопасной обработкой ошибок.
2. Реализовать нормализатор метаданных и C2PA verification summary.
3. Добавить unit tests на C2PA-статусы: trusted / untrusted cert / invalid.

### Agent C — Sanitization Engine
1. Реализовать пайплайн очистки (`-all=` + запись `https://exception.expert`).
2. Добавить пост‑проверку: после очистки в метаданных не должно быть ничего лишнего.
3. Подготовить негативные тесты (битый файл, неподдерживаемый MIME).

### Agent D — Security, Storage & TTL
1. Реализовать реестр артефактов (SQLite).
2. Реализовать токены скачивания с достаточной энтропией и проверкой срока.
3. Реализовать периодический cleanup + тесты TTL + тесты удаления по hard-delete.

### Agent E — UI, Privacy & Docs
1. Создать английский UI (upload form + result page).
2. Добавить понятные статусы C2PA verification в UI.
3. Описать API/Privacy/Retention в README + примеры `curl`.

### Agent F — Container, Perf & Release
1. Собрать production Dockerfile (multi-stage).
2. Добавить `docker-compose.yml` и лимиты ресурсов.
3. Настроить CI: lint + tests + image build + push + performance smoke.

---

## 9) Структура репозитория (целевая)
```text
.
├─ app/
│  ├─ main.py
│  ├─ api/
│  ├─ services/
│  │  ├─ metadata_extractor.py
│  │  ├─ c2pa_verifier.py
│  │  ├─ sanitizer.py
│  │  ├─ storage.py
│  │  └─ cleanup.py
│  ├─ templates/
│  └─ static/
├─ tests/
├─ storage/
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
└─ README.md
```

---

## 10) Тестовый план

### Unit
- Валидация расширений/MIME/magic bytes.
- Нормализация output exiftool.
- Классификация C2PA verification status.
- Sanitize pipeline (до/после).
- TTL logic + hard-delete.

### Integration (API)
1. `POST /api/v1/upload` с PNG/JPEG → `201`, есть `download_url`.
2. `POST /api/v1/analyze` для файла с C2PA → корректный verification summary.
3. Повторный анализ очищенного файла → только `https://exception.expert` в метаданных.
4. `GET download_url` → файл скачивается.
5. После искусственного истечения TTL → `410`.

### Load/Performance
- Smoke нагрузка: 20 RPS, 5 минут, доля ошибок < 1%.
- Пиковый тест: 50 RPS (короткий burst), без деградации cleanup и утечек памяти.

### E2E (контейнер)
- Поднять `docker compose up`.
- Прогнать smoke через `curl` на приложенном тестовом изображении.

---

## 11) CI/CD и публикация Docker image
1. CI pipeline:
   - lint (ruff/flake8),
   - tests (pytest),
   - build image,
   - security scan (trivy, optional),
   - push в registry (GHCR/Docker Hub).
2. Теги образа:
   - `latest`,
   - `vX.Y.Z`,
   - commit SHA.
3. Публикация:
   - настроить `REGISTRY_USERNAME`, `REGISTRY_TOKEN` в secrets,
   - документировать команду pull/run.

---

## 12) Риски и меры
- **ExifTool недоступен в образе** → добавить установку в Dockerfile и healthcheck.
- **Различия PNG/JPEG metadata-поведения** → отдельные тест‑кейсы для обоих форматов.
- **Большие файлы/DoS** → лимиты размера, таймауты subprocess, rate limiting.
- **Потеря прозрачности PNG** → проверка pixel-diff/alpha-preservation в тестах.
- **Непредсказуемые C2PA поля** → хранить raw dump + best-effort parsing + унифицированный статус.

---

## 13) Definition of Ready для старта агентами
Перед параллельной работой зафиксировать:
- единый контракт API;
- выбранный registry для публикации образа;
- лимит размера файла;
- место хранения временных файлов (volume/S3);
- минимальный набор обязательных метаданных, который остаётся после очистки (только URL проекта);
- целевые SLO и профиль нагрузки для первой версии.

---

## 14) Минимальные команды приёмки (для исполнителя)
```bash
# build
docker build -t metadata-cleaner:local .

# run
docker run --rm -p 8000:8000 metadata-cleaner:local

# analyze
curl -F "file=@test.png" http://localhost:8000/api/v1/analyze

# upload + sanitize
curl -F "file=@test.png" http://localhost:8000/api/v1/upload

# download by token
curl -L "http://localhost:8000/api/v1/download/<token>" -o cleaned.png
```
