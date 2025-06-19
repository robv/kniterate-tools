# Kniterate Tools

A Flask application that provides three knitting-related tools:

- **Roller Bind-Off Calculator**: Calculates roller values for bind-off sequences using an exponential decay model with user-specified parameters. (Route: `/`)
- **Sizing Distortion Calculator**: Computes the distortion ratio for knitted images based on original and desired dimensions to ensure square stitches. (Route: `/sizing`)
- **DXF/SVG Converter**: Converts DXF or SVG design files into SVG previews and DAK TXT output, uploading both original and preview files to Cloudflare R2. (Route: `/convert`)

## Tools

1. **Roller Bind-Off Calculator**
   - **URL**: `/`
   - Interactive graph powered by Plotly.
   - No external configuration required.

2. **Sizing Distortion Calculator**
   - **URL**: `/sizing`
   - Form-based input and JSON response.
   - No external configuration required.

3. **DXF/SVG Converter**
   - **URL**: `/convert`
   - File upload form for DXF/SVG, gauge inputs (sts10/rows10), and unit selection (mm/inch).
   - Requires Cloudflare R2 configuration (see below).

## Configuration (for DXF/SVG Converter)

The DXF/SVG Converter tool uses Cloudflare R2 for file storage. The other tools do not require external environment variables.

Create a `.env` file in the project root and set the following variables:

### Required

- `CLOUDFLARE_API_TOKEN`  
  Your Cloudflare API token with permissions to read/write R2 buckets.
- `CLOUDFLARE_BUCKET`  
  The name of the Cloudflare R2 bucket where files will be stored.
- `CLOUDFLARE_ACCOUNT_ID`  
  Your Cloudflare account ID.

### Optional Overrides

- `CLOUDFLARE_R2_ENDPOINT`  
  Custom R2 endpoint URL. Defaults to `https://<CLOUDFLARE_ACCOUNT_ID>.r2.cloudflarestorage.com`.
- `R2_ACCESS_KEY_ID`  
  S3 access key ID for R2. Defaults to `CLOUDFLARE_ACCOUNT_ID`.
- `R2_SECRET_ACCESS_KEY`  
  S3 secret access key for R2. Defaults to `CLOUDFLARE_API_TOKEN`.
- `CLOUDFLARE_R2_PUBLIC_BASE`  
  Public base URL for direct object access. Defaults to `https://<CLOUDFLARE_ACCOUNT_ID>.r2.cloudflarestorage.com/<CLOUDFLARE_BUCKET>`.

### Example `.env`

```env
CLOUDFLARE_API_TOKEN=your_cloudflare_api_token
CLOUDFLARE_BUCKET=your_r2_bucket_name
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id

# Optional overrides
CLOUDFLARE_R2_ENDPOINT=https://your_account_id.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=your_access_key_id
R2_SECRET_ACCESS_KEY=your_secret_access_key
CLOUDFLARE_R2_PUBLIC_BASE=https://your_account_id.r2.cloudflarestorage.com/your_bucket_name
``` 