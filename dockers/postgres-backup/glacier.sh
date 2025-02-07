# export AWS_ACCESS_KEY_ID_PRIVATE="TON_ACCESS_KEY_S3_PRIVÉ"
# export AWS_SECRET_ACCESS_KEY_PRIVATE="TON_SECRET_KEY_S3_PRIVÉ"
# export AWS_ENDPOINT_S3_PRIVATE="https://api.s3.gastbob40.ovh"  # URL de ton S3 privé

# 🔹 Configurer les identifiants pour AWS S3 Glacier
# export AWS_ACCESS_KEY_ID_GLACIER="TON_ACCESS_KEY_AWS_S3"
# export AWS_SECRET_ACCESS_KEY_GLACIER="TON_SECRET_KEY_AWS_S3"
# export AWS_REGION_GLACIER="eu-central-1"
# export AWS_BUCKET_GLACIER="backups"


AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID_PRIVATE AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY_PRIVATE aws s3api put-bucket-lifecycle-configuration --bucket backups --region eu-central-1 --lifecycle-configuration '{
  "Rules": [
    {
      "ID": "DeleteBackupsAfter1Year",
      "Status": "Enabled",
      "Prefix": "",
      "Expiration": {
        "Days": 365
      },
      "NoncurrentVersionExpiration": {
        "NoncurrentDays": 365
      }
    }
  ]
  }'

# 🔹 Dossier temporaire pour stocker les backups
BACKUP_DIR="/tmp/s3-backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +'%Y-%m-%d_%H-%M-%S')

echo "🔹 Récupération de la liste des buckets depuis S3 privé..."
BUCKETS=$(AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID_PRIVATE AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY_PRIVATE aws s3 ls --endpoint-url "$AWS_ENDPOINT_S3_PRIVATE" | awk '{print $3}')

if [[ -z "$BUCKETS" ]]; then
  echo "❌ Aucun bucket trouvé sur S3 privé !"
  exit 1
fi


for BUCKET in $BUCKETS; do
  echo "💾 Téléchargement du bucket: $BUCKET"
  LOCAL_ZIP="$BACKUP_DIR/${BUCKET}_${DATE}.zip"
  S3_PATH="s3://$AWS_BUCKET_GLACIER/$BUCKET/${BUCKET}_${DATE}.zip"

  # 🔹 Télécharger le contenu du bucket localement
  LOCAL_BUCKET_DIR="$BACKUP_DIR/$BUCKET"
  mkdir -p "$LOCAL_BUCKET_DIR"
  AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID_PRIVATE AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY_PRIVATE aws s3 sync "s3://$BUCKET" "$LOCAL_BUCKET_DIR" --endpoint-url "$AWS_ENDPOINT_S3_PRIVATE"

  # 🔹 Créer un fichier ZIP du bucket
  echo "📦 Compression du bucket $BUCKET..."
  zip -r "$LOCAL_ZIP" "$LOCAL_BUCKET_DIR"

  # 🔹 Upload du fichier sur AWS S3 Glacier Deep Archive
  echo "📤 Upload du fichier ZIP vers AWS S3 Glacier Deep Archive..."
  AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID_GLACIER AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY_GLACIER aws s3 cp "$LOCAL_ZIP" "$S3_PATH" --storage-class DEEP_ARCHIVE --region "$AWS_REGION_GLACIER"

  if [[ $? -eq 0 ]]; then
    echo "✅ Backup de $BUCKET transféré vers AWS S3 Glacier Deep Archive : $S3_PATH"
  else
    echo "❌ Erreur lors de l'upload de $BUCKET"
  fi

  # 🔹 Nettoyage des fichiers temporaires
  rm -rf "$LOCAL_BUCKET_DIR" "$LOCAL_ZIP"
done

echo "🎉 Sauvegarde complète vers AWS Glacier Deep Archive avec rétention automatique de 1 an !"