# Metadata Cleaner Service

Веб-сервис очищает метаданные изображений **PNG/JPG/JPEG**, извлекает доступные EXIF-данные и сохраняет в итоговом файле только метаданные проекта:

- `https://exception.expert`

После обработки сервис возвращает ссылку на скачивание файла. Срок хранения файла — **24 часа**.

## Возможности

- Загрузка через HTML-форму (`/`)
- Загрузка через API (`POST /api/process`, `multipart/form-data`, поле `file`)
- Извлечение EXIF/метаданных из исходного файла
- Очистка метаданных с добавлением URL проекта
- Временное хранение обработанных файлов 24 часа

## Локальный запуск (без контейнера)

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Docker

Сборка образа:

```bash
docker build -t metadata-cleaner:latest .
```

Запуск контейнера:

```bash
docker run --rm -p 8000:8000 \
  -e BASE_URL=http://localhost:8000 \
  -e TTL_HOURS=24 \
  -v $(pwd)/storage:/app/storage \
  metadata-cleaner:latest
```

Либо через compose:

```bash
docker compose up --build
```

## Пример API-запроса

```bash
curl -X POST "http://localhost:8000/api/process" \
  -F "file=@/path/to/image.jpg"
```

Пример ответа:

```json
{
  "original_filename": "image.jpg",
  "extracted_exif": {"format": "JPEG"},
  "download_url": "http://localhost:8000/download/abc123",
  "expires_at": "2026-03-29T12:00:00+00:00"
}
```

## Публикация образа в registry

Пример для Docker Hub:

```bash
docker tag metadata-cleaner:latest <dockerhub_user>/metadata-cleaner:latest
docker push <dockerhub_user>/metadata-cleaner:latest
```

Пример для GHCR:

```bash
docker tag metadata-cleaner:latest ghcr.io/<org_or_user>/metadata-cleaner:latest
docker push ghcr.io/<org_or_user>/metadata-cleaner:latest
```

> В текущем окружении может отсутствовать установленный Docker CLI/daemon, поэтому публикацию выполняйте в CI/CD или на хосте с Docker.
