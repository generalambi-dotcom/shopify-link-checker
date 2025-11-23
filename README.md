# Shopify Affiliate Link Checker

A lightweight Express-based dashboard that scans Shopify products for affiliate links stored in a metafield, reports broken URLs, and (in live mode) lets you archive or draft problematic products.

## Architecture
- **Backend:** Node.js + Express, Shopify Admin GraphQL API, Bottleneck (API throttling), `node-fetch` (HTTP), `p-limit` (link check concurrency), `csv-stringify` (export).
- **Frontend:** Static HTML/JS served from `/public`, polls scan jobs, renders results, and triggers remediation actions.
- **Job model:** `/api/scan` creates an in-memory job; `/api/scan/:id` is polled for progress; CSV export at `/api/scan/:id/export`.

## Configuration
Copy `.env.example` and fill in your Shopify credentials and preferences.

```env
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ADMIN_API_TOKEN=shpat_xxx
AFFILIATE_METAFIELD_NAMESPACE=affiliate
AFFILIATE_METAFIELD_KEY=link
SHOPIFY_API_VERSION=2024-07
HTTP_TIMEOUT_MS=15000
MAX_REDIRECTS=5
HTTP_CONCURRENCY=5
SHOPIFY_RATE_LIMIT_MS=500
PORT=3000
```

## Running locally
1. Install dependencies:
   ```bash
   npm install
   ```
2. Start the server:
   ```bash
   npm start
   ```
3. Open `http://localhost:3000` in your browser. Choose Dry Run or Live mode, optionally filter scope/date, and click **Start Scan**.

## Usage notes
- Dry Run: no product updates are sent to Shopify.
- Live Mode: archive/draft buttons call Shopify `productUpdate` mutations.
- Filtering: use the UI filter to view broken/redirect/OK/no-link rows.
- CSV Export: enabled once a scan job finishes.
- Pagination & limits: products fetched in 250-item pages via GraphQL cursor pagination; Shopify API calls are throttled with Bottleneck; link checks run with configurable concurrency, timeout, and redirect limits.

## Project structure
- `server.js` – Express server, Shopify GraphQL helpers, scan job orchestration, CSV export, product status updates.
- `public/index.html` – UI for launching scans, monitoring progress, filtering results, and applying product actions.
- `package.json` – dependencies and scripts.

## Deployment
Deploy as a private/embedded app backend (e.g., Render/Heroku/Fly). Set environment variables as above. If embedding in Shopify Admin, host behind Shopify app proxy or use App Bridge to mount the UI; the backend endpoints remain the same.
