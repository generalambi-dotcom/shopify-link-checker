# Shopify Link Checker

A production-ready tool for validating URLs in Shopify product metafields. Automatically detects broken links and sets products to draft status (unless dry-run mode is enabled).

## Features

- ✅ **Batch Processing**: Processes products in batches of up to 250 (Shopify REST API maximum)
- ✅ **Smart Pagination**: Uses cursor-based pagination with `page_info` tokens
- ✅ **No Re-fetching**: Tracks processed products to avoid duplicate checks within a run
- ✅ **Collection Filtering**: Limit scope to specific collections (smart or custom)
- ✅ **Resume Support**: Continue interrupted jobs with resume tokens
- ✅ **Dual Interface**: Clean web UI and powerful CLI
- ✅ **Rate Limiting**: Intelligent rate limit handling with exponential backoff
- ✅ **Export Options**: CSV and JSONL output formats
- ✅ **Live Progress**: Real-time progress tracking and streaming results
- ✅ **Dry Run Mode**: Test without making changes to products

## Requirements

### Shopify Admin API Scopes

Your Admin API access token must have the following scopes:

- **Products**: `read_products`, `write_products`
- **Metafields**: `read_metafields`

### System Requirements

- Python 3.11+
- Docker (optional, for containerized deployment)

## Installation

### Option 1: Standard Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/shopify-link-checker.git
cd shopify-link-checker

# Install dependencies
pip install -r requirements.txt

# Or install in development mode
pip install -e ".[dev]"
```

### Option 2: Docker

```bash
# Build the Docker image
docker build -t shopify-link-checker .

# Run with Docker Compose
docker-compose up -d
```

## Usage

### Web UI

Start the web server:

```bash
# Using Python
python -m src.cli server --port 8000

# Or using the installed command
shopify-link-checker server --port 8000

# Using Docker
docker-compose up
```

Then open your browser to `http://localhost:8000`

#### Web UI Features

1. **Configuration Form**: Enter your shop details, API token, and metafield information
2. **Live Progress**: Watch real-time progress with batch counters and statistics
3. **Results Table**: View checked products with broken link detection
4. **Export**: Download results as CSV or JSONL
5. **Resume Token**: Copy resume token to continue interrupted jobs

### CLI

The CLI provides all the same functionality in a terminal interface.

#### Basic Usage

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token shpat_xxxxxxxxxxxxx \
  --namespace custom \
  --key video_url \
  --status active
```

#### Using Metafield Handle Format

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token shpat_xxxxxxxxxxxxx \
  --metafield custom.video_url
```

#### Filter by Collections

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token $SHOPIFY_TOKEN \
  --metafield custom.video_url \
  --collection-ids 123456,789012
```

#### Dry Run (Test Mode)

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token $SHOPIFY_TOKEN \
  --metafield custom.video_url \
  --dry-run
```

#### Resume an Interrupted Job

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token $SHOPIFY_TOKEN \
  --metafield custom.video_url \
  --resume-token <base64-token-from-previous-run>
```

#### Advanced Options

```bash
shopify-link-checker check \
  --shop my-shop.myshopify.com \
  --token $SHOPIFY_TOKEN \
  --metafield custom.video_url \
  --status active \
  --batch-size 250 \
  --concurrency 20 \
  --timeout-ms 8000 \
  --follow-redirects \
  --out results.csv
```

### CLI Options Reference

| Option | Description | Default |
|--------|-------------|---------|
| `--shop` | Shop domain (e.g., `my-shop.myshopify.com`) | Required |
| `--token` | Admin API access token | Required (or via `SHOPIFY_TOKEN` env var) |
| `--namespace` | Metafield namespace | Required (or use `--metafield`) |
| `--key` | Metafield key | Required (or use `--metafield`) |
| `--metafield` | Metafield in format `namespace.key` | Alternative to namespace/key |
| `--status` | Product status filter: `active`, `draft`, `archived`, `any` | `active` |
| `--collection-ids` | Comma-separated collection IDs | None (all products) |
| `--batch-size` | Batch size (max 250) | `250` |
| `--concurrency` | Concurrent link checks | `20` |
| `--timeout-ms` | Request timeout in milliseconds | `8000` |
| `--follow-redirects` | Follow HTTP redirects | `true` |
| `--dry-run` | Test mode (don't draft products) | `false` |
| `--resume-token` | Resume token from previous run | None |
| `--api-version` | Shopify API version | `2024-10` |
| `--out` | Output CSV file path | `link_check_<timestamp>.csv` |

## How It Works

### Batching Strategy

The tool respects Shopify's REST API limit of 250 items per request:

1. **Without Collections**: Fetches products in pages of 250 using cursor pagination
   ```
   GET /admin/api/2024-10/products.json?status=active&limit=250
   ```

2. **With Collections**:
   - First, fetches product IDs via the Collects API (250 per page)
     ```
     GET /admin/api/2024-10/collects.json?collection_id=123&limit=250
     ```
   - Deduplicates product IDs across collections
   - Hydrates product details in chunks of 250
     ```
     GET /admin/api/2024-10/products.json?ids=1,2,3,...250
     ```

### Deduplication

Products are tracked in an in-memory set to ensure each product is:
- Checked only once per run
- Not re-fetched even if present in multiple collections
- Properly resumed from checkpoints without overlap

### Broken Link Detection

A URL is considered broken if:
- HTTP status code ≥ 400
- Network/DNS/SSL error occurs
- Request times out
- Too many redirects (configurable threshold)

### Drafting Logic

When a broken link is detected:

1. **Already Draft**: If product status is already `draft`, action is logged as `already_draft`
2. **Dry Run**: If `--dry-run` is enabled, action is logged as `would_draft`
3. **Draft**: Otherwise, product is set to `draft` status via:
   ```
   PUT /admin/api/2024-10/products/{id}.json
   {"product": {"id": 123, "status": "draft"}}
   ```

### Rate Limiting

The tool intelligently handles Shopify rate limits:

- Monitors the `X-Shopify-Shop-Api-Call-Limit` header
- Throttles requests when approaching bucket limit (80% threshold)
- Implements exponential backoff with jitter for 429/5xx responses
- Respects `Retry-After` headers

### Resume Mechanism

Checkpoints are created periodically (every 100 products) containing:

```json
{
  "scope_hash": "sha256 of job scope",
  "page_info": "cursor token",
  "collects_state": {
    "collection_id": {
      "page_info": "cursor",
      "product_ids": [...]
    }
  },
  "seen_ids": [product_ids],
  "processed_count": 1234
}
```

The checkpoint is base64-encoded into a resume token. When resuming:
1. Scope hash is validated (shop, status, metafield, collections must match)
2. Pagination continues from stored cursors
3. Previously seen product IDs are skipped

## Output Formats

### CSV Format

```csv
product_id,product_title,product_status,metafield,url,http_status,is_broken,error,action,checked_at
123456,Example Product,active,custom.video_url,https://example.com/video.mp4,404,true,HTTP 404,drafted,2024-01-15T10:30:00Z
```

### JSONL Format

Each line is a JSON object:

```json
{"product_id": 123456, "product_title": "Example Product", "product_status": "active", "metafield": "custom.video_url", "url": "https://example.com/video.mp4", "http_status": 404, "is_broken": true, "error": "HTTP 404", "action": "drafted", "checked_at": "2024-01-15T10:30:00Z"}
```

## Performance Tips

### Concurrency vs Rate Limits

- **Low concurrency (10-20)**: Safer for shops with tight rate limits
- **High concurrency (50+)**: Faster link checking but may hit rate limits

The tool automatically throttles when approaching rate limits, but starting with lower concurrency prevents unnecessary retries.

### Collection Filtering

When checking specific collections:

- **Small collections (<1000 products)**: Minimal overhead
- **Large collections (>10k products)**: Consider multiple smaller jobs
- **Multiple collections**: Automatic deduplication handles overlaps efficiently

### Batch Size

- Always use the maximum batch size of 250 for optimal performance
- Smaller batches only useful for testing or debugging

## Development

### Running Tests

```bash
pytest tests/
```

### Code Formatting

```bash
# Format with Black
black src/ tests/

# Lint with Ruff
ruff check src/ tests/
```

### Project Structure

```
shopify-link-checker/
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── cli.py               # CLI interface
│   ├── shopify_client.py    # Shopify API client
│   ├── link_checker.py      # URL validation
│   ├── job_manager.py       # Job orchestration
│   ├── models.py            # Data models
│   └── utils.py             # Helper functions
├── templates/
│   └── index.html           # Web UI
├── tests/
│   └── test_*.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Troubleshooting

### "Rate limited after N retries"

- Reduce `--concurrency` value
- Add delays between jobs
- Check your API rate limits in Shopify admin

### "Resume token scope mismatch"

- Ensure shop, status, metafield, and collection IDs match the original job
- Resume tokens cannot be used across different job configurations

### "Failed to fetch metafield"

- Verify the metafield namespace and key exist
- Check Admin API token has `read_metafields` scope
- Ensure products have the specified metafield

### Connection/Timeout Errors

- Increase `--timeout-ms` value
- Check network connectivity
- Verify shop domain is correct

## API Version Compatibility

Default API version: `2024-10`

To use a different version:

```bash
shopify-link-checker check \
  --api-version 2024-07 \
  ...
```

The tool should work with Shopify Admin REST API versions 2023-10 and newer. Older versions may have different pagination behavior.

## Security

- API tokens are never logged or included in resume tokens
- Tokens are kept in memory only during job execution
- Use environment variables (`SHOPIFY_TOKEN`) to avoid exposing tokens in command history
- Resume tokens contain only pagination cursors and product IDs (no sensitive data)

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Check existing issues for similar problems
- Include job configuration and error messages when reporting bugs

## Acknowledgments

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [Typer](https://typer.tiangolo.com/) - CLI framework
- [Rich](https://rich.readthedocs.io/) - Terminal formatting
- [Pydantic](https://pydantic.dev/) - Data validation