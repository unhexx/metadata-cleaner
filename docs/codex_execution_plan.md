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
  - `exiftool` (наиболее полный парсер, включая C2PA/JUMBF, где возможно),
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
   - возвращает JSON с полным разбором метаданных (включая C2PA поля, если доступны)

3. **Download API**
   - `GET /api/v1/download/{token}`
   - отдаёт очищенный файл до истечения TTL

4. **Web UI**
   - `GET /` форма загрузки
   - показывает: исходные ключевые метаданные, статус очистки, ссылку на скачивание, время истечения

5. **Retention/Cleanup**
   - периодическая задача удаляет файлы и записи старше 24 часов

### Поток данных
`Upload` → `Temporary Input` → `Metadata Extractor` → `Sanitizer` → `Temporary Output` → `Signed/Random Download Token` → `Client`

---

## 4) Детализация функционала

### 4.1 Валидация входных файлов
- Разрешить только `.png`, `.jpg`, `.jpeg`.
- Проверять и extension, и фактический MIME/signature.
- Ограничить размер (например, 20MB).
- Генерировать безопасные внутренние имена файлов (UUID).

### 4.2 Извлечение метаданных
- Выполнять `exiftool -j -G1 -a -u -n <file>`.
- Для PNG дополнительно собирать заметные блоки (`IHDR`, `tEXt`, `iTXt`, `zTXt`, C2PA/JUMBF если отображается).
- Формировать нормализованный JSON-ответ с группировкой:
  - file system attrs,
  - technical image attrs,
  - EXIF/XMP/IPTC,
  - C2PA/Content Credentials,
  - validation summary.

### 4.3 Очистка и перезапись метаданных
- Цель: удалить всё, оставить только поле с URL проекта.
- Рекомендуемый алгоритм:
  1) удалить существующие метаданные (`-all=`);
  2) записать безопасное поле (например `XMP-dc:Source` или `XMP-dc:Rights`) со значением `https://exception.expert`;
  3) убедиться повторным чтением, что других метаданных нет.
- Для PNG/JPEG применять единый интерфейс `sanitize_image(file_in) -> file_out`.

### 4.4 Генерация ссылки на скачивание
- Формат: случайный токен (UUIDv4 + HMAC при необходимости).
- Срок жизни: ровно 24 часа с момента обработки.
- Хранить маппинг `token -> file_path, expires_at` (SQLite/JSON/Redis).

### 4.5 Политика хранения 24 часа
- Cleanup job раз в 10–30 минут.
- При скачивании проверять TTL; просроченные удалять/блокировать (410 Gone).

---

## 5) Контракты API (черновик)

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
  }
}
```

### `POST /api/v1/analyze`
**Response 200:** полный JSON с метаданными и сводкой.

### `GET /api/v1/download/{token}`
- `200` + файл
- `404` если токен неизвестен
- `410` если срок истёк

---

## 6) План работ для локальных Codex-агентов

### Agent A — Scaffold & API
1. Создать структуру проекта (`app/`, `app/api`, `app/services`, `app/templates`, `tests/`).
2. Реализовать FastAPI app, роуты `/`, `/api/v1/upload`, `/api/v1/analyze`, `/api/v1/download/{token}`.
3. Добавить базовую валидацию и схемы Pydantic.

### Agent B — Metadata Engine
1. Реализовать сервис вызова `exiftool` с таймаутами и безопасной обработкой ошибок.
2. Реализовать нормализатор метаданных (в т.ч. секции C2PA при наличии полей).
3. Добавить unit tests на парсинг и edge-cases.

### Agent C — Sanitization Engine
1. Реализовать пайплайн очистки (`-all=` + запись `https://exception.expert`).
2. Добавить пост‑проверку: после очистки в метаданных не должно быть ничего лишнего.
3. Подготовить негативные тесты (битый файл, неподдерживаемый MIME).

### Agent D — Storage & TTL
1. Реализовать реестр артефактов (SQLite).
2. Реализовать генерацию токенов, выдачу ссылок, проверку `expires_at`.
3. Реализовать периодический cleanup + тесты TTL.

### Agent E — UI & Docs
1. Создать английский UI (upload form + result page).
2. Описать API в OpenAPI/README + примеры `curl`.
3. Добавить разделы: local run, docker run, troubleshooting.

### Agent F — Container & Release
1. Собрать production Dockerfile (multi-stage).
2. Добавить `docker-compose.yml` для локальной проверки.
3. Настроить CI: lint + tests + image build + push registry.

---

## 7) Структура репозитория (целевая)
```text
.
├─ app/
│  ├─ main.py
│  ├─ api/
│  ├─ services/
│  │  ├─ metadata_extractor.py
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

## 8) Тестовый план

### Unit
- Валидация расширений/MIME.
- Нормализация output exiftool.
- Sanitize pipeline (до/после).
- TTL logic.

### Integration (API)
1. `POST /api/v1/upload` с PNG/JPEG → `201`, есть `download_url`.
2. `GET download_url` → файл скачивается.
3. Повторный анализ скачанного файла → только `https://exception.expert` в метаданных.
4. После искусственного истечения TTL → `410`.

### E2E (контейнер)
- Поднять `docker compose up`.
- Прогнать smoke через `curl` на приложенном тестовом изображении.

---

## 9) CI/CD и публикация Docker image
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

## 10) Риски и меры
- **ExifTool недоступен в образе** → добавить установку в Dockerfile и healthcheck.
- **Различия PNG/JPEG metadata-поведения** → отдельные тест‑кейсы для обоих форматов.
- **Большие файлы/DoS** → лимиты размера, таймауты subprocess, rate limiting.
- **Потеря прозрачности PNG** → проверка pixel-diff/alpha-preservation в тестах.
- **Непредсказуемые C2PA поля** → хранить raw dump + best-effort parsing.

---

## 11) Definition of Ready для старта агентами
Перед параллельной работой зафиксировать:
- единый контракт API;
- выбранный registry для публикации образа;
- лимит размера файла;
- место хранения временных файлов (volume/S3);
- минимальный набор обязательных метаданных, который остаётся после очистки (только URL проекта).

---

## 12) Минимальные команды приёмки (для исполнителя)
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

