# Runbook — Reset completo da Evolution API

**Data:** 2026-07-08
**Gatilho:** Bug do LID regressa, proliferação de instâncias (>3), erros de
entrega em massa (`MessageUpdate.status='ERROR'`), ou sessão WhatsApp corrompida.

**⚠️ AVISO:** Este procedimento **perde o pairing atual** da sessão WhatsApp.
Terás de fazer scan QR com o telemóvel novamente.
**NÃO executes este runbook a menos que seja estritamente necessário.**
Confirma primeiro que o problema não é resolúvel com um simples restart.

---

## Pré-verificação (antes de resetar)

1. Confirma que há erro real e não é alarme falso:
   ```bash
   cd /root/ops4ai/cante && source .env
   docker exec cante-postgres psql -U cante -d cante -tAc \
     "SELECT status, count(*) FROM evolution_api.\"MessageUpdate\" GROUP BY status;"
   ```

2. Verifica o estado da instância:
   ```bash
   curl -sH "apikey:$EVOLUTION_API_KEY" \
     http://127.0.0.1:8088/instance/connectionState/canteEXAMPLEINSTANCE
   # Esperado: {"instance":{"state":"open"}}
   # Se != "open", tenta um reconnect antes do reset:
   # curl -sH "apikey:$EVOLUTION_API_KEY" \
   #   "http://127.0.0.1:8088/instance/connect/canteEXAMPLEINSTANCE"
   ```

3. Verifica instâncias:
   ```bash
   curl -sH "apikey:$EVOLUTION_API_KEY" http://127.0.0.1:8088/instance/fetchInstances | python3 -m json.tool | grep '"name"'
   ```
   Se `count > 3`, há proliferação — o reset é justificado.

---

## Procedimento de reset

### Passo 1: Parar o sender
```bash
cd /root/ops4ai/cante
docker compose stop sender
```
O sender é o único serviço que envia mensagens. Pará-lo evita que tente enviar
durante o reset, acumulando erro.

### Passo 2: Limpar a stream Redis de outbound
```bash
docker exec cante-redis redis-cli XTRIM stream:outbound MAXLEN 0
```
Remove mensagens pendentes que já não vão ser entregues (a instância vai ser
recriada).

### Passo 3: Apagar dados da Evolution
```bash
# Truncar todas as tabelas do schema evolution_api
docker exec cante-postgres psql -U cante -d cante -tAc "
DO \$\$ DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'evolution_api') LOOP
        EXECUTE 'TRUNCATE TABLE evolution_api.\"' || r.tablename || '\" CASCADE';
    END LOOP;
END \$\$;
"

# Apagar ficheiros de estado da instância (sessão Signal, credenciais)
docker exec cante-evolution rm -rf /evolution/instances/*

# Reiniciar o Evolution (recria as tabelas via Prisma migrations)
docker restart cante-evolution
```

### Passo 4: No telemóvel — remover dispositivo
1. Abrir WhatsApp
2. Definições → Dispositivos ligados
3. Remover "Ops4.AI" (ou qualquer dispositivo cante)

### Passo 5: Recriar a instância + scan QR

**Opção A — Via curl (rápido):**
```bash
source .env
INSTANCE="canteEXAMPLEINSTANCE"

# Criar instância
curl -s -X POST "http://127.0.0.1:8088/instance/create" \
  -H "apikey:${EVOLUTION_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"instanceName\": \"${INSTANCE}\",
    \"token\": \"$(uuidgen)\",
    \"qrcode\": true,
    \"integration\": \"WHATSAPP-BAILEYS\"
  }"

# Obter QR code (base64)
curl -s -H "apikey:${EVOLUTION_API_KEY}" \
  "http://127.0.0.1:8088/instance/connect/${INSTANCE}" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('qrcode','no qr'))
# se base64, descodifica: print(base64.b64decode(d['qrcode']))
# ou usa o code do response
"

# Esperar estado "open"
sleep 5
curl -s -H "apikey:${EVOLUTION_API_KEY}" \
  "http://127.0.0.1:8088/instance/connectionState/${INSTANCE}"
```

**Opção B — Via API do cante (recomendado se disponível):**
`POST /v1/numbers` com os dados do número — a API cria a instância, configura o
webhook, e devolve o QR code. Usa o backoffice em `https://cante.srv.example.com`.

### Passo 6: Configurar webhook

```bash
source .env
INSTANCE="canteEXAMPLEINSTANCE"
WEBHOOK_SECRET=$(docker exec cante-postgres psql -U cante -d cante -tAc \
  "SELECT connection_config->>'webhook_secret' FROM \"Number\" WHERE phone = '+351900000001';")

curl -s -X POST "http://127.0.0.1:8088/webhook/set/${INSTANCE}" \
  -H "apikey:${EVOLUTION_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"webhook\": {
      \"enabled\": true,
      "url": \"http://cante-ingress:8001/channels/{number_id}/webhook\",
      \"byEvents\": true,
      \"events\": [\"MESSAGES_UPSERT\"],
      \"headers\": {\"x-webhook-token\": \"${WEBHOOK_SECRET}\"}
    }
  }"
```

### Passo 7: Reiniciar o sender
```bash
docker compose start sender
```

### Passo 8: Teste de entrega
De outro telemóvel (não o pareado), envia uma mensagem para o número do bot
(+351900000001) e verifica:
```sql
-- Após alguns segundos:
SELECT status, count(*) FROM evolution_api."MessageUpdate"
WHERE "remoteJid" LIKE '%<número_de_teste>%'
GROUP BY status;
-- Esperado: DELIVERY_ACK ou READ (NÃO ERROR)
```

---

## Prevenção (para não precisar deste runbook)

1. **Monitor de ERRORs** — `scripts/monitor-evolution-errors.sh` (cron a cada 5 min).
   Um ERROR novo em 15 min → alerta. Responde ANTES que acumulem 12+ ERRORs.

2. **Monitor de saúde** — `scripts/monitor-health.sh` (cron a cada 5 min).
   Alerta se instância != "open" ou count > 3.

3. **Não mexer na DB da Evolution manualmente** — cria-se instâncias órfãs.
   Usa a API da Evolution ou a API do cante (`POST /v1/numbers`).

4. **Backups** — `scripts/backup-db.sh` (cron diário).
   Com backup do schema `evolution_api`, o restore é trivial e não perde pairing.
