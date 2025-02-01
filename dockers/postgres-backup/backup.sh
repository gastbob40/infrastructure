set -e

echo "🔹 Début de la sauvegarde PostgreSQL..."
DATE=$(date +'%Y-%m-%d_%H-%M-%S')
BACKUP_DIR="/tmp/postgres-backups/$DATE"
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
  pg_dump -U "$PG_USER" -h "$PG_HOST" -Fc "$DB" -f "$BACKUP_DIR/${DB}.dump"
  if [[ $? -eq 0 ]]; then
    echo "✅ Sauvegarde de $DB réussie"
  else
    echo "❌ Erreur lors de la sauvegarde de $DB"
  fi
done

echo "📤 Upload des fichiers vers S3..."
aws s3 cp --recursive "$BACKUP_DIR" "s3://$S3_BUCKET/postgres-backups/$DATE/"

if [[ $? -eq 0 ]]; then
  echo "✅ Sauvegarde transférée sur S3 : s3://$S3_BUCKET/postgres-backups/$DATE/"
else
  echo "❌ Erreur lors du transfert vers S3"
fi

echo "🎉 Sauvegarde terminée !"