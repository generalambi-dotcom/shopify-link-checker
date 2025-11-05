# Docker Setup Guide

## Quick Start

### 1. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit with your Shopify token
nano .env
# or
vim .env
```

### 2. Start the Service

```bash
# Simple setup (web UI only)
docker-compose up -d

# Production setup (with Nginx)
docker-compose -f docker-compose.prod.yml up -d
```

### 3. Access the Application

- **Web UI**: http://localhost:8000
- **With Nginx**: http://localhost:80
- **Health Check**: http://localhost:8000/health

---

## Production Deployment

### Step 1: Create Environment File

```bash
cat > .env << 'EOF'
SHOPIFY_TOKEN=shpat_your_actual_token_here
LOG_LEVEL=INFO
EOF
```

### Step 2: Create Output Directories

```bash
mkdir -p output logs
chmod 755 output logs
```

### Step 3: Start Production Stack

```bash
docker-compose -f docker-compose.prod.yml up -d
```

### Step 4: Verify

```bash
# Check containers
docker-compose -f docker-compose.prod.yml ps

# Check health
curl http://localhost/health

# View logs
docker-compose -f docker-compose.prod.yml logs -f
```

---

## Maintenance Commands

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f shopify-link-checker

# Last 100 lines
docker-compose logs --tail=100 shopify-link-checker
```

### Restart Service

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart shopify-link-checker
```

### Update Application

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose down
docker-compose up -d --build
```

### Backup Data

```bash
# Backup output files
tar -czf backup-$(date +%Y%m%d).tar.gz output/ logs/

# Backup with Docker volume
docker run --rm -v shopify-link-checker_output:/data -v $(pwd):/backup alpine tar -czf /backup/output-backup.tar.gz /data
```

---

## Monitoring

### Container Stats

```bash
# Real-time resource usage
docker stats shopify-link-checker

# Detailed info
docker inspect shopify-link-checker
```

### Health Status

```bash
# Check health status
docker inspect --format='{{.State.Health.Status}}' shopify-link-checker

# Automated monitoring
watch -n 5 'curl -s http://localhost:8000/health'
```

---

## Scaling (Advanced)

### Multiple Workers

```bash
# Run multiple instances behind load balancer
docker-compose -f docker-compose.prod.yml up -d --scale shopify-link-checker=3
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs shopify-link-checker

# Check Docker daemon
systemctl status docker

# Remove and recreate
docker-compose down -v
docker-compose up -d
```

### Port Already in Use

```bash
# Find what's using the port
sudo lsof -i :8000

# Change port in docker-compose.yml
# Then restart
```

### Out of Disk Space

```bash
# Clean up Docker
docker system prune -a

# Remove old images
docker image prune -a

# Remove unused volumes
docker volume prune
```

### Permission Denied

```bash
# Fix permissions
sudo chown -R $(id -u):$(id -g) output/ logs/

# Or run as root
docker-compose run --user root shopify-link-checker bash
```

---

## Security Best Practices

1. **Never commit .env file**
   ```bash
   echo ".env" >> .gitignore
   ```

2. **Use secrets management**
   ```bash
   # For Docker Swarm
   echo "shpat_token" | docker secret create shopify_token -
   ```

3. **Run as non-root** (already configured in Dockerfile)

4. **Use HTTPS** (configure SSL in nginx.conf)

5. **Restrict network access**
   ```yaml
   networks:
     shopify-network:
       driver: bridge
       internal: true  # No external access
   ```

---

## Uninstalling

```bash
# Stop and remove containers
docker-compose down

# Remove volumes and data
docker-compose down -v
rm -rf output/ logs/

# Remove images
docker rmi shopify-link-checker
```
