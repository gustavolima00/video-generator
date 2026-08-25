# Quickstart: validação de downloads resilientes de backgrounds

**Feature**: 002-youtube-download-resilience

Guia de validação por milestone. Detalhes de comportamento em
[contracts/youtube_proxy_cache.md](./contracts/youtube_proxy_cache.md); esquema
de config em [contracts/configuracao.md](./contracts/configuracao.md).

## Pré-requisitos

```bash
uv sync --extra dev
```

> **Divergência marcada na implementação (T020)**: o `uv sync` puro **remove** o pytest,
> que vive no extra `dev` — o comando original deste guia deixava a suíte impossível de
> rodar. Corrigido para `--extra dev`.

Testes rodam sem rede (proxy interno fake); os cenários manuais marcados com 🌐
tocam o YouTube de verdade — rodar com moderação, o IP pode estar throttled.

> **Divergência marcada na implementação (T020)**: os cenários manuais abaixo mandam
> rodar `tests/script_youtube_download.py`, que está **defasado e não serve** para
> validar esta feature: (a) chama `list_video_ids` de forma síncrona, mas a interface é
> `async` desde antes desta feature; (b) sorteia um `video_id` novo a cada execução, então
> a segunda rodada nunca produziria um hit. Os gates M1/M2 foram executados com scripts
> equivalentes e corretos, montados no scratchpad da sessão. Consertar o script segue
> fora do escopo desta feature — está registrado aqui para quem repetir a validação.

## M1 — Cache (US1)

Automatizado:

```bash
uv run pytest tests/test_caching_youtube_proxy.py tests/test_pytube_proxy.py tests/services -q
```

Esperado: novos testes cobrem C1–C7 e C9–C11 do contrato; testes existentes
passam sem mudança de comportamento.

🌐 Manual (SC-001/SC-002, cache real):

1. `rm -rf ~/.cache/video-generator/backgrounds` (cache frio).
2. Rodar `uv run python tests/script_youtube_download.py` — logs devem mostrar
   misses e o diretório deve ganhar arquivos `{video_id}-hq.mp4`.
3. Rodar de novo — logs devem mostrar apenas hits e nenhuma requisição de
   download (SC-001).
4. Desligar a rede (ou simular 429) e rodar de novo — ainda completa (SC-002).

## M2 — Limite de disco (US2)

Automatizado:

```bash
uv run pytest tests/test_caching_youtube_proxy.py -q
```

> **Divergência marcada na implementação (T020)**: o seletor original `-k evict` só
> alcança 4 dos 9 testes do M2 — os casos de ordem LRU, de touch no hit e de cap menor
> que um clipe não têm "evict" no nome. O gate do T012 rodou o arquivo inteiro; o comando
> aqui foi alinhado a ele.

Esperado: C8 do contrato — total nunca excede o cap; ordem LRU; touch no hit
muda a ordem; cap menor que um clipe não quebra a execução.

Manual (SC-003): configurar `max_gigabytes` pequeno no `config.yaml`, rodar o
script duas vezes e conferir `du -sh ~/.cache/video-generator/backgrounds`.

## M3 — po_token (US3)

Automatizado:

```bash
uv run pytest tests/test_pytube_proxy.py -q
```

Esperado: T1–T6 do contrato (construção do `YouTube` com/sem token; erro de
configuração pela metade; mensagem de expiração apontando a doc; 429 ainda
aborta).

Higiene de segredo (FR-009):

```bash
git grep -iE "po_token|visitor_data" -- ':!specs' ':!docs' ':!*.md'
```

Esperado: apenas código/testes usando os nomes de campo — nenhum valor real.

🌐 Manual (SC-005): seguir `docs/po-token.md` do zero — obter token no
navegador, preencher `.env`, rodar o script e ver downloads passando com o
token; depois corromper o token e conferir que a falha aponta a doc.

## Regressão completa

```bash
uv run pytest -q
```
