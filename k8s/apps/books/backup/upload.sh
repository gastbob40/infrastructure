#!/bin/bash
set -euo pipefail

# The existing Glacier job archives every private bucket. Keep one full copy here.
bucket=${S3_BUCKET:?}
endpoint=${AWS_ENDPOINT_URL:?}
archive=/backup/calibre.tar.gz
checksum=$(cat /backup/calibre.tar.gz.sha256)

if ! aws s3api head-bucket --bucket "$bucket" --endpoint-url "$endpoint"; then
  aws s3api create-bucket --bucket "$bucket" --endpoint-url "$endpoint" >/dev/null
fi
aws s3 cp "$archive" "s3://$bucket/calibre.tar.gz" \
  --endpoint-url "$endpoint" --only-show-errors \
  --metadata "sha256=$checksum"

remote_checksum=$(aws s3api head-object --bucket "$bucket" --key calibre.tar.gz \
  --endpoint-url "$endpoint" --query 'Metadata.sha256 || Metadata.Sha256' --output text)
if [ "$remote_checksum" != "$checksum" ]; then
  echo "S3 object checksum metadata mismatch" >&2
  exit 1
fi
# Read the stored bytes back, rather than relying only on object metadata.
aws s3 cp "s3://$bucket/calibre.tar.gz" - --endpoint-url "$endpoint" --only-show-errors \
  | sha256sum | awk '{print $1}' > /backup/remote.sha256
if [ "$(cat /backup/remote.sha256)" != "$checksum" ]; then
  echo "S3 read-back checksum mismatch" >&2
  exit 1
fi
echo "Calibre backup uploaded and read-back SHA-256 verified."
