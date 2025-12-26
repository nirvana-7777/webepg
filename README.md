# EPG Service

A production-grade SQLite-based Electronic Program Guide (EPG) service with HTTP API, XMLTV import, and automatic data retention management.

## Features

- **REST API**: Query EPG data via HTTP GET/PUT/DELETE
- **XMLTV Import**: Automatic import from multiple providers
- **Channel Mapping**: Map provider channel IDs to logical channels
- **Data Retention**: Automatic cleanup of old data
- **Multi-Provider Support**: Support for multiple EPG data sources
- **Production Ready**: Structured logging, error handling, thread-safe
- **Extensible**: Clean architecture with service layer separation

## Architecture

```
epg_service/
├── src/
│   ├── main.py              # Application entry point
│   ├── config.py            # Configuration management
│   ├── database/            # Database layer
│   │   ├── schema.py        # Schema and migrations
│   │   ├── models.py        # Data models
│   │   └── connection.py    # Connection pooling
│   ├── services/            # Business logic
│   │   ├── epg_service.py
│   │   ├── import_service.py
│   │   ├── cleanup_service.py
│   │   └── provider_service.py
│   ├── parsers/             # XMLTV parser
│   │   └── xmltv_parser.py
│   ├── api/                 # HTTP API
│   │   ├── server.py
│   │   └── handlers.py
│   └── scheduler/           # Background jobs
│       └── jobs.py
├── config.yaml              # Configuration file
└── requirements.txt
```

## Installation

### Prerequisites

- Python 3.8+
- pip

### Setup

```bash
# Clone repository
git clone <repository-url>
cd epg_service

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Configuration can be set via `config.yaml` or environment variables.

### config.yaml

```yaml
database:
  path: "epg.db"

server:
  host: "0.0.0.0"
  port: 8080
  debug: false
  cors_enabled: false

retention:
  days: 7

scheduler:
  import_time: "03:00"
  timezone: "UTC"

logging:
  level: "INFO"
  format: "text"
```

### Environment Variables

Environment variables override config.yaml:

- `EPG_DB_PATH` - Database file path
- `EPG_SERVER_HOST` - Server host
- `EPG_SERVER_PORT` - Server port
- `EPG_SERVER_DEBUG` - Debug mode (true/false)
- `EPG_CORS_ENABLED` - Enable CORS (true/false)
- `EPG_RETENTION_DAYS` - Data retention days
- `EPG_IMPORT_TIME` - Daily import time (HH:MM)
- `EPG_TIMEZONE` - Timezone for scheduler
- `EPG_LOG_LEVEL` - Log level (DEBUG/INFO/WARNING/ERROR)
- `EPG_LOG_FORMAT` - Log format (text/json)

## Usage

### Running the Service

```bash
# Development mode
python -m src.main

# With custom config
python -m src.main /path/to/config.yaml

# Production mode with Gunicorn
gunicorn -w 4 -b 0.0.0.0:8080 'src.main:create_app()'
```

### API Endpoints

#### Health Check
```bash
GET /api/v1/health
```

#### Channels

```bash
# List all channels
GET /api/v1/channels

# Get channel by ID
GET /api/v1/channels/{id}

# Get channel programs
GET /api/v1/channels/{id}/programs?start=2025-01-01T00:00:00Z&end=2025-01-02T00:00:00Z
```

#### Providers

```bash
# List all providers
GET /api/v1/providers

# Get provider by ID
GET /api/v1/providers/{id}

# Create provider
POST /api/v1/providers
Content-Type: application/json

{
  "name": "Provider Name",
  "xmltv_url": "https://example.com/epg.xml"
}

# Update provider
PUT /api/v1/providers/{id}
Content-Type: application/json

{
  "name": "New Name",
  "xmltv_url": "https://example.com/epg.xml",
  "enabled": true
}

# Delete provider
DELETE /api/v1/providers/{id}
```

#### Import

```bash
# Trigger manual import
POST /api/v1/import/trigger

# Get import status
GET /api/v1/import/status
```

### Example Queries

```bash
# Get EPG for channel 1 for next 24 hours
curl "http://localhost:8080/api/v1/channels/1/programs?start=$(date -u +%Y-%m-%dT%H:%M:%SZ)&end=$(date -u -d '+24 hours' +%Y-%m-%dT%H:%M:%SZ)"

# Create a provider
curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{"name": "MyProvider", "xmltv_url": "https://example.com/epg.xml"}'

# Trigger import
curl -X POST http://localhost:8080/api/v1/import/trigger
```

## Data Model

### Providers
EPG data sources with XMLTV URLs

### Channels
Logical channels (user-facing)

### Channel Mappings
Maps provider channel IDs to logical channels

### Programs
EPG program data with:
- Title, subtitle, description
- Start/end times
- Category, episode number
- Rating, actors, directors
- Icon URL

### Import Log
Tracks import operations for auditing

## Background Jobs

### Daily Import
- Runs at configured time (default: 3 AM)
- Imports XMLTV data from all enabled providers
- Creates/updates channel mappings
- Inserts new programs (skips duplicates)

### Cleanup
- Runs after each import
- Deletes programs outside retention window
- Keeps past X days and future X days
- Cleans old import logs

## Development

### Project Structure

Each module has clear responsibilities:

- **Database Layer**: Schema, models, connection management
- **Service Layer**: Business logic, isolated from API
- **API Layer**: HTTP endpoints, request handling
- **Parser**: Memory-efficient XMLTV streaming
- **Scheduler**: Background job management

### Adding a New Provider

1. Add provider via API:
```bash
curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{"name": "NewProvider", "xmltv_url": "https://..."}'
```

2. Trigger import:
```bash
curl -X POST http://localhost:8080/api/v1/import/trigger
```

The service will:
- Download XMLTV
- Create channels automatically
- Create channel mappings
- Import programs

## Production Deployment

### Systemd Service

Create `/etc/systemd/system/epg-service.service`:

```ini
[Unit]
Description=EPG Service
After=network.target

[Service]
Type=simple
User=epg
WorkingDirectory=/opt/epg-service
Environment="EPG_DB_PATH=/var/lib/epg/epg.db"
ExecStart=/usr/bin/python3 -m src.main /etc/epg/config.yaml
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable epg-service
sudo systemctl start epg-service
```

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV EPG_DB_PATH=/data/epg.db
VOLUME /data

EXPOSE 8080
CMD ["python", "-m", "src.main"]
```

Build and run:
```bash
docker build -t epg-service .
docker run -p 8080:8080 -v epg-data:/data epg-service
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name epg.example.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Performance

- **SQLite WAL mode**: Better concurrent read/write
- **Indexed queries**: Fast time-range lookups
- **Streaming parser**: Memory-efficient XMLTV parsing
- **Batch inserts**: Efficient bulk data import
- **Thread-safe connections**: Safe for concurrent requests

Expected performance:
- 100+ channels
- 10 concurrent clients
- <100ms query response time
- 1000+ programs/second import rate

## Troubleshooting

### Check logs
```bash
# Systemd
sudo journalctl -u epg-service -f

# Docker
docker logs -f <container-id>
```

### Database issues
```bash
# Check database integrity
sqlite3 epg.db "PRAGMA integrity_check;"

# Check schema version
sqlite3 epg.db "SELECT * FROM schema_version;"
```

### Import failures
```bash
# Check recent imports
curl http://localhost:8080/api/v1/import/status
```

## License

[Your License Here]

## Contributing

[Contributing Guidelines]