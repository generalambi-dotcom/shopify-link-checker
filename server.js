// Shopify Link Checker Backend API
// Install dependencies: npm install express node-fetch cheerio cors dotenv

const express = require('express');
const fetch = require('node-fetch');
const cheerio = require('cheerio');
const cors = require('cors');
require('dotenv').config();

const app = express();

// Enhanced CORS configuration for Server-Sent Events
app.use(cors({
  origin: '*', // Allow all origins for development
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization', 'Cache-Control'],
  credentials: false
}));

app.use(express.json());

// Configuration from environment variables or request body
let CONFIG = {
  SHOPIFY_STORE: process.env.SHOPIFY_STORE || '',
  ACCESS_TOKEN: process.env.SHOPIFY_ACCESS_TOKEN || '',
  CUSTOM_FIELD_NAMESPACE: process.env.CUSTOM_FIELD_NAMESPACE || 'custom',
  CUSTOM_FIELD_KEY: process.env.CUSTOM_FIELD_KEY || 'affiliate_link',
  BATCH_SIZE: parseInt(process.env.BATCH_SIZE) || 250,
  BATCH_DELAY: parseInt(process.env.BATCH_DELAY) || 1000,
  STOCK_KEYWORDS: {
    outOfStock: ['out of stock', 'sold out', 'not available', 'unavailable', 'no longer available', 'currently unavailable'],
    inStock: ['in stock', 'add to cart', 'buy now', 'add to bag', 'available', 'add to basket'],
    lowStock: ['only.*left', 'limited stock', 'few left', 'almost gone', '\\d+\\s*left']
  },
  MIN_STOCK_THRESHOLD: parseInt(process.env.MIN_STOCK_THRESHOLD) || 2,
  AUTO_DRAFT: process.env.AUTO_DRAFT === 'true',
  AUTO_ARCHIVE: process.env.AUTO_ARCHIVE === 'true',
  MAX_PRODUCTS_PER_SCAN: parseInt(process.env.MAX_PRODUCTS_PER_SCAN) || 0 // 0 = unlimited
};

// Health check endpoint
app.get('/', (req, res) => {
  res.json({ 
    status: 'OK', 
    service: 'Shopify Link Checker API',
    version: '1.0.0',
    endpoints: {
      '/api/config': 'POST - Update configuration',
      '/api/scan': 'GET - Start product scan (SSE)',
      '/api/scan-simple': 'POST - Start product scan (simple)',
      '/api/scan-collection': 'POST - Scan specific collection',
      '/api/test-link': 'POST - Test single link',
      '/api/products': 'GET - List products with affiliate links'
    }
  });
});

// Update configuration endpoint
app.post('/api/config', (req, res) => {
  const { 
    storeName, 
    accessToken, 
    customFieldNamespace, 
    customFieldKey,
    stockKeywords,
    minStockThreshold,
    autoDraft,
    autoArchive,
    batchSize,
    delayBetweenBatches
  } = req.body;

  if (storeName) CONFIG.SHOPIFY_STORE = storeName;
  if (accessToken) CONFIG.ACCESS_TOKEN = accessToken;
  if (customFieldNamespace) CONFIG.CUSTOM_FIELD_NAMESPACE = customFieldNamespace;
  if (customFieldKey) CONFIG.CUSTOM_FIELD_KEY = customFieldKey;
  if (stockKeywords) CONFIG.STOCK_KEYWORDS = stockKeywords;
  if (minStockThreshold) CONFIG.MIN_STOCK_THRESHOLD = minStockThreshold;
  if (autoDraft !== undefined) CONFIG.AUTO_DRAFT = autoDraft;
  if (autoArchive !== undefined) CONFIG.AUTO_ARCHIVE = autoArchive;
  if (batchSize) CONFIG.BATCH_SIZE = batchSize;
  if (delayBetweenBatches) CONFIG.BATCH_DELAY = delayBetweenBatches;

  res.json({ success: true, message: 'Configuration updated', config: CONFIG });
});

// Fetch products from Shopify with pagination
async function fetchProducts(collectionId = null) {
  const products = [];
  let hasNextPage = true;
  let pageInfo = null;
  let apiCalls = 0;

  try {
    while (hasNextPage) {
      const baseUrl = collectionId
        ? `https://${CONFIG.SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/collections/${collectionId}/products.json`
        : `https://${CONFIG.SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/products.json`;
      
      const url = `${baseUrl}?limit=250${pageInfo ? `&page_info=${pageInfo}` : ''}`;
      
      const response = await fetch(url, {
        headers: {
          'X-Shopify-Access-Token': CONFIG.ACCESS_TOKEN,
          'Content-Type': 'application/json'
        }
      });

      apiCalls++;

      if (!response.ok) {
        throw new Error(`Shopify API error: ${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      
      // Get metafields for each product
      for (const product of data.products) {
        const metafieldUrl = `https://${CONFIG.SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/products/${product.id}/metafields.json`;
        const metaResponse = await fetch(metafieldUrl, {
          headers: { 
            'X-Shopify-Access-Token': CONFIG.ACCESS_TOKEN,
            'Content-Type': 'application/json'
          }
        });

        apiCalls++;

        if (metaResponse.ok) {
          const metaData = await metaResponse.json();
          
          const affiliateField = metaData.metafields?.find(
            m => m.namespace === CONFIG.CUSTOM_FIELD_NAMESPACE && 
                 m.key === CONFIG.CUSTOM_FIELD_KEY
          );

          if (affiliateField?.value) {
            products.push({
              id: product.id,
              title: product.title,
              handle: product.handle,
              affiliateLink: affiliateField.value,
              status: product.status,
              variants: product.variants,
              createdAt: product.created_at,
              updatedAt: product.updated_at
            });
          }
        }

        // Respect rate limits - pause after batch
        if (apiCalls >= CONFIG.BATCH_SIZE) {
          await new Promise(resolve => setTimeout(resolve, CONFIG.BATCH_DELAY));
          apiCalls = 0;
        }
      }
      
      // Check for pagination
      const linkHeader = response.headers.get('Link');
      hasNextPage = linkHeader && linkHeader.includes('rel="next"');
      if (hasNextPage) {
        const match = linkHeader.match(/page_info=([^>]+)>; rel="next"/);
        pageInfo = match ? match[1] : null;
      } else {
        hasNextPage = false;
      }
    }

    return products;
  } catch (error) {
    console.error('Error fetching products:', error);
    throw error;
  }
}

// Check link status and scrape for stock availability
async function checkLink(url) {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000); // 15 second timeout

    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5'
      },
      redirect: 'follow'
    });

    clearTimeout(timeout);

    // Check for broken links
    if (!response.ok) {
      return {
        status: 'broken',
        statusCode: response.status,
        issue: `Broken Link (${response.status})`,
        severity: 'high',
        stockAvailable: false
      };
    }

    const finalUrl = response.url;
    const html = await response.text();
    const $ = cheerio.load(html);
    
    // Get all text content
    const pageText = $('body').text().toLowerCase();
    const htmlText = html.toLowerCase();

    // Check for redirects
    if (finalUrl !== url) {
      return {
        status: 'redirect',
        statusCode: 301,
        redirectTo: finalUrl,
        issue: 'Redirect Detected',
        severity: 'medium',
        originalUrl: url
      };
    }

    // Check out of stock - highest priority
    for (const keyword of CONFIG.STOCK_KEYWORDS.outOfStock) {
      if (pageText.includes(keyword.toLowerCase())) {
        return {
          status: 'out_of_stock',
          statusCode: 200,
          issue: 'Out of Stock',
          severity: 'high',
          stockAvailable: false,
          detectedKeyword: keyword
        };
      }
    }

    // Check low stock with regex patterns
    for (const pattern of CONFIG.STOCK_KEYWORDS.lowStock) {
      const regex = new RegExp(pattern, 'i');
      const match = html.match(regex);
      
      if (match) {
        // Try to extract stock number
        const numberMatch = html.match(/(\d+)\s*(item|unit|piece|stock)?s?\s*(left|remaining|available|in stock)/i);
        let stockCount = numberMatch ? parseInt(numberMatch[1]) : 1;

        if (stockCount < CONFIG.MIN_STOCK_THRESHOLD) {
          return {
            status: 'low_stock',
            statusCode: 200,
            issue: `Low Stock (${stockCount} ${stockCount === 1 ? 'unit' : 'units'})`,
            severity: 'medium',
            stockCount: stockCount,
            stockAvailable: true
          };
        }
      }
    }

    // Check in stock indicators
    for (const keyword of CONFIG.STOCK_KEYWORDS.inStock) {
      if (pageText.includes(keyword.toLowerCase())) {
        return {
          status: 'ok',
          statusCode: 200,
          issue: null,
          severity: null,
          stockAvailable: true,
          detectedKeyword: keyword
        };
      }
    }

    // Could not determine stock status
    return {
      status: 'unknown',
      statusCode: 200,
      issue: 'Cannot Determine Stock Status',
      severity: 'low',
      stockAvailable: null
    };

  } catch (error) {
    if (error.name === 'AbortError') {
      return {
        status: 'timeout',
        statusCode: null,
        issue: 'Request Timeout',
        severity: 'high',
        error: 'Request took too long (>15s)'
      };
    }

    return {
      status: 'error',
      statusCode: null,
      issue: 'Connection Error',
      severity: 'high',
      error: error.message
    };
  }
}

// Update Shopify product status
async function updateProductStatus(productId, status) {
  try {
    const url = `https://${CONFIG.SHOPIFY_STORE}.myshopify.com/admin/api/2024-01/products/${productId}.json`;
    
    const response = await fetch(url, {
      method: 'PUT',
      headers: {
        'X-Shopify-Access-Token': CONFIG.ACCESS_TOKEN,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        product: {
          id: productId,
          status: status
        }
      })
    });

    if (!response.ok) {
      throw new Error(`Failed to update product: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`Error updating product ${productId}:`, error);
    throw error;
  }
}

// Determine action based on link status
function determineAction(linkStatus) {
  if (linkStatus.status === 'broken' || linkStatus.status === 'error' || linkStatus.status === 'timeout') {
    return CONFIG.AUTO_ARCHIVE ? 'Archive' : 'Flag for Archive';
  }
  if (linkStatus.status === 'redirect') {
    return 'Flag for Review';
  }
  if (linkStatus.status === 'out_of_stock') {
    return CONFIG.AUTO_ARCHIVE ? 'Archive' : 'Flag for Archive';
  }
  if (linkStatus.status === 'low_stock') {
    return CONFIG.AUTO_DRAFT ? 'Draft' : 'Flag for Draft';
  }
  return 'Keep Active';
}

// API Endpoint: Get products with affiliate links
app.get('/api/products', async (req, res) => {
  try {
    const products = await fetchProducts();
    res.json({ 
      success: true, 
      count: products.length,
      products: products 
    });
  } catch (error) {
    res.status(500).json({ 
      success: false, 
      error: error.message 
    });
  }
});

// API Endpoint: Test single link
app.post('/api/test-link', async (req, res) => {
  try {
    const { url } = req.body;
    
    if (!url) {
      return res.status(400).json({ error: 'URL is required' });
    }

    const result = await checkLink(url);
    res.json({ success: true, result });
  } catch (error) {
    res.status(500).json({ 
      success: false, 
      error: error.message 
    });
  }
});

// API Endpoint: Start full scan with streaming progress
app.get('/api/scan', async (req, res) => {
  try {
    // Set headers for Server-Sent Events with proper CORS
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Credentials', 'false');
    res.setHeader('X-Accel-Buffering', 'no'); // Disable buffering on Nginx

    const startTime = Date.now();
    
    // Send initial message
    res.write(`data: ${JSON.stringify({ type: 'start', message: 'Starting scan...' })}\n\n`);

    // Fetch all products
    res.write(`data: ${JSON.stringify({ type: 'info', message: 'Fetching products from Shopify...' })}\n\n`);
    const products = await fetchProducts();
    
    res.write(`data: ${JSON.stringify({ 
      type: 'info', 
      message: `Found ${products.length} products with affiliate links` 
    })}\n\n`);

    const results = [];
    const issues = [];
    let processed = 0;

    // Process each product
    for (let i = 0; i < products.length; i++) {
      const product = products[i];
      
      // Send progress update
      res.write(`data: ${JSON.stringify({ 
        type: 'progress', 
        progress: ((i + 1) / products.length) * 100,
        current: i + 1,
        total: products.length,
        currentProduct: product.title
      })}\n\n`);

      // Check the link
      const linkStatus = await checkLink(product.affiliateLink);
      const action = determineAction(linkStatus);

      const result = {
        ...product,
        ...linkStatus,
        action,
        lastChecked: new Date().toISOString()
      };

      results.push(result);

      // Track issues
      if (linkStatus.issue) {
        issues.push({
          id: product.id,
          product: product.title,
          issue: linkStatus.issue,
          severity: linkStatus.severity,
          url: product.affiliateLink,
          action,
          statusCode: linkStatus.statusCode
        });

        // Send issue notification
        res.write(`data: ${JSON.stringify({ 
          type: 'issue', 
          issue: {
            product: product.title,
            issue: linkStatus.issue
          }
        })}\n\n`);
      }

      // Perform auto-actions
      if (CONFIG.AUTO_DRAFT && linkStatus.status === 'low_stock') {
        await updateProductStatus(product.id, 'draft');
        res.write(`data: ${JSON.stringify({ 
          type: 'action', 
          message: `Auto-drafted: ${product.title}` 
        })}\n\n`);
      }

      if (CONFIG.AUTO_ARCHIVE && (linkStatus.status === 'broken' || linkStatus.status === 'out_of_stock' || linkStatus.status === 'timeout')) {
        await updateProductStatus(product.id, 'archived');
        res.write(`data: ${JSON.stringify({ 
          type: 'action', 
          message: `Auto-archived: ${product.title}` 
        })}\n\n`);
      }

      processed++;

      // Batch delay
      if (processed % CONFIG.BATCH_SIZE === 0 && i < products.length - 1) {
        res.write(`data: ${JSON.stringify({ 
          type: 'info', 
          message: `Batch complete. Waiting ${CONFIG.BATCH_DELAY}ms before next batch...` 
        })}\n\n`);
        await new Promise(resolve => setTimeout(resolve, CONFIG.BATCH_DELAY));
      }
    }

    const endTime = Date.now();
    const duration = Math.round((endTime - startTime) / 1000);

    // Send completion message
    res.write(`data: ${JSON.stringify({ 
      type: 'complete',
      summary: {
        totalProducts: products.length,
        issuesFound: issues.length,
        duration: `${Math.floor(duration / 60)}m ${duration % 60}s`,
        storeHealth: Math.round(((products.length - issues.length) / products.length) * 100)
      },
      results,
      issues
    })}\n\n`);

    res.end();

  } catch (error) {
    res.write(`data: ${JSON.stringify({ 
      type: 'error', 
      error: error.message 
    })}\n\n`);
    res.end();
  }
});

// Alternative simple POST endpoint without streaming (for CORS compatibility)
app.post('/api/scan-simple', async (req, res) => {
  try {
    const startTime = Date.now();
    
    let products = await fetchProducts();
    
    // Limit products if configured
    const maxProducts = req.body.maxProducts || CONFIG.MAX_PRODUCTS_PER_SCAN;
    if (maxProducts && maxProducts > 0) {
      products = products.slice(0, maxProducts);
      console.log(`Limited scan to ${maxProducts} products`);
    }
    
    const results = [];
    const issues = [];
    const scanLog = []; // Collect logs to send back

    scanLog.push({ time: new Date().toISOString(), message: `🚀 Starting scan of ${products.length} products`, type: 'info' });

    for (let i = 0; i < products.length; i++) {
      const product = products[i];
      
      // Log each product being checked
      scanLog.push({ 
        time: new Date().toISOString(), 
        message: `[${i + 1}/${products.length}] 🔍 Checking: ${product.title}`, 
        type: 'progress' 
      });
      
      const linkStatus = await checkLink(product.affiliateLink);
      
      const result = {
        ...product,
        ...linkStatus,
        action: determineAction(linkStatus),
        lastChecked: new Date().toISOString()
      };

      results.push(result);

      if (linkStatus.issue) {
        scanLog.push({ 
          time: new Date().toISOString(), 
          message: `⚠️ Issue found: ${product.title} - ${linkStatus.issue}`, 
          type: 'warning' 
        });
        
        issues.push({
          id: product.id,
          product: product.title,
          collection: 'Unknown',
          issue: linkStatus.issue,
          severity: linkStatus.severity,
          url: product.affiliateLink,
          action: result.action,
          lastChecked: 'Just now'
        });
      } else {
        scanLog.push({ 
          time: new Date().toISOString(), 
          message: `✅ OK: ${product.title}`, 
          type: 'success' 
        });
      }

      // Auto-actions
      if (CONFIG.AUTO_DRAFT && linkStatus.status === 'low_stock') {
        await updateProductStatus(product.id, 'draft');
        scanLog.push({ 
          time: new Date().toISOString(), 
          message: `📝 Drafted: ${product.title}`, 
          type: 'action' 
        });
      }
      if (CONFIG.AUTO_ARCHIVE && (linkStatus.status === 'broken' || linkStatus.status === 'out_of_stock' || linkStatus.status === 'timeout')) {
        await updateProductStatus(product.id, 'archived');
        scanLog.push({ 
          time: new Date().toISOString(), 
          message: `📦 Archived: ${product.title}`, 
          type: 'action' 
        });
      }

      // Batch delay
      if ((i + 1) % CONFIG.BATCH_SIZE === 0 && i + 1 < products.length) {
        scanLog.push({ 
          time: new Date().toISOString(), 
          message: `⏸️ Batch ${Math.ceil((i + 1) / CONFIG.BATCH_SIZE)} complete. Waiting ${CONFIG.BATCH_DELAY}ms before next batch...`, 
          type: 'info' 
        });
        await new Promise(resolve => setTimeout(resolve, CONFIG.BATCH_DELAY));
      }
    }

    const endTime = Date.now();
    const duration = Math.round((endTime - startTime) / 1000);

    scanLog.push({ 
      time: new Date().toISOString(), 
      message: `✅ Scan complete! ${products.length} products checked, ${issues.length} issues found in ${Math.floor(duration / 60)}m ${duration % 60}s`, 
      type: 'success' 
    });

    res.json({
      success: true,
      summary: {
        totalProducts: products.length,
        issuesFound: issues.length,
        duration: `${Math.floor(duration / 60)}m ${duration % 60}s`,
        storeHealth: products.length > 0 ? Math.round(((products.length - issues.length) / products.length) * 100) : 0
      },
      results,
      issues,
      scanLog // Send logs back to frontend
    });

  } catch (error) {
    console.error('Scan error:', error);
    res.status(500).json({ 
      success: false,
      error: error.message 
    });
  }
});

// API Endpoint: Scan specific collection
app.post('/api/scan-collection', async (req, res) => {
  try {
    const { collectionId } = req.body;
    
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('X-Accel-Buffering', 'no');

    res.write(`data: ${JSON.stringify({ type: 'start', message: 'Starting collection scan...' })}\n\n`);

    const products = await fetchProducts(collectionId);
    
    res.write(`data: ${JSON.stringify({ 
      type: 'info', 
      message: `Found ${products.length} products in collection` 
    })}\n\n`);

    res.end();

  } catch (error) {
    res.write(`data: ${JSON.stringify({ 
      type: 'error', 
      error: error.message 
    })}\n\n`);
    res.end();
  }
});

const PORT = process.env.PORT || 3001;
const server = app.listen(PORT, () => {
  console.log(`🚀 Shopify Link Checker API running on http://localhost:${PORT}`);
  console.log(`📊 Dashboard: Configure at /api/config endpoint`);
  console.log(`🔍 Ready to scan products!`);
});

// Increase timeout for large scans (30 minutes)
server.timeout = 1800000; // 30 minutes in milliseconds
server.keepAliveTimeout = 1800000;
server.headersTimeout = 1900000;
