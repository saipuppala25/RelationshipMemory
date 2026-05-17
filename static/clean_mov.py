import os
import boto3
from dotenv import load_dotenv

# Load variables from your local .env file
load_dotenv()

# Retrieve credentials safely from environment variables
ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "memories")

# Initialize S3 client pointing to Cloudflare R2
s3 = boto3.client(
    service_name="s3",
    endpoint_url=f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=ACCESS_KEY_ID,
    aws_secret_access_key=SECRET_ACCESS_KEY,
)

def clean_mov_files():
    print(f"Connecting to R2 Bucket: '{BUCKET_NAME}'...")
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=BUCKET_NAME)
    
    delete_batch = []
    count = 0

    for page in pages:
        if "Contents" not in page:
            continue
            
        for obj in page["Contents"]:
            key = obj["Key"]
            # Case-insensitive check for .mov extension
            if key.lower().endswith(".mov"):
                print(f"Queued for deletion: {key}")
                delete_batch.append({"Key": key})
                count += 1
                
                # Delete in batches of 1000 (S3 API limit)
                if len(delete_batch) == 1000:
                    s3.delete_objects(Bucket=BUCKET_NAME, Delete={"Objects": delete_batch})
                    delete_batch = []

    # Delete any remaining files in the final batch
    if delete_batch:
        s3.delete_objects(Bucket=BUCKET_NAME, Delete={"Objects": delete_batch})

    print(f"\nCleanup complete. Successfully deleted {count} .mov files.")

if __name__ == "__main__":
    if not ACCOUNT_ID or not ACCESS_KEY_ID or not SECRET_ACCESS_KEY:
        print("Error: Missing R2 environment variables in your .env file.")
    else:
        clean_mov_files()
