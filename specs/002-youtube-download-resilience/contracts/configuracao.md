# Contract: Superfície de configuração

**Feature**: 002-youtube-download-resilience

## `config.yaml` / `config.prod.yaml` (rastreados — nunca contêm segredo)

```yaml
proxies:
  youtube_config:
    type: pytube
    download_clients: [WEB, MWEB, WEB_SAFARI]   # existente
    cache:                                       # novo (M1/M2)
      enabled: true
      dir: ~/.cache/video-generator/backgrounds  # expandido; criado se ausente
      max_gigabytes: 20
```

Omissão do bloco `cache` inteiro = defaults acima (cache ligado). Ou seja, os
yamls só precisam mudar se o operador quiser outro caminho/limite; comentários
com placeholders documentam o bloco.

## `.env` (não rastreado — `.gitignore` já cobre) — M3

```dotenv
# Par obrigatório: o pytubefix exige visitorData junto com o po_token.
# Como obter/renovar: docs/po-token.md
YOUTUBE_PO_TOKEN=coloque_aqui_o_po_token
YOUTUBE_VISITOR_DATA=coloque_aqui_o_visitor_data
```

Regras:

- Ausentes/vazios → downloads se comportam exatamente como hoje (FR-010).
- Apenas um dos dois presente → erro na inicialização (fail fast, T3).
- Nenhum valor real de token aparece em arquivo rastreado (FR-009); exemplos
  usam placeholders.

## Documentação do operador — `docs/po-token.md` (M3, em português)

Deve cobrir, no mínimo:

1. O que o token resolve (429/bot detection) e o que não resolve (primeiro
   download de um clipe com IP bloqueado e cache frio).
2. Passo a passo de obtenção (navegador → DevTools → `v1/player` →
   `serviceIntegrityDimensions.poToken` + `context.client.visitorData`).
3. Onde instalar (`.env`, nomes das variáveis).
4. Como reconhecer expiração (downloads voltando a falhar com a mensagem que
   aponta esta doc) e como renovar (repetir a captura; reiniciar o processo).
