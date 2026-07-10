# Webhook Persistence — Evolution API

**Data:** 2026-07-08

## Comportamento

### O webhook persiste entre restarts do container Evolution
Sim. O webhook configurado via `POST /webhook/set/{instance}` é guardado na
tabela `evolution_api."Webhook"` (Prisma). Um `docker restart` do container
Evolution **não** perde a configuração — o Evolution relê a DB ao iniciar e
re-regista o webhook automaticamente.

### O webhook NÃO persiste se a instância for recriada
Se a instância for eliminada (`DELETE /instance/delete/{instance}`) e recriada,
a nova instância tem um `instanceId` novo e as rows associadas na DB são
removidas em cascata. O webhook **tem de ser re-configurado manualmente** após
recriar a instância.

## Como configurar o webhook (runbook)

### Via API do cante (fluxo normal — recomendado)
O endpoint `POST /v1/numbers` chama `EvolutionAdapter.set_webhook()` automaticamente
(`services/api/main.py:508`). Usa este fluxo se possível.

### Via curl (manual — para a instância atual que foi criada por curl)
```bash
source .env
curl -X POST "http://127.0.0.1:8088/webhook/set/canteEXAMPLEINSTANCE" \
  -H "apikey:${EVOLUTION_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "enabled": true,
      "url": "http://cante-ingress:8001/channels/{number_id}/webhook",
      "byEvents": true,
      "events": ["MESSAGES_UPSERT"],
      "headers": {"x-webhook-token": "<webhook_secret>"}
    }
  }'
```

O `<webhook_secret>` é o valor de `Number.connection_config.webhook_secret` para
o número configurado — consulta a DB:
```sql
SELECT connection_config->>'webhook_secret'
FROM "Number"
WHERE phone = '+351900000001';
```

## Estado atual (2026-07-08)
- Instância: `canteEXAMPLEINSTANCE` (criada por curl durante debug do LID).
- Webhook: configurado manualmente para `MESSAGES_UPSERT` → `http://cante-ingress:8001/channels/{number_id}/webhook`.
- Persiste restarts do container Evolution ✓
- Se a instância for recriada: **reconfigurar o webhook** (query de cima + curl).
