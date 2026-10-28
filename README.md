# Job Fit Screener — triagem de vagas com IA

Pipeline em Python que **coleta ~3.500 vagas por rodada em 18 fontes**, pede a
um modelo de decisão (Jev, da TypeSafe AI) uma nota de aderência de cada vaga
ao meu currículo e às minhas preferências, e entrega um **ranking em Excel com
links clicáveis** — separando o que vale aplicar hoje do que não vale o tempo.

> *English summary: a Python pipeline that collects ~3,500 job postings per
> run from 18 sources (public APIs, ATS boards, RSS), scores each one against
> a CV with an LLM-based decision API, and outputs a ranked, clickable Excel
> report. Includes a labeled evaluation set used to raise ranking accuracy
> from 52% to 70%, a versioned score cache, and an offline test suite.*

## O problema

Procurar vaga de Revenue Operations / Business Analyst / E-commerce significa
abrir dezenas de sites, ler centenas de descrições e decidir, uma a uma, se
vale a candidatura. A maior parte do tempo vai em vaga que não serve — vendedor
com meta disfarçado de "Key Account", analista júnior, vaga só para quem mora
nos EUA.

## O que o projeto faz

```
 18 fontes de vagas ──► coleta + deduplicação ──► filtros baratos ──► Jev (IA) ──► regras ──► Excel ranqueado
 (APIs, ATS, RSS)        ~3.500 vagas/rodada     título, local,      nota por     ajustes      aplicar_agora /
                                                 senioridade         vaga         determinís-  avaliar / não
                                                                                  ticos        aplicar
```

1. **Coleta** vagas de 18 fontes: Gupy (via o MCP oficial para candidatos), Workable,
   Greenhouse, InHire, Lever, Ashby, Workday, SmartRecruiters, Teamtailor,
   Amazon, Shopee, TikTok, 99/DiDi, Remotive, We Work Remotely, Remote OK, Remote.io e
   beBee — ~115 empresas acompanhadas individualmente, mais a busca por termo.
2. **Filtra antes de gastar IA**: palavras-chave de título (com exclusões como
   "estágio", "loja", "engenheiro"), e — nas vagas remotas internacionais —
   só as que aceitam candidatos no Brasil/LATAM. Isso cortou ~48% das chamadas
   à API sem perder nenhuma vaga "aplicar agora". Vaga presencial ou híbrida
   em outro país (os boards globais trazem Londres, Cidade do México...)
   também fica de fora (~225 por rodada).
3. **Pontua cada vaga** com o Jev, mandando currículo + vaga + preferências
   ("quero / não quero"). A nota final combina aderência (55%), senioridade
   (20%), ausência de gap crítico (15%) e confiança do modelo (10%).
4. **Aplica regras determinísticas** onde a IA erra de forma previsível (ex.:
   "analista júnior" nunca passa de "avaliar"; "especialista" é sempre
   compatível com a senioridade; se o próprio modelo aponta um requisito
   obrigatório não atendido, a vaga não fica em "aplicar agora") e registra o
   motivo numa coluna `ajuste`.
5. **Gera o relatório**: CSV + Excel com cores por recomendação, filtros,
   salário (quando a fonte informa) e link direto para a vaga.

## Decisões de engenharia que valem destacar

- **Avaliação com gabarito, não "achismo".** `calibrar.py` mede quantos pares
  de vagas o modelo ordena igual a um gabarito rotulado à mão. Foi assim que
  se provou que mandar a descrição completa + um bloco de preferências levava
  o acerto de **52% para 70%**, e que o peso de requisitos técnicos precisava
  cair.
- **Cache versionado.** Cada nota fica salva com um hash de
  (currículo + preferências + perguntas + tamanho do texto) e o modelo que deu
  a nota. Rodadas seguintes só pontuam vagas novas (~150 de ~3.500); se o
  currículo, as preferências ou o modelo mudam, tudo é repontuado
  automaticamente. O cache é gravado a cada 25 vagas, então uma queda de rede
  não perde trabalho. As descrições baixadas uma a uma (InHire, Shopee,
  Workday, beBee...) também ficam guardadas (`output/descricoes.json`), em vez
  de ~900 páginas rebaixadas por rodada.
- **Sem navegador automatizado.** Toda fonte usa API pública (documentada ou
  descoberta lendo o tráfego do próprio site), RSS ou HTML já renderizado no
  servidor — mais leve e menos frágil que Selenium/Playwright.
- **Respeito a termos de uso.** LinkedIn, Glassdoor e Indeed ficam de fora de
  propósito (proíbem scraping). Sites com bloqueio anti-bot (hiring.cafe,
  Mercado Livre) não são contornados. O Remote OK é sempre creditado com link,
  como pedem os termos da API dele.
- **Falha visível, não silenciosa.** Se uma fonte ligada volta vazia (site
  mudou ou bloqueou), o script avisa no terminal em vez de simplesmente sumir
  com as vagas dela. O mesmo vale para cada empresa: board com 0 vagas ou 404
  costuma ser empresa que trocou de sistema de vagas (o Nubank foi do
  Greenhouse pro Ashby), e o aviso já diz como procurar o novo.
- **Segurança sem atalho.** O servidor de vagas da DiDi não envia o
  certificado intermediário; em vez de desligar a verificação HTTPS (como o
  próprio site faz), o coletor completa a cadeia com o intermediário oficial
  da DigiCert.
- **Resiliência.** Retentativas com backoff para 429/5xx/timeout na API de IA e
  coleta em paralelo nas fontes lentas.
- **Testes offline.** `selftest.py` roda 25 testes sem rede nem chave: o
  parsing de cada fonte (com respostas reais gravadas), filtros, regras de
  ajuste, cache, o fluxo completo com um cliente de IA simulado e o painel
  com a sincronização (SQLite no lugar do Postgres).

## Radar: achando o que o script não vê

Um teste comparando com o LinkedIn mostrou que só ~15% das vagas de lá
estavam no pipeline — muitas empresas simplesmente não estavam na lista. O
comando `/radar` (um *skill* do [Claude Code](https://claude.com/claude-code),
em `.claude/skills/radar/`) resolve isso sob demanda:

1. faz buscas na web (páginas de ATS, listagens públicas do LinkedIn, busca
   aberta) com os cargos-alvo;
2. `radar_cruzar.py` separa o que é **novo** do que o script já tem (com a
   nota do Jev) e do que já apareceu antes — comparação tolerante a variações
   de nome de empresa e título;
3. confere se as vagas novas continuam abertas;
4. `descobrir_ats.py` descobre em qual sistema de vagas a empresa publica e, se
   for um que o script lê, a empresa entra no `config.yaml` — e passa a ser
   coletada automaticamente dali em diante.

## Painel de candidaturas

Achar a vaga é metade do trabalho; a outra é acompanhar onde já me candidatei.
Um painel na web (pasta `web/`, publicada na Vercel, com senha) tem duas abas:

- **Vagas de hoje**: as vagas "aplicar agora" e "avaliar" do relatório do dia,
  com busca, nota e um botão "Já enviei";
- **Candidaturas**: o funil enviada → entrevista → oferta / recusada /
  encerrada, com data de envio, dias desde então e anotações. Vagas de fora do
  relatório (LinkedIn, site da empresa) entram por "+ Adicionar candidatura".

```
  PC (rodada diária)                                Vercel
  screen.py ── relatório do dia                     painel + Postgres (Neon)
        │                                                 ▲   │
        └── sincronizar.py ──── sobe as vagas do dia ─────┘   │
                 ▲                                            │
                 └──── cópia de segurança das candidaturas ◄──┘
```

As candidaturas são registradas no painel, que é a fonte da verdade; o PC só
manda as vagas do dia e guarda uma cópia em `output/candidaturas.json`. O CV de
cada candidatura eu monto à parte, com o Claude, seguindo um documento de
regras (`regras-cv.md`, fora do git).

> Houve uma versão que gerava o CV em PDF pela API (`kit.py`, no histórico do
> git). Saiu por custo: ~US$ 0,26 por vaga, para um trabalho que o Claude já
> faz na assinatura.

## Como foi construído

Projeto pessoal, desenvolvido em par com o **Claude Code** (IA de
programação). Meu papel foi o de dono do produto: definir o problema e os
critérios de uma boa vaga, rotular o gabarito de avaliação, decidir as regras
(o que é "júnior", quando requisito técnico pesa, que fontes são aceitáveis) e
validar cada rodada contra a minha própria leitura das vagas.

## Como rodar

Pré-requisitos: Python 3.10+ e uma chave da API do Jev
([console.typesafe.ai](https://console.typesafe.ai/keys)). Sem chave, dá
para rodar tudo em modo simulado com `--mock`.

```bash
pip install -r requirements.txt
cp .env.example .env              # e cole a chave em TYPESAFE_API_KEY
cp cv.exemplo.txt cv.txt          # e cole o texto do seu currículo
```

Opcional: crie um `preferencias.txt` com o que você quer e não quer (o
exemplo está no `config.yaml`, bloco `preferencias`) e um `gabarito.csv` no
formato de `gabarito.exemplo.csv` para calibrar. **`cv.txt`,
`preferencias.txt`, `regras-cv.md`, `gabarito.csv`, `.env` e `output/` ficam
fora do git.**

```bash
python selftest.py                # testes offline, sem rede nem chave
python screen.py --limit 3        # rodada curta com a API real
python screen.py                  # rodada completa
python screen.py --collect-only   # só coleta, sem pontuar
python calibrar.py                # mede o acerto contra o gabarito
python sincronizar.py             # manda as vagas do dia pro painel (PAINEL_URL/TOKEN no .env)
```

O relatório sai em `output/vagas_triadas_<data>.xlsx` (e `.csv`).

### Colunas do relatório

| Coluna | O que é |
|---|---|
| `recomendacao` | `aplicar_agora` / `avaliar` / `baixa_prioridade` / `nao_aplicar` |
| `nota_fit` | 0 a 10 — aderência, senioridade, gap crítico e confiança, ponderados |
| `aderencia_nivel` | explicação em texto do nível de aderência |
| `senioridade_ok` / `gap_critico` | se o nível bate; se falta algum requisito obrigatório |
| `trilha` | em que frente de carreira a vaga se encaixa |
| `ajuste` | regra determinística que alterou a recomendação, se houve |
| `salario` | quando a fonte informa (Gupy, vagas remotas internacionais) |
| `titulo`, `empresa`, `cidade`, `fonte`, `publicado_em`, `link` | dados da vaga |

### Configuração

Tudo em `config.yaml`: termos de busca, palavras-chave e exclusões de título,
fontes ligadas/desligadas e a lista de empresas de cada ATS (cada uma com um
comentário de quando e por que entrou).

## Estrutura

```
screen.py              # pipeline: coleta, cache, pontuação, regras, relatório
jev_client.py          # cliente da API (com retentativas) + cliente simulado
exportar_excel.py      # Excel formatado com links clicáveis
calibrar.py            # acurácia do ranking contra um gabarito rotulado
radar_cruzar.py        # /radar: vagas achadas na web x vagas já coletadas
descobrir_ats.py       # /radar: descobre o sistema de vagas de uma empresa
web/                   # painel de candidaturas (Vercel): index.html + api/index.py (Flask + Postgres)
sincronizar.py         # PC -> painel: vagas do dia; painel -> PC: cópia das candidaturas
selftest.py            # 25 testes offline
collectors/            # um coletor por fonte + utilitários (filtros, HTML, local, cache de descrições)
.claude/skills/radar/  # instruções do comando /radar
config.yaml            # termos, filtros, fontes e empresas
```

## Limitações conhecidas

- APIs não documentadas (InHire, Shopee, TikTok, DiDi, Workable, Amazon, Workday) podem mudar
  sem aviso; cada coletor tem comentários explicando como foi descoberto e
  como redescobrir.
- beBee é leitura de HTML e já ficou fora do ar por alguns dias; o script
  avisa quando isso acontece. A busca dele só traz os primeiros ~300
  caracteres de cada vaga — o Jev chegou a dar 9,3 a uma vaga que, no texto
  inteiro, exigia SQL avançado. Desde 28/10/2026 o script lê a página completa
  de cada vaga do beBee uma vez e guarda o texto (a 1ª rodada é a lenta).
- O Remote.io virou app em JavaScript em 28/10/2026; o coletor passou a ler o
  sitemap de vagas ativas e abrir a página de cada candidata. Como ~1.900
  vagas passam pelo título, abre até 300 novas por rodada (as mais recentes) —
  as primeiras rodadas ainda não cobrem tudo.
- Empresas cujas vagas não estão num sistema que o script lê só aparecem via
  `/radar` — ex.: Americanas (vagas corporativas só no LinkedIn).
- A nota da IA é uma triagem, não uma decisão: as primeiras rodadas foram
  conferidas manualmente e o gabarito continua sendo a referência.

## Rodar todo dia e receber aviso das vagas novas

1. Clique duas vezes em `instalar_agendamento.bat`. Ele cria a tarefa "Radar de Vagas" no Agendador de Tarefas do Windows, todo dia às 07:00 (outro horário: `instalar_agendamento.bat 08:30`). Se o PC estiver desligado nessa hora, roda quando ligar.
2. Cada rodada salva o log em `output/run_diario.log` e, além do relatório completo, um `output/novas_<data>.csv` só com as vagas que nunca tinham aparecido antes (nota >= 7.5, "aplicar_agora" ou "avaliar"; ajustável no bloco `avisos` do `config.yaml`).
3. Aviso no celular (opcional): instale o app ntfy, assine um tópico com nome difícil de adivinhar e coloque esse nome em `NTFY_TOPIC` no `.env` (ou `setx NTFY_TOPIC nome-do-topico`). Detalhes em `novidades.py`.
