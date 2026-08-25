# Implementation Plan: Downloads resilientes de backgrounds do YouTube

**Branch**: `main` (sem branch dedicada criada; diretório da feature: `002-youtube-download-resilience`) | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-youtube-download-resilience/spec.md`

## Summary

Tornar a execução diária independente da rede para clipes já baixados e resistente ao
throttling por IP (HTTP 429) do YouTube. Duas entregas, em ordem de prioridade:

1. **Cache local de backgrounds** (correção principal): um decorator
   `CachingYouTubeProxy` sobre a interface `IYouTubeProxy` que, antes de baixar,
   procura o mp4 no disco por `video_id` + faixa de qualidade e só vai à rede em
   miss. Escrita atômica (tmp + `os.replace`), validação barata no hit, limite de
   disco configurável com eviction LRU por `mtime`. O `VideoService` não muda.
2. **Suporte a po_token** (fallback para misses): token de prova de origem +
   `visitor_data` fornecidos via `.env` (padrão `Secrets` existente), injetados no
   `PyTubeProxy` e repassados ao pytubefix 10.11.0 via
   `use_po_token=True` + `po_token_verifier`. Ausência de token = comportamento
   idêntico ao atual. Documentação de obtenção/renovação em português.

## Technical Context

**Language/Version**: Python 3.11+ (venv atual: 3.12)

**Primary Dependencies**: pytubefix==10.11.0 (pinado; mecanismo de po_token
verificado no pacote instalado), pydantic/pydantic-settings (configs e secrets),
dependency-injector (`src/core/container.py`), moviepy (consumo dos bytes)

**Storage**: sistema de arquivos — diretório de cache configurável, padrão
`~/.cache/video-generator/backgrounds` (fora da working tree), arquivos
`{video_id}-{hq|lq}.mp4`, LRU por `mtime`

**Testing**: pytest (`tests/`), no padrão de `tests/test_pytube_proxy.py` e
`tests/services/`

**Target Platform**: macOS (laptop) e Linux (servidor), caches independentes por
máquina

**Project Type**: Single project, Clean Architecture em módulos (`src/proxies`,
`src/services`, `src/entities`, `src/core`)

**Performance Goals**: com cache quente, zero requisições de download em execução
repetida sobre o mesmo pool (SC-001) e ≥80% menos tempo na aquisição de
backgrounds (SC-006)

**Constraints**: uso de disco do cache ≤ limite configurado (padrão 20 GB, SC-003);
token nunca em arquivo rastreado (FR-009); sem token, comportamento byte a byte
igual ao atual (FR-010); aborto imediato em 429 preservado (FR-013)

**Scale/Scope**: pool ~144 vídeos (3 canais × pool 100, com dedup), execução
diária não assistida, 2 máquinas atrás do mesmo IP público

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Princípio | Avaliação | Resultado |
|-----------|-----------|-----------|
| I. Fail Fast e Simplicidade | O cache introduz ramos de degradação graciosa (arquivo corrompido → miss; escrita falhou → segue sem cachear) que contrariam o default de falhar cedo. A própria constituição admite robustez quando "o caso é frequente ou o custo da falha é alto": a execução é diária e não assistida, e a falha custa o vídeo do dia. Cada desvio é exigido por FR explícito (FR-005, FR-006) e está justificado em Complexity Tracking. Fora desses pontos, o desenho falha cedo (ex.: 429 continua abortando — FR-013; config inválida estoura na carga). | PASS |
| II. Arquitetura Limpa em Módulos | Cache como decorator de `IYouTubeProxy` montado na factory: o `VideoService` (regra de negócio) não sabe que existe cache; armazenamento e YouTube continuam detalhes na borda (`src/proxies`). Token entra pelo padrão existente `Secrets` → container → factory → config do proxy, como os demais segredos. Nenhuma camada interna passa a conhecer camada externa. | PASS |
| III. Prompts Baseados em Racional | N/A — a feature não toca prompts de LLM. | N/A |
| Idioma | Artefatos de plano/documentação do operador em português; código, comentários e commits em inglês. Desvio pontual: `spec.md` desta feature foi escrito em inglês, acompanhando o idioma do pedido do usuário — mantido por rastreabilidade (justificado em Complexity Tracking). | PASS (com desvio documentado) |

**Re-check pós-design (Phase 1)**: o desenho final não adicionou camadas nem
abstrações além do decorator e de um modelo de config aninhado; nenhum novo
desvio. PASS.

## Delivery Plan

3 milestones, ~1 PR cada (≤ 8, dentro do limite). Cada um é independentemente
testável e entrega valor sozinho, espelhando as user stories P1→P3.

### M1 — Cache de backgrounds (US1 / P1) — PR 1

Decorator `CachingYouTubeProxy` (novo `src/proxies/caching_youtube_proxy.py`),
config aninhada `BackgroundCacheConfig` em `PyTubeYouTubeConfig`, montagem na
`YouTubeProxyFactory`, escrita atômica, validação no hit, log de hit/miss.
Sem eviction ainda (limite chega no M2).

**Gate de verificação M1**:
- Testes novos: hit não chama o proxy interno; miss chama uma vez e persiste;
  arquivo corrompido/truncado → descartado e re-baixado; diretório não gravável →
  warning e download segue; escrita é atômica (sem arquivo parcial visível);
  chaves `hq`/`lq` não se misturam.
- Testes existentes (`tests/test_pytube_proxy.py`, `tests/services/`) passam sem
  modificação de comportamento.
- Cenário SC-001/SC-002 simulado em teste: segunda chamada com proxy interno que
  falharia (throttle simulado) ainda retorna os bytes.

### M2 — Limite de disco com eviction LRU (US2 / P2) — PR 2

Eviction por `mtime` após cada escrita até caber no `max_gigabytes` (padrão 20);
hit faz touch (`os.utime`); cap menor que o arquivo recém-escrito ainda serve a
execução corrente (bytes já em memória).

**Gate de verificação M2**:
- Testes: tamanho total nunca excede o cap após inserções; ordem de eviction é
  LRU; reuso conta como uso (touch); entrada evictada volta como miss simples;
  cap menor que um clipe não quebra a execução.
- SC-003 verificável via teste com cap pequeno artificial.

### M3 — po_token configurável + documentação (US3 / P3) — PR 3

Campos `youtube_po_token`/`youtube_visitor_data` em `Secrets` (`.env`),
pass-through no container/factory até `PyTubeYouTubeConfig`, uso no
`PyTubeProxy._download_with_client` via `use_po_token=True` +
`po_token_verifier`, limpeza do cache interno de tokens do pytubefix quando um
token é configurado (evita token rotacionado ser sombreado — ver research.md),
mensagem de erro apontando a doc quando um download falha com token configurado,
e `docs/po-token.md` em português (obter, instalar, detectar expiração, renovar).

**Gate de verificação M3**:
- Testes: com token configurado, `YouTube` recebe `use_po_token=True` e o
  verifier retorna `(visitor_data, po_token)` da config; sem token (ou token em
  branco), `YouTube` é construído exatamente como hoje; falha com token
  configurado menciona renovação e a doc.
- FR-004 do spec de qualidade: nenhum valor de token em arquivo rastreado
  (`git grep` limpo; `.env` já está no `.gitignore`).
- SC-005: doc revisada seguindo apenas os passos escritos.

**Projeção**: 3 PRs.

## Project Structure

### Documentation (this feature)

```text
specs/002-youtube-download-resilience/
├── plan.md              # Este arquivo
├── research.md          # Phase 0 (decisões e mecanismo pytubefix verificado)
├── data-model.md        # Phase 1 (entidades: cache, configs, secrets)
├── quickstart.md        # Phase 1 (guia de validação)
├── contracts/
│   ├── youtube_proxy_cache.md   # Contrato de comportamento do decorator
│   └── configuracao.md          # Esquema yaml + variáveis .env
└── tasks.md             # Phase 2 (/speckit-tasks — não criado por /speckit-plan)
```

### Source Code (repository root)

```text
src/
├── proxies/
│   ├── pytube_proxy.py             # M3: passa po_token ao pytubefix; erro aponta doc
│   ├── caching_youtube_proxy.py    # M1/M2: NOVO — decorator de cache sobre IYouTubeProxy
│   ├── factories.py                # M1/M3: YouTubeProxyFactory monta decorator + injeta token
│   └── interfaces.py               # INALTERADO (IYouTubeProxy é contrato congelado)
├── entities/configs/proxies/
│   └── youtube.py                  # M1/M2: BackgroundCacheConfig; M3: campos de token
├── core/
│   ├── secrets.py                  # M3: youtube_po_token, youtube_visitor_data
│   └── container.py                # M3: repassa secrets à YouTubeProxyFactory
└── services/
    └── video_service.py            # INALTERADO

docs/
└── po-token.md                     # M3: NOVO — doc do operador (português)

tests/
├── test_caching_youtube_proxy.py   # M1/M2: NOVO
├── test_pytube_proxy.py            # M3: casos de token; existentes intactos
└── services/                       # INALTERADOS

config.yaml / config.prod.yaml      # M1/M2: bloco cache (com placeholders/comentários)
```

**Structure Decision**: projeto único existente; toda a mudança fica na borda
(`src/proxies`, `src/entities/configs`, `src/core`), com um arquivo novo de
proxy, um novo arquivo de teste e uma doc de operador. `VideoService` e
`IYouTubeProxy` são invariantes da feature.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Degradação graciosa no cache (corrompido → miss; escrita best-effort) em vez de fail fast | FR-005/FR-006: execução diária não assistida; a falha custa o vídeo do dia e o caso "rede indisponível/disco cheio" é exatamente o cenário que a feature existe para cobrir | Falhar cedo abortaria a run por um problema de *cache* — pior que não ter cache; a constituição admite robustez quando custo da falha é alto |
| Validação barata no hit (não-vazio + assinatura mp4) em vez de nenhuma ou de parse completo | FR-005 exige detectar arquivo truncado/ilegível sem envenenar o id | Nenhuma validação deixaria um arquivo corrompido ser pulado para sempre pelo service; parse completo (moviepy) no hit custaria mais do que o download que o cache evita |
| `spec.md` em inglês (constituição pede docs ao usuário em português) | O pedido do usuário veio em inglês; manter o idioma preserva rastreabilidade termo a termo com o input | Traduzir agora só re-escreveria um artefato já validado; artefatos de plano seguem em português |
