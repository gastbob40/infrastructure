#!/bin/bash

set -e

echo "🔹 Début de la sauvegarde PostgreSQL..."
DATE=$(date +'%Y-%m-%d_%H-%M-%S')
BACKUP_DIR="/tmp/postgres-backups"
mkdir -p "$BACKUP_DIR"


export PGPASSWORD="$PG_PASSWORD"


echo "📦 Connexion à PostgreSQL..."
DB_LIST=$(psql -U "$PG_USER" -h "$PG_HOST" -d postgres -t -A -F '' -c "SELECT json_agg(datname) FROM pg_database WHERE datistemplate = false;" | jq -c '.[]')

if [[ -z "$DB_LIST" ]]; then
  echo "⚠️ Aucune base de données trouvée."
  exit 1
fi

for DB in $(echo "$DB_LIST" | jq -r '.'); do
  echo "💾 Sauvegarde de la base : $DB"

  FILE_NAME="${DB}-${DATE}.dump"
  LOCAL_PATH="/tmp/postgres-backups/${FILE_NAME}"

  S3_PATH="s3://$S3_BUCKET/${DB}/${FILE_NAME}"

  pg_dump -U "$PG_USER" -h "$PG_HOST" -Fc "$DB" -f "$LOCAL_PATH"
  if [[ $? -eq 0 ]]; then
    echo "✅ Sauvegarde de $DB réussie"
  else
    echo "❌ Erreur lors de la sauvegarde de $DB"
  fi

  echo "📤 Upload de la sauvegarde vers S3..."

  aws s3 cp "$LOCAL_PATH" "$S3_PATH" --endpoint-url "$AWS_ENDPOINT_URL"

   if [[ $? -eq 0 ]]; then
      echo "✅ Fichier uploadé sur S3 : $S3_PATH"
    else
      echo "❌ Erreur lors de l'upload vers S3 pour $DB"
    fi


  echo "🗑️ Suppression des anciennes sauvegardes (plus de 14 jours)..."
  aws s3 ls "s3://$S3_BUCKET/${DB}/" --endpoint-url "$AWS_ENDPOINT_URL" | while read -r line; do
    FILE_DATE=$(echo $line | awk '{print $1}')
    FILE_NAME=$(echo $line | awk '{print $4}')

    # Convertir la date du fichier en timestamp
    FILE_TIMESTAMP=$(date -d "$FILE_DATE" +%s)
    CURRENT_TIMESTAMP=$(date +%s)
    THRESHOLD_TIMESTAMP=$(($CURRENT_TIMESTAMP - 14 * 24 * 60 * 60))  # 14 jours en secondes

    if [[ $FILE_TIMESTAMP -lt $THRESHOLD_TIMESTAMP ]]; then
      echo "🗑️ Suppression de l'ancienne sauvegarde : $FILE_NAME"
      aws s3 rm "s3://$S3_BUCKET/${DB}/$FILE_NAME" --endpoint-url "$AWS_ENDPOINT_URL"
    fi
  done
done

echo "🎉 Sauvegarde terminée !"
