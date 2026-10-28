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
   à API sem perder nenhuma vaga "aplicar agora".
3. **Pontua cada vaga** com o Jev, mandando currículo + vaga + preferências
   ("quero / não quero"). A nota final combina aderência (55%), senioridade
   (20%), ausência de gap crítico (15%) e confiança do modelo (10%).
4. **Aplica regras determinísticas** onde a IA erra de forma previsível (ex.:
   "analista júnior" nunca passa de "avaliar"; "especialista" é sempre
   compatível com a senioridade) e registra o motivo numa coluna `ajuste`.
5. **Gera o relatório**: CSV + Excel com cores por recomendação, filtros,
   salário (quando a fonte informa) e link direto para a vaga.

## Decisões de engenharia que valem destacar

- **Avaliação com gabarito, não "achismo".** `calibrar.py` mede quantos pares
  de vagas o modelo ordena igual a um gabarito rotulado à mão. Foi assim que
  se provou que mandar a descrição completa + um bloco de preferências levava
  o acerto de **52% para 70%**, e que o peso de requisitos técnicos precisava
  cair.
- **Cache versionado.** Cada nota fica salva com um hash de
  (currículo + preferências + perguntas + tamanho do texto). Rodadas seguintes
  só pontuam vagas novas (~1.100 de ~3.500); se o currículo ou as
  preferências mudam, o hash muda e tudo é repontuado automaticamente. O
  cache é gravado a cada 25 vagas, então uma queda de rede não perde trabalho.
- **Sem navegador automatizado.** Toda fonte usa API pública (documentada ou
  descoberta lendo o tráfego do próprio site), RSS ou HTML já renderizado no
  servidor — mais leve e menos frágil que Selenium/Playwright.
- **Respeito a termos de uso.** LinkedIn, Glassdoor e Indeed ficam de fora de
  propósito (proíbem scraping). Sites com bloqueio anti-bot (hiring.cafe,
  Mercado Livre) não são contornados. O Remote OK é sempre creditado com link,
  como pedem os termos da API dele.
- **Falha visível, não silenciosa.** Se uma fonte ligada volta vazia (site
  mudou ou bloqueou), o script avisa no terminal em vez de simplesmente sumir
  com as vagas dela.
- **Segurança sem atalho.** O servidor de vagas da DiDi não envia o
  certificado intermediário; em vez de desligar a verificação HTTPS (como o
  próprio site faz), o coletor completa a cadeia com o intermediário oficial
  da DigiCert.
- **Resiliência.** Retentativas com backoff para 429/5xx/timeout na API de IA e
  coleta em paralelo nas fontes lentas.
- **Testes offline.** `selftest.py` roda 31 testes sem rede nem chave: o
  parsing de cada fonte (com respostas reais gravadas), filtros, regras de
  ajuste, cache, o fluxo completo com um cliente de IA simulado e o kit de
  candidatura, o painel e a sincronização com a nuvem (com um Word simulado
  e SQLite no lugar do Postgres).

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

## Kit de candidatura: da vaga ao envio

Achar a vaga é metade do trabalho; a outra metade é adaptar o CV e preencher o
formulário. `kit.py` monta, para as vagas que eu escolho no relatório:

- **CV adaptado em PDF**, no mesmo template dos meus CVs e com exatamente 2
  páginas: mesmas experiências, cargos e datas do currículo-mestre, com os
  bullets escolhidos, reordenados e reescritos no vocabulário da vaga (em
  inglês quando a vaga é em inglês). O PDF vai direto para a pasta de CVs, com
  o nome no padrão `CV_Michel_<Vaga><Empresa>.pdf`;
- **carta curta** e **respostas prontas** para as perguntas típicas do formulário.

Como funciona:

1. **Checagem antes de gastar.** Uma chamada barata lista os requisitos
   *obrigatórios* que eu não atendo (diferencial não conta). Se houver algum,
   o script pergunta "montar mesmo assim?"; se não, a vaga é pulada e registrada.
2. **Regras escritas, não implícitas.** O `regras-cv.md` (fora do git) junta
   tudo o que defini ao longo das candidaturas: formato, ordem das
   experiências, banco de resultados, quando usar a variante para instituições
   financeiras e o que eu *não* tenho e nunca pode aparecer.
3. **2 páginas por construção.** O modelo escreve bullets de sobra, do mais
   para o menos relevante; o script gera o .docx, pergunta ao Word quantas
   páginas deu e corta do fim até caber.
4. **Checagens determinísticas** em cima do texto do modelo: todo número citado
   (R$, %, anos, SKUs) precisa existir no `cv.txt` ou nas regras, e nenhum
   termo da lista de proibidos (ex.: Power BI, Python) pode aparecer no CV. O
   que falhar vai para um checklist "antes de enviar". Dados que o CV não tem
   (pretensão salarial, data de início) saem como `[marcador]`, nunca chutados.
5. **Registro.** Cada vaga entra em `output/kits/registro.csv`: lacunas,
   decisão (montada / pulada), variante e arquivo.

O texto vem do Claude (API da Anthropic) com saída em JSON estruturado; o
documento é montado pelo script, e nome e contatos nunca passam pelo modelo. O
envio continua manual, de propósito: candidatura automática em massa exige
automatizar navegador e contornar CAPTCHA, e é justamente o que o projeto evita.

## Painel: decidir, revisar e acompanhar

Uma página com quatro abas:

- **Para decidir**: vagas em que falta requisito obrigatório, com "Montar
  mesmo assim" / "Pular essa vaga". Quando o `kit.py` roda sem ninguém no
  terminal, a vaga cai aqui em vez de ser pulada;
- **Prontas para enviar**: o PDF na tela, carta e respostas com botão de
  copiar, e o checklist do que preencher ou conferir antes de enviar;
- **Candidaturas**: o funil enviada → entrevista → oferta / recusada, com data
  de envio, dias desde então e anotações;
- **Vagas de hoje**: o relatório com busca e "Gerar kit".

A mesma página roda em dois lugares:

```
  PC (como antes)                                   Vercel (de qualquer lugar)
  screen.py, kit.py, Word, OneDrive                 painel + Postgres (Neon), com senha
        │                                                 ▲   │
        └── sincronizar.py ──── sobe vagas, kits, PDFs ───┘   │
                 ▲                                            │
                 └──── baixa pedidos ("gerar", "montar") e ◄──┘
                       mudanças de status / anotações
```

- **Local** (`python painel.py` ou `abrir_painel.bat`): gera os kits na hora.
- **Na nuvem** (pasta `web/`, publicada na Vercel): status, anotações e
  histórico ficam num banco e dá para ver e atualizar do celular. O trabalho
  pesado continua no PC — Word, pasta do OneDrive, chave da API — então
  "Gerar kit" pela nuvem vira um pedido que o `sincronizar.py` atende quando o
  PC está ligado (`instalar_sincronizacao.bat` o deixa rodando escondido).
  Conflitos se resolvem pela versão mais nova de cada candidatura, e o PC
  nunca sobrescreve uma mudança mais recente feita na nuvem.

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
formato de `gabarito.exemplo.csv` para calibrar. Para o `kit.py`, um
`regras-cv.md` com as suas regras de CV (formato, fatos, o que nunca colocar).
**`cv.txt`, `preferencias.txt`, `regras-cv.md`, `gabarito.csv`, `.env` e
`output/` ficam fora do git.**

```bash
python selftest.py                # testes offline, sem rede nem chave
python screen.py --limit 3        # rodada curta com a API real
python screen.py                  # rodada completa
python screen.py --collect-only   # só coleta, sem pontuar
python calibrar.py                # mede o acerto contra o gabarito
python kit.py                     # lista as vagas "aplicar agora" do último relatório
python kit.py 3 8                 # kit de candidatura das vagas 3 e 8 (ANTHROPIC_API_KEY + Word)
python painel.py                  # painel no navegador: decidir, revisar, acompanhar
python sincronizar.py             # sobe/baixa o que mudou no painel na nuvem
```

O relatório sai em `output/vagas_triadas_<data>.xlsx` (e `.csv`); os kits, em
`output/kits/<empresa>_<vaga>/`.

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
kit.py                 # CV adaptado em PDF + carta + respostas para uma vaga
painel.py              # painel local no navegador (Flask)
web/                   # painel na nuvem (Vercel): index.html (a página, usada também
                       #   pelo painel local) e api/index.py (Flask + Postgres)
sincronizar.py         # PC <-> nuvem: sobe vagas/kits/PDFs, baixa pedidos e status
candidaturas.py        # estado de cada vaga: para decidir, pronta, enviada...
selftest.py            # 31 testes offline
collectors/            # um coletor por fonte + utilitários (filtros, HTML, local)
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
  das vagas do beBee ainda sem nota (só as novas, então a 1ª rodada é a lenta).
- Empresas cujas vagas não estão num sistema que o script lê só aparecem via
  `/radar` — ex.: Americanas (vagas corporativas só no LinkedIn).
- A nota da IA é uma triagem, não uma decisão: as primeiras rodadas foram
  conferidas manualmente e o gabarito continua sendo a referência.

## Rodar todo dia e receber aviso das vagas novas

1. Clique duas vezes em `instalar_agendamento.bat`. Ele cria a tarefa "Radar de Vagas" no Agendador de Tarefas do Windows, todo dia às 07:00 (outro horário: `instalar_agendamento.bat 08:30`). Se o PC estiver desligado nessa hora, roda quando ligar.
2. Cada rodada salva o log em `output/run_diario.log` e, além do relatório completo, um `output/novas_<data>.csv` só com as vagas que nunca tinham aparecido antes (nota >= 7.5, "aplicar_agora" ou "avaliar"; ajustável no bloco `avisos` do `config.yaml`).
3. Aviso no celular (opcional): instale o app ntfy, assine um tópico com nome difícil de adivinhar e coloque esse nome em `NTFY_TOPIC` no `.env` (ou `setx NTFY_TOPIC nome-do-topico`). Detalhes em `novidades.py`.
