# Quick Start Guide

## 1. File Structure Setup

Create the following directory structure:

```
epg_service/
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── models.py
│   │   └── connection.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── epg_service.py
│   │   ├── import_service.py
│   │   ├── cleanup_service.py
│   │   └── provider_service.py
│   ├── parsers/
│   │   ├── __init__.py
│   │   └── xmltv_parser.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── server.py
│   │   └── handlers.py
│   └── scheduler/
│       ├── __init__.py
│       └── jobs.py
├── config.yaml
├── requirements.txt
└── README.md
```

Copy each artifact to its corresponding file.

## 2. Installation

```bash
# Install dependencies
pip install -r requirements.txt
```

## 3. First Run

```bash
# Run the service (will create database automatically)
python -m src.main
```

The service will:
- Create `epg.db` SQLite database
- Initialize schema
- Start HTTP server on port 8080
- Start background scheduler

## 4. Add Your First Provider

```bash
# Add a provider (replace with your actual XMLTV URL)
curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My EPG Provider",
    "xmltv_url": "https://example.com/xmltv.xml"
  }'
```

Response:
```json
{
  "id": 1,
  "name": "My EPG Provider",
  "xmltv_url": "https://example.com/xmltv.xml",
  "enabled": true,
  "created_at": "2025-12-26T10:00:00",
  "updated_at": "2025-12-26T10:00:00"
}
```

## 5. Trigger First Import

```bash
# Manually trigger import (or wait for scheduled 3 AM run)
curl -X POST http://localhost:8080/api/v1/import/trigger
```

## 6. Query EPG Data

```bash
# List channels
curl http://localhost:8080/api/v1/channels

# Get programs for channel 1 (next 24 hours)
curl "http://localhost:8080/api/v1/channels/1/programs?start=2025-12-26T00:00:00Z&end=2025-12-27T00:00:00Z"
```

## Testing with Sample Data

If you don't have an XMLTV source yet, create a test file:

**test_epg.xml**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="channel1">
    <display-name>Test Channel 1</display-name>
  </channel>
  <channel id="channel2">
    <display-name>Test Channel 2</display-name>
  </channel>
  
  <programme start="20251226120000 +0000" stop="20251226130000 +0000" channel="channel1">
    <title>Test Program 1</title>
    <desc>This is a test program</desc>
    <category>News</category>
  </programme>
  
  <programme start="20251226130000 +0000" stop="20251226140000 +0000" channel="channel1">
    <title>Test Program 2</title>
    <desc>Another test program</desc>
    <category>Entertainment</category>
  </programme>
</tv>
```

Serve it locally:
```bash
# Simple HTTP server
python -m http.server 8000
```

Add provider with local URL:
```bash
curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Provider",
    "xmltv_url": "http://localhost:8000/test_epg.xml"
  }'
```

## Common Commands

### Service Management

```bash
# Start service
python -m src.main

# Start with custom config
python -m src.main /path/to/config.yaml

# Start with environment variables
EPG_SERVER_PORT=9090 EPG_LOG_LEVEL=DEBUG python -m src.main
```

### Provider Management

```bash
# List providers
curl http://localhost:8080/api/v1/providers

# Update provider
curl -X PUT http://localhost:8080/api/v1/providers/1 \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Delete provider
curl -X DELETE http://localhost:8080/api/v1/providers/1
```

### Import Management

```bash
# Trigger import
curl -X POST http://localhost:8080/api/v1/import/trigger

# Check import status
curl http://localhost:8080/api/v1/import/status
```

### Health Check

```bash
curl http://localhost:8080/api/v1/health
```

## Troubleshooting

### Port already in use
```bash
# Change port via config
EPG_SERVER_PORT=9090 python -m src.main
```

### Database locked
```bash
# Check for other processes
lsof epg.db

# Delete database and restart (WARNING: deletes all data)
rm epg.db
python -m src.main
```

### Import fails
```bash
# Check logs for errors
# Enable debug mode
EPG_LOG_LEVEL=DEBUG python -m src.main

# Verify XMLTV URL is accessible
curl -I https://your-xmltv-url.com/epg.xml
```

## Next Steps

1. **Add more providers**: Add all your EPG sources
2. **Configure retention**: Adjust `retention.days` in config.yaml
3. **Set up systemd**: For automatic startup (see README.md)
4. **Configure reverse proxy**: For production deployment
5. **Enable monitoring**: Set up log aggregation and metrics

## Production Checklist

- [ ] Change default port if needed
- [ ] Set appropriate retention period
- [ ] Configure timezone for scheduler
- [ ] Enable JSON logging for production
- [ ] Set up database backups
- [ ] Configure reverse proxy (nginx/apache)
- [ ] Set up systemd service
- [ ] Configure log rotation
- [ ] Set up monitoring/alerting
- [ ] Document your XMLTV sources