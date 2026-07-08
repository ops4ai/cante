# Decisão: DB partilhada (`?schema=`) vs dedicada para Evolution API

**Data:** 2026-07-07
**Decisor:** Agente B (infra), após postmortem do bug do LID
**Âmbito:** `cante-cds` deployment

## Contexto

A Evolution API v2.3.7 usa Prisma e precisa de uma base de dados PostgreSQL para
guardar instâncias, contactos, mensagens, webhooks, etc. Existem duas opções:

| Opção | Connection URI | Pros | Contras |
|-------|---------------|------|---------|
| A — Partilhada (`?schema=`) | `postgresql://.../cante?schema=evolution_api` | 1 DB para gerir, backups unificados, menos infra | Schema não-default; diferença vs known-good |
| B — Dedicada | `postgresql://.../evolution_v2` | Isolamento total, mesmo padrão do gate-evolution | 2 DBs para gerir, backups separados, QR re-scan |

O sistema de referência que funciona (gate-evolution) usa **DB dedicada** `evolution_v2`
com schema `public`. O cante-cds usava originalmente a opção A (partilhada com
`?schema=evolution_api`).

Durante o debug do bug do LID (7 Jul 2026), a opção B foi testada — criou-se a DB
`evolution_v2`, migrou-se a instância, fez-se re-scan QR. O bug do LID **persistiu**
em ambas as configurações, provando que o schema da DB **não era a causa**.

## Decisão

**Mantém-se a opção A — DB partilhada `cante?schema=evolution_api`.**

## Justificação

1. **Provada a funcionar.** Após a fix do `_resolve_lid()` (resolver número→@lid
   via `Contact.profilePicUrl`), as mensagens 1:1 são entregues com
   `DELIVERY_ACK`/`READ` confirmados. O `?schema=` não interfere com o LID
   addressing nem com o `sendText`.

2. **O bug do LID era de código, não de infra.** A resolução de LID falhava porque
   o webhook da Evolution não entrega o LID real — o adapter enviava para
   `@s.whatsapp.net` enquanto a sessão vivia em `@lid`. A fix foi no adapter Python
   (query à `Contact`), não na DB.

3. **Menos complexidade.** Uma DB única significa um backup, um volume, um
   `pg_dump`. A DB `evolution_v2` foi eliminada (DROP DATABASE) — estava vazia e
   era ruído.

4. **Sem re-scan QR.** Migrar para DB dedicada obrigaria a recriar a instância
   Evolution e fazer novo scan QR no telemóvel — risco desnecessário de perder o
   pairing ativo.

5. **O `?schema=` é suportado pelo Prisma.** A Evolution v2.3.7 aceita o parâmetro
   `?schema=evolution_api` na connection string; o Prisma cria as tabelas no schema
   especificado. É uma feature documentada do Prisma, não um workaround.

## Riscos monitorizados

- Se a Evolution no futuro deixar de suportar `?schema=`, o sintoma será
  `Evolution restart-loop` com erro de Prisma. O health check (B5) apanha isso.
- Se houver colisão de nomes de tabela entre o schema `public` do cante
  (SQLAlchemy) e `evolution_api` (Prisma) — não há; schemas diferentes não colidem.

## Coordenação

- A query `_resolve_lid` em `core/cante/evolution.py` lê `evolution_api."Contact"`.
  Se um dia se migrar para DB dedicada, o agente A terá de atualizar essa query.
  Por agora, **não é necessário** — mantém-se como está.

## Referências

- Postmortem do bug do LID: [[cante-cds-whatsapp-lid-fix-2026-07-07]]
- Handoff B: `docs/HANDOFF_B_INFRA_EVOLUTION.md`
- BRINGUP_ERRATA.md §2 — Evolution v2.3 requer DB provider + Redis
