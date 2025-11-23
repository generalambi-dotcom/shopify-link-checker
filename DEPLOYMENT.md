# Deployment Guide

This guide covers deploying the Shopify Link Checker to various cloud platforms with a custom domain/subdomain.

## Table of Contents

- [Option 1: Railway (Easiest)](#option-1-railway-easiest)
- [Option 2: Render](#option-2-render)
- [Option 3: Fly.io](#option-3-flyio)
- [Option 4: DigitalOcean with Docker](#option-4-digitalocean-with-docker)
- [Option 5: AWS/GCP/Azure with Docker](#option-5-awsgcpazure-with-docker)
- [Custom Domain Setup](#custom-domain-setup)

---

## Option 1: Railway (Easiest)

Railway is the simplest option - deploy in minutes with automatic SSL.

### Steps

1. **Create Railway Account**
   - Sign up at [railway.app](https://railway.app)

2. **Deploy from GitHub**
   ```bash
   # Install Railway CLI
   npm install -g @railway/cli

   # Login
   railway login

   # Initialize project
   railway init

   # Deploy
   railway up
   ```

3. **Or Deploy via Web UI**
   - Go to Railway Dashboard
   - Click "New Project" > "Deploy from GitHub repo"
   - Select your repository
   - Railway auto-detects the `railway.json` config

4. **Set Environment Variables**
   In Railway dashboard, add these variables:
   ```
   PORT=8000
   LOG_LEVEL=INFO
   ```

5. **Add Custom Domain**
   - Go to Settings > Domains
   - Click "Generate Domain" for a free `.railway.app` subdomain
   - Or click "Custom Domain" to add your own

### Cost
- Free tier: 500 hours/month
- Starter: $5/month with more resources

---

## Option 2: Render

Render offers easy deployment with free SSL and custom domains.

### Steps

1. **Create Render Account**
   - Sign up at [render.com](https://render.com)

2. **Deploy via Blueprint**
   - Click "New" > "Blueprint"
   - Connect your GitHub repo
   - Render detects `render.yaml` automatically

3. **Or Manual Deploy**
   - Click "New" > "Web Service"
   - Connect GitHub repo
   - Select "Docker" as environment
   - Set Dockerfile path: `Dockerfile.prod`

4. **Set Environment Variables**
   ```
   PORT=8000
   LOG_LEVEL=INFO
   ```

5. **Add Custom Domain**
   - Go to Settings > Custom Domains
   - Add your domain/subdomain
   - Follow DNS instructions

### Cost
- Free tier: 750 hours/month (spins down after inactivity)
- Starter: $7/month (always on)

---

## Option 3: Fly.io

Fly.io offers global edge deployment with great performance.

### Steps

1. **Install Fly CLI**
   ```bash
   # macOS
   brew install flyctl

   # Linux
   curl -L https://fly.io/install.sh | sh

   # Windows
   powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
   ```

2. **Login & Deploy**
   ```bash
   # Login
   fly auth login

   # Launch (first time)
   fly launch

   # Deploy updates
   fly deploy
   ```

3. **Set Secrets**
   ```bash
   fly secrets set LOG_LEVEL=INFO
   ```

4. **Add Custom Domain**
   ```bash
   # Add certificate
   fly certs add linkchecker.yourdomain.com

   # Get DNS instructions
   fly certs show linkchecker.yourdomain.com
   ```

### Cost
- Free tier: 3 shared VMs
- Pay-as-you-go after that (~$2-5/month for small apps)

---

## Option 4: DigitalOcean with Docker

For more control, deploy to a VPS with automatic SSL via Traefik.

### Steps

1. **Create Droplet**
   - Go to [DigitalOcean](https://digitalocean.com)
   - Create Droplet > Marketplace > Docker
   - Choose size: Basic $6/month (1GB RAM) is sufficient

2. **SSH into Server**
   ```bash
   ssh root@your-droplet-ip
   ```

3. **Clone Repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/shopify-link-checker.git
   cd shopify-link-checker
   ```

4. **Configure Environment**
   ```bash
   cp .env.example .env
   nano .env
   ```

   Add these to `.env`:
   ```
   DOMAIN=linkchecker.yourdomain.com
   ACME_EMAIL=your@email.com
   LOG_LEVEL=INFO
   ```

5. **Point DNS to Server**
   - Add A record: `linkchecker.yourdomain.com` -> `your-droplet-ip`
   - Wait for DNS propagation (5-30 minutes)

6. **Deploy with Docker Compose**
   ```bash
   docker-compose -f docker-compose.prod.yml up -d
   ```

7. **Verify**
   ```bash
   # Check containers
   docker-compose -f docker-compose.prod.yml ps

   # Check logs
   docker-compose -f docker-compose.prod.yml logs -f

   # Test health
   curl https://linkchecker.yourdomain.com/health
   ```

### Cost
- $6/month for basic Droplet
- Free SSL via Let's Encrypt

---

## Option 5: AWS/GCP/Azure with Docker

### AWS (Elastic Beanstalk)

1. **Install EB CLI**
   ```bash
   pip install awsebcli
   ```

2. **Initialize & Deploy**
   ```bash
   eb init -p docker shopify-link-checker
   eb create production
   eb deploy
   ```

3. **Add Custom Domain**
   - Use Route 53 or your DNS provider
   - Point to the Elastic Beanstalk URL

### GCP (Cloud Run)

1. **Deploy**
   ```bash
   gcloud run deploy shopify-link-checker \
     --source . \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated
   ```

2. **Map Custom Domain**
   ```bash
   gcloud run domain-mappings create \
     --service shopify-link-checker \
     --domain linkchecker.yourdomain.com
   ```

### Azure (Container Apps)

1. **Deploy**
   ```bash
   az containerapp up \
     --name shopify-link-checker \
     --source . \
     --ingress external
   ```

---

## Custom Domain Setup

Regardless of platform, you'll need to configure DNS.

### For Subdomain (e.g., linkchecker.yourdomain.com)

1. **Get Target Address**
   - Railway/Render/Fly: Copy the provided URL or IP
   - Self-hosted: Your server IP

2. **Add DNS Record**

   | Type | Name | Value | TTL |
   |------|------|-------|-----|
   | CNAME | linkchecker | your-app.railway.app | 3600 |

   Or for IP address:

   | Type | Name | Value | TTL |
   |------|------|-------|-----|
   | A | linkchecker | 123.45.67.89 | 3600 |

3. **Wait for Propagation**
   - Usually 5-30 minutes
   - Check: `dig linkchecker.yourdomain.com`

### For Root Domain (e.g., yourdomain.com)

Most platforms require an A record for root domains:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | 123.45.67.89 | 3600 |

---

## SSL/HTTPS

All recommended platforms provide free SSL:

- **Railway**: Automatic via Let's Encrypt
- **Render**: Automatic via Let's Encrypt
- **Fly.io**: Automatic via Let's Encrypt
- **DigitalOcean + Traefik**: Automatic via Let's Encrypt (configured in docker-compose.prod.yml)

---

## Environment Variables

Set these on your hosting platform:

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | Yes | Server port (usually 8000 or auto-assigned) |
| `LOG_LEVEL` | No | Logging level (INFO, DEBUG, WARNING) |
| `DOMAIN` | Only for self-hosted | Your custom domain |
| `ACME_EMAIL` | Only for self-hosted | Email for SSL certificates |

**Note**: Shopify credentials are entered in the web UI, not as environment variables (for security).

---

## Quick Start Commands

### Railway
```bash
npm i -g @railway/cli && railway login && railway up
```

### Render
```bash
# Just connect GitHub repo in web UI - uses render.yaml
```

### Fly.io
```bash
curl -L https://fly.io/install.sh | sh && fly auth login && fly launch
```

### DigitalOcean
```bash
# On your droplet:
git clone <repo> && cd shopify-link-checker
cp .env.example .env && nano .env
docker-compose -f docker-compose.prod.yml up -d
```

---

## Troubleshooting

### App Not Starting
```bash
# Check logs
docker-compose -f docker-compose.prod.yml logs shopify-link-checker
```

### SSL Certificate Issues
```bash
# For Traefik, check acme.json permissions
docker-compose -f docker-compose.prod.yml exec traefik cat /letsencrypt/acme.json
```

### DNS Not Resolving
```bash
# Check DNS propagation
dig linkchecker.yourdomain.com
nslookup linkchecker.yourdomain.com
```

### SSE/Streaming Not Working
Ensure your reverse proxy doesn't buffer responses. The included configs handle this, but if using a different proxy, add:
```
proxy_buffering off;
proxy_cache off;
```
