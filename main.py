# Este script posta updates da Câmara dos Deputados em algum subreddit.
#
# O principal conceito é a atualização. Uma atualização é identificada por três
# coisas:
#
# 1. Tipo de proposição
# 2. Número da proposição
# 3. Ano da proposição
# 4. Número de sequência
#
# Por exemplo, a proposição PL 1234/2019 tem três atualizações: PL 1234/2019-1,
# PL 1234/2019-2 e PL 1234/2019-3. Uma atualização pode estar postada, neste
# caso ela tem status POSTADA. no subreddit. Para verificar a última atualização
# postada, o script consulta a API do Reddit. Após verificar a última
# atualização postada, o script consulta a API da Câmara dos Deputados e baixa
# as atualizações mais recentes até encontrar a última atualização postada no
# subreddit. Estas atualizações têm status NÃO_POSTADA. O script também tem uma
# função de detectar e remover duplicatas.
#
# O script precisa de um arquivo .env com as seguintes variáveis:
#
# REDDIT_CLIENT_ID='xyz'
#
# REDDIT_CLIENT_SECRET='xyz'
#
# REDDIT_USER_AGENT='praw'
#
# REDDIT_USERNAME='xyz'
#
# REDDIT_PASSWORD='xyz'
#
# SUBREDDIT='xyz'

### OLD CODE

import logging
import os
import praw
import schedule
import time

RAIZ_DO_PROJETO = os.path.abspath(os.path.dirname(__file__))

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s: %(message)s", datefmt="%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def get_com_backoff(url, headers, params=None, backoff=0.5):
    resposta = requests.get(url, headers=headers, params=params)
    while resposta.status_code == 429:
        logger.warning(f"rate limit atingido (429), esperando {backoff} segundos...")
        time.sleep(backoff)
        backoff *= 2
        resposta = requests.get(url, headers=headers, params=params)

    return resposta


def caminho_absoluto(arquivo):
    """Retorna o caminho absoluto a partir da raiz do projeto"""
    return os.path.join(RAIZ_DO_PROJETO, arquivo)


def get_env(var, default=None):
    got = os.getenv(var, default)
    if got is None and default is None:
        logger.error(f'variável de ambiente "{var}" deve ser definida')
        exit(1)
    elif got is None:
        return default
    else:
        return got


def baixar_autor_principal_e_seu_partido(id):
    logger.debug(f"baixando autores da proposição {id}")
    autores = get_com_backoff(
        f"{URL_DA_API}/proposicoes/{id}/autores",
        headers={"Content-Type": "application/json"},
    ).json()["dados"]

    # a primeira assinatura é o autor principal
    autores.sort(key=lambda x: x["ordemAssinatura"], reverse=True)
    principal = autores[0]

    if principal["tipo"] == "Deputado":
        logger.debug(f"baixando partido de {principal['nome']}")
        resposta = get_com_backoff(
            principal["uri"],
            headers={"Content-Type": "application/json"},
        ).json()

        try:
            detalhes = resposta["dados"]["ultimoStatus"]
            nome = detalhes["nomeEleitoral"]
            partido = detalhes["siglaPartido"]
        except:
            nome = principal["nome"]
            partido = None
    else:
        # se não for "Deputado", vai ser "Senado Federal",
        # "Poder Executivo", etc.
        nome = principal["nome"]
        partido = None

    return nome, partido


def tramitacao_nao_selecionada(tramitacao):
    return tramitacao["descricaoTramitacao"] not in TRAMITACOES_SELECIONADAS


def cortar(texto, maximo_de_caracteres):
    if len(texto) > maximo_de_caracteres:
        texto = texto[: maximo_de_caracteres - 3] + "..."
    return texto


### NEW CODE

import argparse
import logging
import datetime
import pendulum
import json
import tabulate
import re

import requests
import requests_cache

LISTAR_HELP = """Lista atualizações de um dia ou intervalo de dias."""
POSTAR_HELP = """Posta atualizações de um dia ou intervalo de dias."""
CRON_HELP = """Rodar cron que posta atualizações."""
DELETAR_HELP = (
    """Deleta as postagens das atualizações de um dia ou intervalo de dias."""
)
COMANDO_HELP = f"""Comando a ser executado.

cron: {CRON_HELP}
deletar: {DELETAR_HELP}
postar: {POSTAR_HELP}
listar: {LISTAR_HELP}"""
DIAS_HELP = """Dias, no format YYYY-MM-DD ou YYYY-MM-DD:YYYY-MM-DD, para listar
            atualizações. Se informado só uma data, lista atualizações de hoje
            até aquele dia (incluso). Se informado um intervalo, lista
            atualizações do intervalo. Se nada for informado, lista atualizações
            de hoje. Os dias são relativos à atualização na Câmara, não à data
            em que o post foi feito no Reddit. É assumido que o post é feito
            um dia depois da atualização."""
FONTES_HELP = """Fontes de onde baixar os dados. Se nada for informado, baixa
            dados de todas as fontes."""
SOMENTE_FLAGGED_HELP = """Se informado, lista apenas atualizações que foram
            marcadas com flags."""

if get_env("DEVELOPMENT", False):
    requests_cache.install_cache(
        "http_cache",
        backend="sqlite",
        expire_after=-1,
        allowable_methods=("GET",),
    )

SUBREDDIT = get_env("SUBREDDIT")

URL_DA_API = "https://dadosabertos.camara.leg.br/api/v2"
with open(caminho_absoluto("tramitacoes-selecionadas.txt")) as f:
    TRAMITACOES_SELECIONADAS = [l.strip() for l in f.readlines() if l.strip()]


cliente_do_reddit = praw.Reddit(
    **{
        "client_id": get_env("REDDIT_CLIENT_ID"),
        "client_secret": get_env("REDDIT_CLIENT_SECRET"),
        "user_agent": get_env("REDDIT_USER_AGENT"),
        "username": get_env("REDDIT_USERNAME"),
        "password": get_env("REDDIT_PASSWORD"),
    }
)


class Atualizacao(object):
    _fields = [
        "id",
        "tipo",
        "numero",
        "ano",
        "sequencia",
        "autor",
        "partido",
        "ementa",
        "despacho",
        "tipo_de_tramitacao",
        "url_da_atualizacao",
        "datahora_da_atualizacao",
        "datahora_do_post",
        "url_do_post",
        "ups",
        "downs",
        "num_comentarios",
        "flag_related",
        "flagged",
    ]

    def __init__(self, *args, **kwargs):

        for k in self._fields:
            setattr(self, k, None)

        for k in kwargs:
            if k not in self._fields:
                raise ValueError(f"campo inválido: {k}")

        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"{self.__class__.__name__}({', '.join(f'{k}={v!r}' for k, v in self._asdict().items())})"

    def _asdict(self):
        return {k: getattr(self, k) for k in self._fields}

    def __eq__(self, other):
        return self._asdict() == other._asdict()

    def __hash__(self):
        return hash(tuple(self._asdict().values()))


def imprimir_atualizacoes(atualizacoes, curto=True):
    if curto:
        campos = [
            "flagged",
            "id",
            "autor",
            "url_do_post",
            "datahora_da_atualizacao",
            "datahora_do_post",
            "flag_related",
            "ups",
        ]
    else:
        campos = Atualizacao._fields

    def formata(valor, campo):
        if isinstance(valor, datetime.datetime):
            return pendulum.instance(valor).format("DD/MM/YYYY HH:mm")
        if campo == "flagged":
            return "🚩" if valor else ""
        if campo == "flag_related":
            return ", ".join([f"{p.id} ({p.url_do_post})" for p in valor])
        return valor

    ordenado_por_data_da_atualizacao = sorted(
        atualizacoes, key=lambda a: a.datahora_da_atualizacao
    )

    print(
        tabulate.tabulate(
            [
                [formata(getattr(a, c), c) for c in campos]
                for a in ordenado_por_data_da_atualizacao
            ],
            headers=campos,
            showindex=True,
        )
    )
    print(f"total: {len(atualizacoes)}")
    print(f"postadas: {len([a for a in atualizacoes if a.url_do_post])}")
    print(f"flagged: {len([a for a in atualizacoes if a.flagged])}")


def inferir_atualizacao_do_post(post):
    # Título do post pode ter várias variações:
    #
    # [Dr. Leonardo - REPUBLICANOS] PL 2655/2022: ...
    #
    # [Poder Executivo] PL 2655/2022: ...
    #
    # [Senado Federal] PL 2655/2022: ...
    #
    # [Dr. Leonardo - REPUBLICANOS] PL 2655/2022 (1): ...
    #
    # [Dr. Leonardo - REPUBLICANOS] PL 2655/2022 (2): ...
    #
    # Exemplo completo:
    #
    # E.g., [Dr. Leonardo - REPUBLICANOS] PL 2655/2022: Define os critérios para a não incidência de imposto de renda sobre verbas destinadas a custear despesas necessárias ao exercício de mandato eletivo nos Poderes Legislativos federal, estadual ou municipal, e remite os créditos tributários e anistia os r...
    title = post.title
    shortlink = post.shortlink
    posted_url = post.url
    ups = post.ups
    downs = post.downs
    datahora_do_post = pendulum.from_timestamp(post.created_utc)
    num_comentarios = post.num_comments

    # Tenta dar match no título do post
    regexes = [
        r"\[(?P<autor>.*) - (?P<partido>) \] (?P<tipo>[A-Z]*) (?P<numero>[0-9]*)/(?P<ano>[0-9]*)( \((?P<sequencia>[0-9]*)\))?: (?P<ementa>.*)",
        r"\[(?P<autor>.*)- (?P<partido>) \] (?P<tipo>[A-Z]*) (?P<numero>[0-9]*)/(?P<ano>[0-9]*)( \((?P<sequencia>[0-9]*)\))?: (?P<ementa>.*)",
        r"\[(?P<autor>.*)\] (?P<tipo>[A-Z]*) (?P<numero>[0-9]*)/(?P<ano>[0-9]*)( \((?P<sequencia>[0-9]*)\))?: (?P<ementa>.*)",
    ]

    for regex in regexes:
        match = re.match(regex, title)
        if match:
            break

    if not match:
        logger.error(f"não conseguiu dar match no título do post: {title}")
        return None

    # Extrai dados do título do post
    autor = match.group("autor")
    try:
        partido = match.group("partido")
    except IndexError:
        partido = None
    tipo = match.group("tipo")
    numero = match.group("numero")
    ano = match.group("ano")
    sequencia = match.group("sequencia")
    ementa = match.group("ementa")
    tipo_de_tramitacao = post.link_flair_text

    datahora_da_atualizacao = None
    for comment in post.comments:
        if comment.author == get_env("REDDIT_USERNAME"):
            body = comment.body
            match = re.match(
                r"Despacho \((?P<datahora>[0-9]+/[0-9]+/[0-9]+)\)",
                body,
            )
            if match:
                parsed_by_datetime = datetime.datetime.strptime(
                    match.group("datahora"),
                    "%d/%m/%Y",
                )
                datahora_da_atualizacao = pendulum.instance(parsed_by_datetime)
                break
    logger.debug(f"datahora_da_atualizacao: {datahora_da_atualizacao}")

    # Constrói atualização
    atualizacao = Atualizacao(
        id=f"{tipo} {numero}/{ano} ({sequencia})",
        tipo=tipo,
        numero=numero,
        ano=ano,
        sequencia=sequencia,
        autor=autor,
        partido=partido,
        ementa=ementa,
        despacho=None,
        tipo_de_tramitacao=tipo_de_tramitacao,
        url_da_atualizacao=posted_url,
        url_do_post=shortlink,
        datahora_da_atualizacao=datahora_da_atualizacao,
        datahora_do_post=datahora_do_post,
        ups=ups,
        downs=downs,
        num_comentarios=num_comentarios,
        flagged=False,
        flag_related=[],
    )

    return atualizacao


def buscar_atualizacoes_postadas_no_reddit(data_inicio, data_fim):
    data_inicio = data_inicio
    data_fim = data_fim
    logger.info(
        f"buscando atualizações postadas no reddit entre {data_inicio} e {data_fim}"
    )
    posts = []
    atualizacoes = []

    for i, post in enumerate(cliente_do_reddit.subreddit(SUBREDDIT).new(limit=None)):
        logger.debug(
            f"date={pendulum.from_timestamp(post.created_utc)}, data_fim={data_fim}, post {i}: {post.id}, link_to_reddit={post.shortlink}"
        )
        logger.debug(
            f"timestamps: post {post.created_utc}, data_fim {data_fim.timestamp()}"
        )

        atualizacao = inferir_atualizacao_do_post(post)
        if not atualizacao:
            logger.info(f"post {post.id}, url={post.url} não é uma atualização")
            continue

        if not atualizacao.datahora_da_atualizacao:
            logger.error(f"post {post.id}, url={post.url} não tem data de atualização")
            atualizacao.flagged = True
            atualizacoes.append(atualizacao)
            continue

        if atualizacao.datahora_da_atualizacao < data_inicio:
            logger.info(
                f"parando de buscar posts, {atualizacao.id} ({atualizacao.url_do_post}) é muito antigo"
            )
            break
        if atualizacao.datahora_da_atualizacao > data_fim:
            logger.debug(
                f"ignorando post {post.id}, {atualizacao.id} ({atualizacao.url_do_post}) é muito novo"
            )
            continue

        atualizacoes.append(atualizacao)
    logger.info(f"encontrados {len(posts)} posts")

    return atualizacoes


def buscar_proposicoes_com_atualizacao(tipo, data_inicio, data_fim):
    logger.info(
        f"buscando proposições com atualização do tipo {tipo} entre {data_inicio} e {data_fim}"
    )

    # retorna o link pra próxima página dentro da resposta ou None
    # se for a última página
    def proxima_pagina(resposta):
        for link in resposta["links"]:
            if link["rel"] == "next":
                return link["href"]
        return None

    logger.info("requisitando atualizações (1)")
    resposta = get_com_backoff(
        f"{URL_DA_API}/proposicoes",
        headers={"Content-Type": "application/json"},
        params={
            "dataInicio": data_inicio.format("YYYY-MM-DD"),
            "dataFim": data_fim.format("YYYY-MM-DD"),
            "itens": 100,
            "siglaTipo": tipo,
        },
    )

    try:
        resposta = resposta.json()
    except json.decoder.JSONDecodeError:
        logger.error(f"resposta da API não é JSON: {resposta.text}")
        return []
    proposicoes_com_atualizacao = resposta["dados"]

    numero_de_requisicoes = 2
    while href := proxima_pagina(resposta):
        logger.info(f"requisitando atualizações ({numero_de_requisicoes})")
        resposta = get_com_backoff(
            href,
            headers={"Content-Type": "application/json"},
        ).json()
        proposicoes_com_atualizacao += resposta["dados"]
        numero_de_requisicoes += 1

    return proposicoes_com_atualizacao


def buscar_tramitacoes(id, data_inicio, data_fim):
    logger.debug(f"requisitando últimas tramitações de {id}")
    dados = []
    for dia in pendulum.period(data_inicio, data_fim).range("days"):
        resposta = get_com_backoff(
            f"{URL_DA_API}/proposicoes/{id}/tramitacoes",
            headers={"Content-Type": "application/json"},
            params={
                "dataInicio": dia.format("YYYY-MM-DD"),
                "dataFim": dia.format("YYYY-MM-DD"),
            },
        )

        try:
            dados += resposta.json()["dados"]
        except KeyError:
            logger.error(f"erro ao buscar tramitações de {id}")
            logger.error(resposta.text)
            continue

    return dados


def buscar_autor_principal_e_seu_partido(id):
    logger.debug(f"baixando autores da proposição {id}")
    try:
        autores = get_com_backoff(
            f"{URL_DA_API}/proposicoes/{id}/autores",
            headers={"Content-Type": "application/json"},
        ).json()["dados"]
    except Exception as e:
        logger.error(f"erro ao buscar autores da proposição {id}")
        logger.error(e)
        return None, None

    # a primeira assinatura é o autor principal
    autores.sort(key=lambda x: x["ordemAssinatura"], reverse=True)
    principal = autores[0]

    if principal["tipo"] == "Deputado":
        logger.debug(f"baixando partido de {principal['nome']}")
        resposta = get_com_backoff(
            principal["uri"],
            headers={"Content-Type": "application/json"},
        ).json()

        try:
            detalhes = resposta["dados"]["ultimoStatus"]
            nome = detalhes["nomeEleitoral"]
            partido = detalhes["siglaPartido"]
        except:
            nome = principal["nome"]
            partido = None
    else:
        # se não for "Deputado", vai ser "Senado Federal",
        # "Poder Executivo", etc.
        nome = principal["nome"]
        partido = None

    return nome, partido


def buscar_atualizacoes_do_tipo(
    tipo, data_inicio, data_fim, pula=tramitacao_nao_selecionada
):
    logger.info(
        f"buscando atualizações do tipo {tipo} entre {data_inicio} e {data_fim}"
    )

    proposicoes_com_atualizacao = buscar_proposicoes_com_atualizacao(
        tipo, data_inicio, data_fim
    )

    atualizacoes = []
    for proposicao in proposicoes_com_atualizacao:

        logger.info(
            f"buscando atualizações da proposição {proposicao['siglaTipo']} {proposicao['numero']}/{proposicao['ano']}"
        )

        id = proposicao["id"]
        ultimas_tramitacoes = buscar_tramitacoes(id, data_inicio, data_fim)
        autor, partido = baixar_autor_principal_e_seu_partido(id)

        for tramitacao in ultimas_tramitacoes:
            if pula(tramitacao):
                logger.info(
                    f'pulando tramitação de {id}: {tramitacao["descricaoTramitacao"]}'
                )
                continue

            logger.info(
                f'adicionando tramitação de {id}: {tramitacao["descricaoTramitacao"]}'
            )

            atualizacoes.append(
                Atualizacao(
                    id=f'{proposicao["siglaTipo"]} {proposicao["numero"]}/{proposicao["ano"]} ({tramitacao["sequencia"]})',
                    tipo=proposicao["siglaTipo"],
                    numero=proposicao["numero"],
                    ano=proposicao["ano"],
                    sequencia=tramitacao["sequencia"],
                    autor=autor,
                    partido=partido,
                    ementa=proposicao["ementa"],
                    despacho=tramitacao["despacho"],
                    tipo_de_tramitacao=tramitacao["descricaoTramitacao"],
                    url_da_atualizacao=f'https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={proposicao["id"]}',
                    datahora_da_atualizacao=pendulum.parse(tramitacao["dataHora"]),
                    datahora_do_post=None,
                    url_do_post=None,
                    ups=None,
                    downs=None,
                    num_comentarios=None,
                    flagged=False,
                    flag_related=[],
                )
            )

    return atualizacoes


def buscar_atualizacoes_na_camara(data_inicio, data_fim):
    logger.info(f"buscando atualizações na câmara entre {data_inicio} e {data_fim}")

    tipos = ["PL", "PLV", "MPV", "PLP", "PEC"]
    atualizacoes = []
    for tipo in tipos:
        atualizacoes.extend(buscar_atualizacoes_do_tipo(tipo, data_inicio, data_fim))

    return atualizacoes


def une_atualizacoes(atualizacao, unificado):
    for campo in Atualizacao._fields:
        campo_atualizacao = getattr(atualizacao, campo)
        campo_unificado = getattr(unificado, campo)

        if campo_atualizacao == None and campo_unificado == None and campo != "partido":
            logger.error(
                f'cuidado! campo "{campo}" é None em ambos, possivelmente um post duplicado'
            )
            # links pro reddit
            logger.error(f"atualizacao: {atualizacao.url_do_post}")
            logger.error(f"unificado: {unificado.url_do_post}")
            # print all fields from atualizacao
            # for f in [f for f in dir(atualizacao) if not f.startswith("__")]:
            #     logger.error(f"{f}: {getattr(atualizacao, f)}")
            atualizacao.flagged = True
            atualizacao.flag_related.append(unificado)
            return atualizacao

        # Verifica se o campo é None em um dos dois, se for, pega o valor do outro
        if campo_atualizacao == None:
            logger.debug(
                f"campo {campo} é None em atualizacao, pegando valor de unificado"
            )
            setattr(atualizacao, campo, campo_unificado)
            continue

        if campo_unificado == None:
            logger.debug(
                f"campo {campo} é None em unificado, pegando valor de atualizacao"
            )
            setattr(unificado, campo, campo_atualizacao)
            continue

    return atualizacao


def buscar_atualizacoes(data_inicio, data_fim, fontes=None):
    if fontes is None:
        fontes = ["reddit", "camara"]

    if "reddit" in fontes:
        atualizacoes_postadas = buscar_atualizacoes_postadas_no_reddit(
            data_inicio, data_fim
        )
    else:
        atualizacoes_postadas = []

    if "camara" in fontes:
        atualizacoes_da_camara = buscar_atualizacoes_na_camara(data_inicio, data_fim)
    else:
        atualizacoes_da_camara = []

    unificado = {}
    for atualizacao in atualizacoes_postadas + atualizacoes_da_camara:
        if atualizacao.id in unificado:
            # print all fields in atualizacao and unificado[atualizacao.id]
            # atualizacao_fiels = {
            #     campo: getattr(atualizacao, campo)
            #     for campo in dir(atualizacao)
            #     if not campo.startswith("_")
            # }
            # unificado_fields = {
            #     campo: getattr(unificado[atualizacao.id], campo)
            #     for campo in dir(unificado[atualizacao.id])
            #     if not campo.startswith("_")
            # }
            # logger.debug(f"atualizacao: {atualizacao_fiels}")
            # logger.debug(f"unificado: {unificado_fields}")

            resultado = une_atualizacoes(atualizacao, unificado[atualizacao.id])
            if resultado is None:
                continue

            unificado[atualizacao.id] = resultado
        else:
            unificado[atualizacao.id] = atualizacao

    return unificado.values()


def listar_atualizacoes(data_inicio, data_fim, fontes=None):
    atualizacoes = buscar_atualizacoes(data_inicio, data_fim, fontes)

    imprimir_atualizacoes(atualizacoes)


def postar_atualizacao(atualizacao):
    logger.info(f"postando atualizacao {atualizacao.id}")
    url = atualizacao.url_da_atualizacao
    autor = atualizacao.autor
    if atualizacao.partido is not None:
        autor += f" - {atualizacao.partido}"
    sequencia = atualizacao.sequencia
    if not sequencia:
        logger.error(f"atualizacao {atualizacao.id} não tem sequencia")
        return
    title = cortar(f"[{autor}] {atualizacao.id}: {atualizacao.ementa}", 300)
    flair = atualizacao.tipo_de_tramitacao
    flair = cortar(flair, 64)
    datahora = atualizacao.datahora_da_atualizacao.format("DD/MM/YYYY")
    comment = f"Despacho ({datahora})\n\n{atualizacao.despacho}"

    cliente_do_reddit.validate_on_submit = True
    logger.info(f"postando {atualizacao.id}")
    post = cliente_do_reddit.subreddit(SUBREDDIT).submit(title, url=url)
    logger.info(f"postado {atualizacao.id}: {post.shortlink}")
    post.mod.flair(text=flair)
    post.reply(comment)

    return True


def deletar_atualizacoes(atualizacoes):
    count = 0
    for atualizacao in atualizacoes:
        if atualizacao.url_do_post is None:
            logger.warning(f"atualizacao {atualizacao.id} não foi postada, pulando")
            continue
        logger.info(
            f"deletando atualizacao {atualizacao.id} ({atualizacao.url_do_post})"
        )
        post = cliente_do_reddit.submission(url=atualizacao.url_do_post)
        post.mod.remove()
        count += 1
    logger.info(f"deletadas {count} atualizacoes")


def postar_atualizacoes(atualizacoes):
    count = 0
    for atualizacao in atualizacoes:
        if atualizacao.flagged:
            logger.warning(f"atualizacao {atualizacao.id} está flaggada, pulando")
            continue
        if atualizacao.url_do_post is not None:
            logger.warning(
                f"atualizacao {atualizacao.id} já foi postada, pulando: {atualizacao.url_do_post}"
            )
            continue

        if postar_atualizacao(atualizacao):
            count += 1

    logger.info(f"postadas {count} atualizacoes")


def postar_automatico():
    hoje = pendulum.today()
    postar_atualizacoes(buscar_atualizacoes(hoje, hoje))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--log",
        "-l",
        choices=["debug", "info", "warning", "error", "critical"],
        default="debug",
    )

    parser.add_argument(
        "comando", choices=["listar", "postar", "deletar", "cron"], help=COMANDO_HELP
    )

    parser.add_argument("--dias", "-d", help=DIAS_HELP)

    parser.add_argument(
        "--fontes",
        "-f",
        choices=["reddit", "camara"],
        help=FONTES_HELP,
        action="append",
    )

    parser.add_argument(
        "--somente-flagged",
        "-F",
        help=SOMENTE_FLAGGED_HELP,
        action="store_true",
    )

    args = parser.parse_args()
    logger.setLevel(
        {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }[args.log]
    )
    logger.debug(f"argumentos: {args}")

    if args.comando == "cron":
        schedule.every(4).hours.do(postar_automatico)

        while True:
            schedule.run_pending()
            time.sleep(1)

    dias = args.dias
    if dias:
        dias = dias.split(":")
        if len(dias) == 1:
            dias = [pendulum.parse(dias[0]), pendulum.today()]
        else:
            dias = [pendulum.parse(dias[0]), pendulum.parse(dias[1])]
    else:
        dias = [pendulum.today(), pendulum.today()]

    atualizacoes = buscar_atualizacoes(dias[0], dias[1], fontes=args.fontes)
    if args.somente_flagged:
        atualizacoes = [a for a in atualizacoes if a.flagged == args.somente_flagged]

    if args.comando == "listar":
        imprimir_atualizacoes(atualizacoes)

    elif args.comando == "postar":
        postar_atualizacoes(atualizacoes)

    elif args.comando == "deletar":
        deletar_atualizacoes(atualizacoes)
