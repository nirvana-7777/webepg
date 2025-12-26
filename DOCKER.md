# Docker Deployment Guide

## Quick Start

### 1. Build and Run with Docker Compose

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Stop and remove data
docker-compose down -v
```

### 2. Build and Run with Docker

```bash
# Build image
docker build -t epg-service .

# Run container
docker run -d \
  --name epg-service \
  -p 8080:8080 \
  -v epg-data:/data \
  epg-service

# View logs
docker logs -f epg-service

# Stop and remove
docker stop epg-service
docker rm epg-service
```

## Configuration

### Environment Variables

All configuration can be set via environment variables in `docker-compose.yml`:

```yaml
environment:
  - EPG_DB_PATH=/data/epg.db
  - EPG_SERVER_PORT=8080
  - EPG_RETENTION_DAYS=7
  - EPG_IMPORT_TIME=03:00
  - EPG_TIMEZONE=UTC
  - EPG_LOG_LEVEL=INFO
```

### Custom Config File

Mount a custom config file:

```yaml
volumes:
  - ./my-config.yaml:/app/config.yaml:ro
```

## Usage

### Add Provider

```bash
docker exec epg-service curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{"name": "MyProvider", "xmltv_url": "https://example.com/epg.xml"}'
```

Or from host machine:

```bash
curl -X POST http://localhost:8080/api/v1/providers \
  -H "Content-Type: application/json" \
  -d '{"name": "MyProvider", "xmltv_url": "https://example.com/epg.xml"}'
```

### Trigger Import

```bash
curl -X POST http://localhost:8080/api/v1/import/trigger
```

### Query EPG

```bash
# List channels
curl http://localhost:8080/api/v1/channels

# Get programs
curl "http://localhost:8080/api/v1/channels/1/programs?start=2025-12-26T00:00:00Z&end=2025-12-27T00:00:00Z"
```

## Data Persistence

Data is stored in a Docker volume:

```bash
# List volumes
docker volume ls

# Inspect volume
docker volume inspect epg-service_epg-data

# Backup database
docker exec epg-service sqlite3 /data/epg.db ".backup /data/backup.db"
docker cp epg-service:/data/backup.db ./backup.db

# Restore database
docker cp ./backup.db epg-service:/data/epg.db
docker-compose restart
```

## Monitoring

### View Logs

```bash
# Follow logs
docker-compose logs -f epg-service

# Last 100 lines
docker-compose logs --tail=100 epg-service

# Since specific time
docker-compose logs --since 2025-12-26T10:00:00 epg-service
```

### Check Health

```bash
# Container health status
docker-compose ps

# API health check
curl http://localhost:8080/api/v1/health

# Import status
curl http://localhost:8080/api/v1/import/status
```

### Container Stats

```bash
# Real-time stats
docker stats epg-service

# Container info
docker inspect epg-service
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs epg-service

# Check if port is in use
sudo netstat -tulpn | grep 8080

# Use different port
# Edit docker-compose.yml: "8081:8080"
```

### Database Locked

```bash
# Restart container
docker-compose restart epg-service

# If persistent, remove database and restart
docker-compose down
docker volume rm epg-service_epg-data
docker-compose up -d
```

### Import Failures

```bash
# Check logs for errors
docker-compose logs epg-service | grep -i error

# Test XMLTV URL accessibility from container
docker exec epg-service curl -I https://your-xmltv-url.com/epg.xml

# Manually trigger import with debug
docker exec epg-service curl -X POST http://localhost:8080/api/v1/import/trigger
```

### High Memory Usage

```bash
# Limit memory in docker-compose.yml
services:
  epg-service:
    deploy:
      resources:
        limits:
          memory: 512M
```

## Production Setup

### With Nginx Reverse Proxy

Create `nginx.conf`:

```nginx
server {
    listen 80;
    server_name epg.yourdomain.com;

    location / {
        proxy_pass http://epg-service:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Update `docker-compose.yml`:

```yaml
services:
  epg-service:
    # ... existing config ...
    expose:
      - "8080"
    # Remove ports section

  nginx:
    image: nginx:alpine
    container_name: epg-nginx
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - epg-service
    networks:
      - epg-network
```

### With SSL (Let's Encrypt)

Add to `docker-compose.yml`:

```yaml
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certbot/conf:/etc/letsencrypt:ro
      - ./certbot/www:/var/www/certbot:ro

  certbot:
    image: certbot/certbot
    volumes:
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    entrypoint: "/bin/sh -c 'trap exit TERM; while :; do certbot renew; sleep 12h & wait $${!}; done;'"
```

### Resource Limits

```yaml
services:
  epg-service:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

### Logging Configuration

```yaml
services:
  epg-service:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Multi-Container Setup

For multiple EPG services with shared database:

```yaml
services:
  epg-service-1:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - epg-data:/data

  epg-service-2:
    build: .
    ports:
      - "8081:8080"
    volumes:
      - epg-data:/data
    environment:
      - EPG_IMPORT_TIME=04:00  # Different import time

volumes:
  epg-data:
```

## Development Mode

For development with hot-reload:

```yaml
services:
  epg-service-dev:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./src:/app/src
      - epg-data:/data
    environment:
      - EPG_SERVER_DEBUG=true
      - EPG_LOG_LEVEL=DEBUG
    command: python -m src.main
```

## Backup and Restore

### Automated Backup Script

Create `backup.sh`:

```bash
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="./backups"
mkdir -p $BACKUP_DIR

docker exec epg-service sqlite3 /data/epg.db ".backup /data/backup_${DATE}.db"
docker cp epg-service:/data/backup_${DATE}.db ${BACKUP_DIR}/epg_${DATE}.db
docker exec epg-service rm /data/backup_${DATE}.db

echo "Backup created: ${BACKUP_DIR}/epg_${DATE}.db"
```

### Restore from Backup

```bash
docker cp ./backups/epg_20251226_120000.db epg-service:/data/epg.db
docker-compose restart epg-service
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Build and Push Docker Image

on:
  push:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Build Docker image
        run: docker build -t myregistry/epg-service:latest .
      
      - name: Push to registry
        run: |
          echo ${{ secrets.DOCKER_PASSWORD }} | docker login -u ${{ secrets.DOCKER_USERNAME }} --password-stdin
          docker push myregistry/epg-service:latest
```

## Common Commands Reference

```bash
# Build
docker-compose build

# Start
docker-compose up -d

# Stop
docker-compose down

# Restart
docker-compose restart

# View logs
docker-compose logs -f

# Execute command in container
docker-compose exec epg-service bash

# Update image
docker-compose pull
docker-compose up -d

# Clean up
docker-compose down -v --rmi all

# Database shell
docker exec -it epg-service sqlite3 /data/epg.db
```