import html as _html
import re
import threading
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str, max_len: int = 12000) -> str:
    # desfaz o escape antes de remover as tags: a Greenhouse devolve o HTML
    # escapado ("&lt;p&gt;"), que antes passava direto como lixo pro Jev.
    text = _html.unescape(text or "")
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)  # entidades que estavam dentro do HTML (&amp;nbsp; etc.)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _fold(text: str) -> str:
    """minúsculas e sem acento, pra "Negócios" bater com "negocios"."""
    sem_acento = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def title_matches(titulo: str, keywords) -> bool:
    """True se o título contém algum dos trechos em `keywords` (string única ou
    lista), ignorando maiúsculas e acentos. Sem keywords, tudo passa.
    `keywords` também pode ser uma função titulo -> bool (ver title_filter)."""
    if callable(keywords):
        return keywords(titulo)
    if not keywords:
        return True
    if isinstance(keywords, str):
        keywords = [keywords]
    t = _fold(titulo)
    return any(_fold(k) in t for k in keywords)


def title_filter(keywords, excludes):
    """Função titulo -> bool: passa se bater com `keywords` e NÃO bater com
    nenhum trecho de `excludes`. Pode ser passada como term_filter pros coletores."""
    def ok(titulo: str) -> bool:
        return title_matches(titulo, keywords) and not (excludes and title_matches(titulo, excludes))
    return ok


# Vagas remotas internacionais: só servem se aceitam quem mora no Brasil.
# Forte: o local cita Brasil/LATAM/Américas. Fraco: "anywhere/worldwide" — vale
# só se o local não restringir a país/região ("Anywhere in the United States").
_LOCAL_FORTE = re.compile(r"\b(brazil|brasil|latam|latin america|america latina|south america|americas)\b")
_LOCAL_FRACO = re.compile(r"\b(worldwide|anywhere|global|world)\b")
_RESTRICAO = re.compile(
    r"\b(united states|usa|us|u\.s|canada|uk|united kingdom|europe|emea|apac|asia|india|"
    r"germany|france|spain|australia|north america|cet|est|pst|timezone)\b"
)
_TEXTO_ACEITA_BR = re.compile(
    r"\b(brazil|brasil|latam|latin america|america latina|south america|"
    r"anywhere in the world|remote worldwide|work from anywhere in the world|globally remote)\b"
)


def aceita_brasil(local: str, descricao: str = "", anywhere_confiavel: bool = True) -> bool:
    """True se a vaga remota aceita candidato no Brasil: o local cita Brasil,
    LATAM ou Américas, ou diz "anywhere/worldwide" sem restringir a país.
    "USA", "Europe", "Argentina, Mexico" etc. ficam de fora. Local vazio, só
    "Remote" — ou "Anywhere" em site onde isso não é confiável
    (anywhere_confiavel=False) — só passa se a descrição citar Brasil/LATAM/
    "anywhere in the world"."""
    loc = _fold(local).strip()
    if _LOCAL_FORTE.search(loc):
        return True
    if anywhere_confiavel and _LOCAL_FRACO.search(loc) and not _RESTRICAO.search(loc):
        return True
    ambiguo = loc in ("", "remote", "remoto", "fully remote", "100% remote") or (
        not anywhere_confiavel and loc in ("anywhere", "worldwide")
    )
    if ambiguo:
        return bool(_TEXTO_ACEITA_BR.search(_fold(descricao)))
    return False


# Fontes "por empresa" trazem o board global inteiro (Melbourne na AB InBev,
# Lisboa na Ebury, Cidade do México no QuintoAndar). Regra do Michel
# (28/10/2026): só Brasil, ou remoto que aceita Brasil — vaga presencial/híbrida
# em outro país não vai pro Jev. Empresa europeia continua acompanhada pelas
# vagas dela no Brasil. Conservador: só descarta quando o local cita um lugar
# de fora e nenhum do Brasil; local vazio ou só "Remote" passa.
_LOCAL_BR = re.compile(
    r"\b(brasil|brazil|br|bra|sao paulo|sp|rio de janeiro|rj|belo horizonte|curitiba|porto alegre|"
    r"florianopolis|recife|salvador|fortaleza|brasilia|campinas|barueri|osasco|alphaville|"
    r"blumenau|joinville|goiania|manaus|belem|vitoria|ribeirao preto|sorocaba|santos|"
    r"sao jose|uberlandia|londrina|maringa|natal|joao pessoa|cuiaba|campo grande|nova lima|"
    r"minas gerais|parana|santa catarina|rio grande do sul|bahia|pernambuco|ceara|goias|"
    r"distrito federal|espirito santo|amazonas|para|mato grosso)\b"
)
_LOCAL_FORA = re.compile(
    r"\b(united states|usa|us|u\.s\.|canada|toronto|vancouver|montreal|new york|nyc|san francisco|"
    r"seattle|austin|boston|chicago|los angeles|denver|atlanta|miami|dallas|houston|washington|"
    r"mexico|cdmx|guadalajara|monterrey|colombia|bogota|medellin|argentina|buenos aires|chile|"
    r"santiago de chile|peru|lima|uruguay|montevideo|ecuador|costa rica|"
    r"united kingdom|uk|england|london|ireland|dublin|portugal|lisbon|lisboa|spain|espana|madrid|"
    r"barcelona|malaga|germany|berlin|munich|hamburg|france|paris|netherlands|amsterdam|belgium|"
    r"brussels|switzerland|zurich|zug|geneva|italy|milan|rome|poland|warsaw|krakow|sweden|stockholm|"
    r"denmark|copenhagen|norway|oslo|finland|helsinki|austria|vienna|czech|prague|romania|"
    r"bucharest|greece|athens|turkey|istanbul|israel|tel aviv|"
    r"india|bangalore|bengaluru|hyderabad|mumbai|delhi|pune|singapore|australia|sydney|melbourne|"
    r"new zealand|japan|tokyo|china|shanghai|beijing|hong kong|taiwan|korea|seoul|philippines|"
    r"manila|indonesia|jakarta|vietnam|thailand|bangkok|malaysia|kuala lumpur|uae|dubai|"
    r"saudi arabia|riyadh|south africa|cape town|nigeria|lagos|kenya|nairobi|egypt|cairo)\b"
)


def fora_do_brasil(local: str, remoto: bool = False, descricao: str = "") -> bool:
    """True se a vaga é claramente de fora do Brasil (ver _LOCAL_FORA)."""
    loc = _fold(local)
    if not loc.strip() or _LOCAL_BR.search(loc) or not _LOCAL_FORA.search(loc):
        return False
    # remoto com local de fora ("Mexico City / Remote") fica se o local ou a
    # descrição citar Brasil/LATAM
    return not (remoto and (aceita_brasil(local) or _TEXTO_ACEITA_BR.search(_fold(descricao))))


# Vagas publicadas por cada board/empresa nesta rodada, ANTES do filtro de
# título. Board que zera quase sempre é empresa que trocou de sistema de vagas
# (28/10/2026: o Nubank foi do Greenhouse pro Ashby e a Creditas pra Gupy, e as
# duas sumiram do relatório sem aviso) — o screen.py lista os zerados no fim da
# coleta. (fonte, board) -> "" se tem vaga, ou o motivo ("0 vagas", "404").
_BOARDS: dict[tuple[str, str], str] = {}
_BOARDS_LOCK = threading.Lock()


def registrar_board(fonte: str, board: str, total: int, erro: str = "") -> None:
    with _BOARDS_LOCK:
        _BOARDS[(fonte, board)] = erro or ("" if total else "0 vagas publicadas")


def boards_zerados() -> list[tuple[str, str, str]]:
    with _BOARDS_LOCK:
        return [(f, b, motivo) for (f, b), motivo in _BOARDS.items() if motivo]


def limpar_boards() -> None:
    with _BOARDS_LOCK:
        _BOARDS.clear()
