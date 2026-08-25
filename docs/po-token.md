# po_token do YouTube

Guia do operador para instalar e renovar o *proof-of-origin token* usado nos
downloads de background. Configuração geral do proxy do YouTube:
[configuration.md](./configuration.md#youtube-youtube_config).

## O que o token resolve — e o que não resolve

O YouTube exige, dos clientes que usamos para baixar (`WEB`, `MWEB`,
`WEB_SAFARI`), uma prova de que a requisição vem de um navegador de verdade: o
**po_token**, sempre acompanhado do **visitorData** da sessão que o gerou.

Sem configuração nenhuma, o pytubefix fabrica esse token sozinho (via botGuard).
Ele funciona, mas não carrega uma sessão real — é justamente o token sintético
que o YouTube passou a responder com `HTTP 429` quando o mesmo IP baixa muitos
clipes por dia. Um token capturado do seu navegador tem precedência sobre o
sintético e costuma passar onde ele é recusado.

O que o token **não** faz:

- **Não desbloqueia um IP já throttled.** Se o endereço está em 429, o token não
  o tira de lá; o que salva a execução nesse estado é o
  [cache de backgrounds](./configuration.md#background-cache-youtube_configcache),
  que serve os clipes já baixados sem tocar na rede.
- **Não ajuda em cache frio com IP bloqueado.** O primeiro download de um clipe
  novo continua dependendo de o YouTube aceitar a requisição.
- **Não é permanente.** O token expira; renovar é um passo manual (abaixo).

Ou seja: o cache cobre o dia a dia, e o token existe para o caso residual — o
*miss* que precisa mesmo ir à rede.

## Como obter o par

1. Abra o YouTube no navegador, de preferência em uma **janela anônima e sem
   login** — o token fica atrelado à sessão que o gerou, e uma sessão anônima
   evita amarrar sua conta ao pipeline.
2. Abra o DevTools (`F12` ou `Cmd+Option+I`) e vá para a aba **Network**.
3. Reproduza um vídeo qualquer. Se preferir, filtre por `player` na busca de
   requisições.
4. Localize a requisição **`v1/player`** (método `POST`) e abra o corpo enviado
   (**Payload** / **Request**).
5. Copie os dois valores:
   - `serviceIntegrityDimensions.poToken` → é o **po_token**;
   - `context.client.visitorData` → é o **visitor_data**.

Os dois vêm da mesma requisição de propósito: o YouTube só aceita o token junto
com o visitorData para o qual ele foi emitido. Misturar valores de capturas
diferentes não funciona.

## Onde instalar

No arquivo `.env` da raiz do projeto (não versionado — o `.gitignore` já o
cobre; nunca coloque esses valores no `config.yaml`):

```dotenv
YOUTUBE_PO_TOKEN=cole_aqui_o_po_token
YOUTUBE_VISITOR_DATA=cole_aqui_o_visitor_data
```

Regras que valem a pena saber antes de rodar:

- **Os dois ou nenhum.** Preencher só um dos campos derruba a inicialização com
  uma mensagem apontando para cá — de propósito: um par pela metade é engano de
  configuração, e descobrir isso só no primeiro download desperdiçaria a rodada.
- **Vazio conta como ausente.** Deixar os valores em branco (ou só com espaços)
  é o mesmo que não configurar: os downloads voltam a se comportar exatamente
  como antes, com o token sintético do pytubefix.
- **Reinicie o processo depois de editar o `.env`.** Os valores são lidos na
  inicialização. Não é preciso mexer em nada dentro do `.venv`: quando há token
  configurado, o cache interno de tokens do pytubefix é limpo na subida, então o
  valor do `.env` é sempre a única fonte de verdade e um token renovado nunca
  fica sombreado pelo antigo.

## Como reconhecer a expiração e renovar

O token expira em silêncio: o YouTube recusa um token vencido do mesmo jeito que
recusa a ausência de token. Na prática, a expiração aparece como downloads
voltando a falhar (`429`/`403`) depois de um período funcionando.

Para não deixar dúvida, com token configurado toda falha de download traz um
complemento na mensagem:

```text
Failed to download video <id>: ... A po_token is configured, so it may have
expired — YouTube refuses an expired token the same way it refuses none; see
docs/po-token.md to renew it.
```

Renovar é repetir a captura: nova janela anônima, nova requisição `v1/player`,
novos `poToken` e `visitorData`, **os dois** atualizados no `.env`, e o processo
reiniciado. Não adianta atualizar só o token e manter o visitorData antigo.

Se, mesmo com um par recém-capturado, os downloads continuarem em `429`, o
problema não é o token: o IP está throttled. Nesse caso a saída é esperar o
bloqueio passar — cada nova tentativa o prolonga — enquanto o cache de
backgrounds mantém as execuções rodando com os clipes já baixados.
