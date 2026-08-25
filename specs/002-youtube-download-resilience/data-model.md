# Data Model: Downloads resilientes de backgrounds do YouTube

**Feature**: 002-youtube-download-resilience | **Date**: 2026-08-25

Nenhum banco de dados: o estado vive no filesystem (cache) e em modelos de
configuração pydantic já existentes no projeto.

## Entidades

### Entrada de cache (clipe armazenado)

Representada por um arquivo no diretório de cache — sem índice separado; o
filesystem é a fonte de verdade.

| Atributo | Representação | Regras |
|----------|---------------|--------|
| `video_id` | Parte do nome do arquivo | 11 chars `[-_A-Za-z0-9]` (já validado no proxy) |
| Faixa de qualidade | Sufixo do nome: `hq` (padrão) ou `lq` (`low_quality=True`) | Faixas nunca servem uma à outra |
| Nome do arquivo | `{video_id}-{hq|lq}.mp4` | Diretório plano |
| Conteúdo | Bytes do mp4 exatamente como o proxy interno retornou | Publicado só via `os.replace` (nunca parcial) |
| Último uso | `mtime` do arquivo | Atualizado com `os.utime` a cada hit; critério do LRU |
| Tamanho | `st_size` | Somado para o cap de disco |

**Validade no hit**: legível, não vazio e box `ftyp` no offset 4. Inválido →
apagar + tratar como miss (re-download). Nunca servir bytes inválidos.

**Transições**:

```text
(ausente) --download ok + escrita ok--> válido
(ausente) --download ok + escrita falhou--> (ausente)  [warning; bytes servidos mesmo assim]
válido    --hit--> válido (mtime atualizado)
válido    --eviction LRU--> (ausente)
válido    --detectado inválido no hit--> (ausente) → re-download
```

### `BackgroundCacheConfig` (novo, aninhado em `PyTubeYouTubeConfig`)

Arquivo: `src/entities/configs/proxies/youtube.py` (yaml:
`proxies.youtube_config.cache`).

| Campo | Tipo | Default | Regras |
|-------|------|---------|--------|
| `enabled` | `bool` | `True` | `False` → factory não monta o decorator (comportamento atual) |
| `dir` | `str` | `~/.cache/video-generator/backgrounds` | `~` expandido na construção; criado se ausente; não gravável → modo download-only com warning (FR-006) |
| `max_gigabytes` | `float` | `20.0` | > 0; cap aplicado após cada escrita (LRU) |

### `PyTubeYouTubeConfig` (alterado)

| Campo | Tipo | Default | Observação |
|-------|------|---------|------------|
| `download_clients` | `List[str]` | `["WEB", "MWEB", "WEB_SAFARI"]` | Existente, inalterado |
| `cache` | `BackgroundCacheConfig` | instância default | Novo (M1/M2) |
| `po_token` | `Optional[str]` | `None` | Novo (M3); preenchido pela factory a partir de `Secrets`, nunca pelo yaml |
| `visitor_data` | `Optional[str]` | `None` | Novo (M3); idem; obrigatório junto com `po_token` (o pytubefix exige o par) |

Normalização: token/visitor_data em branco ou whitespace → `None` (edge case do
spec). `po_token` sem `visitor_data` (ou vice-versa) → erro claro na construção
do proxy (fail fast: configuração pela metade é engano do operador, não estado a
contornar).

### `Secrets` (alterado — `src/core/secrets.py`, fonte `.env`)

| Campo | Tipo | Default |
|-------|------|---------|
| `youtube_po_token` | `Optional[str]` | `None` |
| `youtube_visitor_data` | `Optional[str]` | `None` |

Fluxo: `Secrets` → `container.py` → `YouTubeProxyFactory.create(...)` → seta na
config → `PyTubeProxy`. Mesmo padrão dos demais segredos (ex.:
`config.api_key = leonardo_api_key`).

## Relações

```text
YouTubeProxyFactory
  └─ cria PyTubeProxy(config)                  [token via Secrets]
     └─ envolve em CachingYouTubeProxy(inner, cache_config)   [se cache.enabled]
        └─ injetado no VideoService como IYouTubeProxy  (service inalterado)
```
