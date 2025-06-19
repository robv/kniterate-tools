import os
try:
    # Load environment variables from .env file if available
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Cloudflare R2 basic configuration
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
CLOUDFLARE_BUCKET = os.getenv("CLOUDFLARE_BUCKET")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")

# Derive the R2 endpoint URL from account ID if not explicitly set
CLOUDFLARE_R2_ENDPOINT = os.getenv(
    "CLOUDFLARE_R2_ENDPOINT",
    f"https://{CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com"
)

# AWS S3 (R2) credentials for boto3
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID") or CLOUDFLARE_ACCOUNT_ID
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY") or CLOUDFLARE_API_TOKEN

# Cloudflare R2 API endpoints
CLOUDFLARE_R2_API_BASE = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/r2/buckets/{CLOUDFLARE_BUCKET}/objects"
# Public base URL for direct object access
CLOUDFLARE_R2_PUBLIC_BASE = os.getenv("CLOUDFLARE_R2_PUBLIC_BASE") or f"https://{CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com/{CLOUDFLARE_BUCKET}" 