# Backup & Restore — cante-cds

**Data:** 2026-07-08
**Responsável:** Infra (agente B)

## O que precisa de backup

| Recurso | Localização | Contém |
|---------|------------|--------|
| PostgreSQL | Volume `cante-cds_postgres_data` | Schema `public` (cante — numbers, bots, routes, messages) + Schema `evolution_api` (Evolution — instância, contactos, webhooks, sessão WhatsApp) |
| Redis | Volume (interno ao container) | Streams de mensagens (efémeras — não crítico, reconstruível) |
| `.env` | `/root/ops4ai/cante-cds/.env` | Secrets, API keys, config |
| `frontend/dist/` | Host filesystem | SPA bundle (reconstruível via `npm run build`) |

**Crítico:** PostgreSQL + `.env`. Sem o schema `evolution_api`, perde-se o
pairing da sessão WhatsApp e é necessário re-scan QR.

## Backup — pg_dump

### Script de backup
Ver `scripts/backup-db.sh`.

### Configuração cron recomendada
```
# Diário às 03:17 UTC, guarda 7 dias
17 3 * * * /root/ops4ai/cante-cds/scripts/backup-db.sh
```

### O que o script faz
1. `pg_dump` da DB `cante` (schemas `public` + `evolution_api`) via container postgres
2. Comprime com `gzip`
3. Guarda em `/root/ops4ai/cante-cds/backups/` com timestamp
4. Remove backups com mais de `RETENTION_DAYS` dias (default: 7)
5. Opcional: envia para S3/remoto se `BACKUP_REMOTE` estiver configurado

## Restore

### Full restore (toda a DB)
```bash
cd /root/ops4ai/cante-cds
# 1. Parar serviços que escrevem na DB
docker compose stop api worker sender scheduler ingress

# 2. Restore
gunzip -c backups/cante-cds-YYYYMMDD-HHMMSS.sql.gz | \
  docker exec -i cante-cds-postgres psql -U cante -d cante

# 3. Reiniciar
docker compose start api worker sender scheduler ingress
```

### Restore só do schema evolution_api (preserva dados do cante)
```bash
# Extrair só o schema evolution_api do dump
gunzip -c backups/cante-cds-YYYYMMDD-HHMMSS.sql.gz | \
  sed -n '/^-- Name: evolution_api/,/^-- Name:/p' | \
  docker exec -i cante-cds-postgres psql -U cante -d cante

# ATENÇÃO: isto é aproximado. O dump em plain-text tem marcadores de schema.
# Método mais fiável: restore para uma DB temporária e copia.
```

### Restore manual (via pg_restore com custom format)
Se usares `pg_dump -Fc` (custom format):
```bash
docker exec -i cante-cds-postgres pg_restore -U cante -d cante --clean --if-exists \
  < backups/cante-cds-YYYYMMDD-HHMMSS.dump
```

## Recuperação do pairing WhatsApp sem backup

Se o schema `evolution_api` se perder e não houver backup:
1. `docker compose stop sender`
2. `docker exec cante-cds-postgres psql -U cante -d cante -c "DROP SCHEMA IF EXISTS evolution_api CASCADE;"`
3. Reiniciar o Evolution → `docker restart cante-cds-evolution` (recria as tabelas)
4. **No telemóvel:** WhatsApp → Dispositivos ligados → remover "Ops4.AI"
5. Criar nova instância + re-scan QR (ver `docs/runbook-evolution-reset.md`)
6. `docker compose start sender`

## Verificação pós-backup

```bash
# Confirmar que o dump contém as tabelas da Evolution
gunzip -c backups/cante-cds-*.sql.gz | grep -c "evolution_api." | head -1

# Tamanho do backup
ls -lh backups/
```
