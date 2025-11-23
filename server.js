require('dotenv').config();
const express = require('express');
const cors = require('cors');
const fetch = require('node-fetch');
const Bottleneck = require('bottleneck');
const pLimit = require('p-limit');
const { stringify } = require('csv-stringify');
const path = require('path');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ---- Configuration ----
const CONFIG = {
  SHOPIFY_STORE_DOMAIN: process.env.SHOPIFY_STORE_DOMAIN || '',
  SHOPIFY_ADMIN_API_TOKEN: process.env.SHOPIFY_ADMIN_API_TOKEN || '',
  AFFILIATE_METAFIELD_NAMESPACE: process.env.AFFILIATE_METAFIELD_NAMESPACE || 'affiliate',
  AFFILIATE_METAFIELD_KEY: process.env.AFFILIATE_METAFIELD_KEY || 'link',
  SHOPIFY_API_VERSION: process.env.SHOPIFY_API_VERSION || '2024-07',
  HTTP_TIMEOUT_MS: parseInt(process.env.HTTP_TIMEOUT_MS || '15000', 10),
  MAX_REDIRECTS: parseInt(process.env.MAX_REDIRECTS || '5', 10),
  HTTP_CONCURRENCY: parseInt(process.env.HTTP_CONCURRENCY || '5', 10),
  SHOPIFY_RATE_LIMIT_MS: parseInt(process.env.SHOPIFY_RATE_LIMIT_MS || '500', 10)
};

const limiter = new Bottleneck({ minTime: CONFIG.SHOPIFY_RATE_LIMIT_MS });
const jobs = new Map();

// ---- Helpers ----
function requireEnv() {
  if (!CONFIG.SHOPIFY_STORE_DOMAIN || !CONFIG.SHOPIFY_ADMIN_API_TOKEN) {
    throw new Error('Missing SHOPIFY_STORE_DOMAIN or SHOPIFY_ADMIN_API_TOKEN in environment.');
  }
}

async function shopifyGraphQL(query, variables = {}) {
  requireEnv();
  const url = `https://${CONFIG.SHOPIFY_STORE_DOMAIN}/admin/api/${CONFIG.SHOPIFY_API_VERSION}/graphql.json`;
  const response = await limiter.schedule(() =>
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Shopify-Access-Token': CONFIG.SHOPIFY_ADMIN_API_TOKEN
      },
      body: JSON.stringify({ query, variables })
    })
  );

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Shopify API error: ${response.status} ${response.statusText} - ${text}`);
  }

  const payload = await response.json();
  if (payload.errors) {
    throw new Error(`Shopify GraphQL errors: ${JSON.stringify(payload.errors)}`);
  }
  return payload.data;
}

function toGid(id) {
  return id.startsWith('gid://') ? id : `gid://shopify/Product/${id}`;
}

function parseStatus(status) {
  if (status >= 200 && status < 300) return 'OK (2xx)';
  if (status >= 300 && status < 400) return 'Redirected';
  if (status === 404) return 'Broken – Not Found (404)';
  if (status >= 500 && status < 600) return 'Broken – Server Error (5xx)';
  if (status >= 400 && status < 500) return 'Broken – Client Error (4xx)';
  return 'Broken – Other';
}

async function followRedirects(url, depth = 0) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONFIG.HTTP_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      redirect: 'manual',
      signal: controller.signal
    });
    const statusCode = res.status;
    const location = res.headers.get('location');
    const redirects = [];

    if (statusCode >= 300 && statusCode < 400 && location && depth < CONFIG.MAX_REDIRECTS) {
      redirects.push(location);
      const next = new URL(location, url).toString();
      const nested = await followRedirects(next, depth + 1);
      return {
        statusCode: nested.statusCode,
        finalUrl: nested.finalUrl,
        redirects: [url, ...nested.redirects]
      };
    }

    return { statusCode, finalUrl: url, redirects: depth > 0 ? [url] : [] };
  } catch (error) {
    return { error: error.message };
  } finally {
    clearTimeout(timer);
  }
}

async function checkAffiliateLink(url) {
  const result = await followRedirects(url);
  if (result.error) {
    return {
      status: 'Broken – Other',
      statusCode: null,
      finalUrl: null,
      redirects: [],
      error: result.error
    };
  }
  return {
    status: parseStatus(result.statusCode),
    statusCode: result.statusCode,
    finalUrl: result.finalUrl,
    redirects: result.redirects,
    error: null
  };
}

function buildQuery(scope = 'all', updatedAfter) {
  const clauses = [];
  if (scope === 'active') clauses.push('status:active');
  if (scope === 'draft') clauses.push('status:draft');
  if (scope === 'archived') clauses.push('status:archived');
  if (updatedAfter) clauses.push(`updated_at:>${updatedAfter}`);
  return clauses.join(' ');
}

async function fetchAllProducts({ scope, updatedAfter }) {
  const products = [];
  let hasNext = true;
  let cursor = null;
  const queryFilter = buildQuery(scope, updatedAfter);

  const gql = `
    query FetchProducts($cursor: String, $query: String) {
      products(first: 250, after: $cursor, query: $query) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          title
          status
          handle
          updatedAt
          createdAt
          onlineStoreUrl
          images(first: 1) { nodes { originalSrc altText } }
          metafield(namespace: "${CONFIG.AFFILIATE_METAFIELD_NAMESPACE}", key: "${CONFIG.AFFILIATE_METAFIELD_KEY}") {
            id
            value
          }
        }
      }
    }
  `;

  while (hasNext) {
    const data = await shopifyGraphQL(gql, { cursor, query: queryFilter || null });
    const connection = data.products;
    connection.nodes.forEach((node) => {
      products.push({
        id: node.id,
        numericId: node.id.split('/').pop(),
        title: node.title,
        status: node.status,
        handle: node.handle,
        updatedAt: node.updatedAt,
        createdAt: node.createdAt,
        onlineStoreUrl: node.onlineStoreUrl,
        image: node.images?.nodes?.[0]?.originalSrc || null,
        affiliateLink: node.metafield?.value || null
      });
    });
    hasNext = connection.pageInfo.hasNextPage;
    cursor = connection.pageInfo.endCursor;
  }

  return products;
}

async function runScan(jobId, options) {
  const { dryRun = true, scope = 'all', updatedAfter = null } = options;
  const job = jobs.get(jobId);
  try {
    const products = await fetchAllProducts({ scope, updatedAfter });
    job.total = products.length;
    job.status = 'running';

    const limit = pLimit(CONFIG.HTTP_CONCURRENCY);
    const checks = products.map((product, index) =>
      limit(async () => {
        let linkResult = null;
        if (!product.affiliateLink) {
          linkResult = {
            status: 'No link found',
            statusCode: null,
            finalUrl: null,
            redirects: [],
            error: 'Missing affiliate metafield'
          };
        } else {
          linkResult = await checkAffiliateLink(product.affiliateLink);
        }

        const row = {
          productId: product.numericId,
          productGid: product.id,
          title: product.title,
          status: product.status,
          affiliateUrl: product.affiliateLink,
          finalUrl: linkResult.finalUrl,
          httpStatus: linkResult.statusCode,
          classification: linkResult.status,
          redirects: linkResult.redirects,
          error: linkResult.error,
          checkedAt: new Date().toISOString(),
          onlineStoreUrl: product.onlineStoreUrl,
          image: product.image
        };

        job.results.push(row);
        job.processed += 1;
        job.progress = `${job.processed}/${job.total}`;
        return row;
      })
    );

    await Promise.all(checks);
    job.status = 'completed';
    job.dryRun = dryRun;
  } catch (error) {
    job.status = 'error';
    job.error = error.message;
    console.error('Scan failed', error);
  }
}

async function updateProductStatus(productGid, status) {
  const mutation = `
    mutation UpdateProductStatus($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id status }
        userErrors { field message }
      }
    }
  `;
  const data = await shopifyGraphQL(mutation, {
    input: {
      id: productGid,
      status: status.toUpperCase()
    }
  });
  const errors = data.productUpdate.userErrors;
  if (errors && errors.length) {
    throw new Error(errors.map((e) => e.message).join('; '));
  }
  return data.productUpdate.product;
}

// ---- Routes ----
app.get('/api/config', (_, res) => {
  res.json({
    ...CONFIG,
    SHOPIFY_ADMIN_API_TOKEN: undefined
  });
});

app.post('/api/scan', (req, res) => {
  const { dryRun = true, scope = 'all', updatedAfter = null } = req.body || {};
  const jobId = `job_${Date.now()}`;
  jobs.set(jobId, {
    id: jobId,
    status: 'queued',
    progress: '0/0',
    processed: 0,
    total: 0,
    dryRun,
    scope,
    updatedAfter,
    results: []
  });

  runScan(jobId, { dryRun, scope, updatedAfter });
  res.json({ jobId });
});

app.get('/api/scan/:jobId', (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ error: 'Job not found' });
  res.json(job);
});

app.get('/api/scan/:jobId/export', (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job || job.status !== 'completed') {
    return res.status(400).json({ error: 'Job not ready for export' });
  }

  res.setHeader('Content-Type', 'text/csv');
  res.setHeader('Content-Disposition', 'attachment; filename="affiliate-scan.csv"');

  const stringifier = stringify({
    header: true,
    columns: [
      'productId',
      'title',
      'status',
      'affiliateUrl',
      'finalUrl',
      'httpStatus',
      'classification',
      'error',
      'redirects',
      'checkedAt'
    ]
  });

  job.results.forEach((row) => {
    stringifier.write({
      ...row,
      redirects: row.redirects.join(' -> ')
    });
  });
  stringifier.pipe(res);
  stringifier.end();
});

app.post('/api/products/:productId/status', async (req, res) => {
  const { status, dryRun = true } = req.body || {};
  const productId = req.params.productId;
  if (!status) return res.status(400).json({ error: 'Missing status' });
  if (dryRun) return res.json({ message: 'Dry run enabled - no change applied' });

  try {
    const product = await updateProductStatus(toGid(productId), status);
    res.json({ product });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/health', (_, res) => res.json({ status: 'ok' }));

// Fallback to UI
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

const port = process.env.PORT || 3000;
app.listen(port, () => {
  console.log(`Shopify link checker listening on port ${port}`);
});
