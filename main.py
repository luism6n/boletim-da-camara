from datetime import datetime

import argparse
import json
import logging
import os
import requests


import praw

RAIZ_DO_PROJETO = os.path.abspath(os.path.dirname(__file__))


def caminho_absoluto(arquivo):
    """Retorna o caminho absoluto a partir da raiz do projeto"""
    return os.path.join(RAIZ_DO_PROJETO, arquivo)


logging.basicConfig(
    format="%(asctime)s %(levelname)-8s: %(message)s", datefmt="%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

with open(caminho_absoluto("config.json")) as f:
    config = json.load(f)

SUBREDDIT = config["subreddit"]

URL_DA_API = "https://dadosabertos.camara.leg.br/api/v2"
with open(caminho_absoluto("tramitacoes-selecionadas.txt")) as f:
    TRAMITACOES_SELECIONADAS = [l.strip() for l in f.readlines() if l.strip()]


cliente_do_reddit = praw.Reddit(**config["credenciaisDoReddit"])


def baixar_proposicoes_com_atualizacao(dia, tipo):
    # retorna o link pra próxima página dentro da resposta ou None
    # se for a última página
    def proxima_pagina(resposta):
        for link in resposta["links"]:
            if link["rel"] == "next":
                return link["href"]
        return None

    logger.info("requisitando atualizações (1)")
    resposta = requests.get(
        f"{URL_DA_API}/proposicoes",
        headers={"Content-Type": "application/json"},
        params={
            "dataInicio": dia,
            "dataFim": dia,
            "itens": 100,
            "siglaTipo": tipo,
        },
    ).json()
    proposicoes_com_atualizacao = resposta["dados"]

    numero_de_requisicoes = 2
    while href := proxima_pagina(resposta):
        logger.info(f"requisitando atualizações ({numero_de_requisicoes})")
        resposta = requests.get(
            href,
            headers={"Content-Type": "application/json"},
        ).json()
        proposicoes_com_atualizacao += resposta["dados"]
        numero_de_requisicoes += 1

    return proposicoes_com_atualizacao


def baixar_tramitacoes(dia, id):
    logger.debug(f"requisitando últimas tramitações de {id}")
    return requests.get(
        f"{URL_DA_API}/proposicoes/{id}/tramitacoes",
        headers={"Content-Type": "application/json"},
        params={
            "dataInicio": dia,
            "dataFim": dia,
        },
    ).json()["dados"]


def baixar_autor_principal_e_seu_partido(id):
    logger.debug(f"baixando autores da proposição {id}")
    autores = requests.get(
        f"{URL_DA_API}/proposicoes/{id}/autores",
        headers={"Content-Type": "application/json"},
    ).json()["dados"]

    # a primeira assinatura é o autor principal
    autores.sort(key=lambda x: x["ordemAssinatura"], reverse=True)
    principal = autores[0]

    if principal["tipo"] == "Deputado":
        logger.debug(f"baixando partido de {principal['nome']}")
        resposta = requests.get(
            principal["uri"],
            headers={"Content-Type": "application/json"},
        ).json()

        detalhes = resposta["dados"]["ultimoStatus"]
        nome = detalhes["nomeEleitoral"]
        partido = detalhes["siglaPartido"]
    else:
        # se não for "Deputado", vai ser "Senado Federal",
        # "Poder Executivo", etc.
        nome = principal["nome"]
        partido = None

    return nome, partido


def tramitacao_nao_selecionada(tramitacao):
    return tramitacao["descricaoTramitacao"] not in TRAMITACOES_SELECIONADAS


# baixar_atualizacoes não recebe um argumento "maximo" porque todas atualizações
# de um dia precisam ser baixadas juntas para serem ordenadas. Se houvesse um
# argumento para limitar o número de proposições, teríamos que baixar tudo igual
# e retornar somente algumas sem ganhos de performance. O argumento dia deve ser
# no formato yyyy-mm-dd (ou %Y-%d-%m).
def baixar_atualizacoes(
    dia, tipo, pula=tramitacao_nao_selecionada, diretorio_destino="atualizacoes"
):
    arquivo_de_cache = f"{diretorio_destino}/{tipo}-{dia}.json"
    if os.path.exists(arquivo_de_cache):
        logger.info(f"atualizacoes para {tipo} do dia {dia} já baixadas, usando cache")
        with open(caminho_absoluto(arquivo_de_cache)) as f:
            return json.load(f)

    proposicoes_com_atualizacao = baixar_proposicoes_com_atualizacao(dia, tipo)

    atualizacoes = []
    for proposicao in proposicoes_com_atualizacao:
        nome = f"{proposicao['siglaTipo']} {proposicao['numero']}/{proposicao['ano']}"
        id = proposicao["id"]
        ultimas_tramitacoes = baixar_tramitacoes(dia, id)
        autor, partido = baixar_autor_principal_e_seu_partido(id)

        logger.info(f"{len(ultimas_tramitacoes)} tramitações encontradas para {nome}")

        for tramitacao in ultimas_tramitacoes:
            if pula(tramitacao):
                logger.info(
                    f'pulando tramitação de {nome}: {tramitacao["descricaoTramitacao"]}'
                )
                continue

            logger.info(
                f'salvando tramitação de {nome}: {tramitacao["descricaoTramitacao"]}'
            )
            atualizacoes.append(
                {
                    "id": proposicao["id"],
                    "nome": nome,
                    "autor": autor,
                    "partido": partido,
                    "ementa": proposicao["ementa"],
                    "despacho": tramitacao["despacho"],
                    "sequencia": tramitacao["sequencia"],
                    "tipo_de_tramitacao": tramitacao["descricaoTramitacao"],
                    "url": f'https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={proposicao["id"]}',
                    "datahora": tramitacao["dataHora"],
                }
            )

    with open(caminho_absoluto(arquivo_de_cache), "w") as f:
        json.dump(atualizacoes, f)

    return atualizacoes


def listar_atualizacoes(dia, tipos):
    logger.info(f"listando atulizações de {', '.join(tipos)} do dia {dia}")

    atualizacoes = {}
    for tipo in tipos:
        atualizacoes[tipo] = baixar_atualizacoes(dia, tipo)

        for atualizacao in atualizacoes[tipo]:
            print("id:", atualizacao["id"])
            print("nome:", atualizacao["nome"])
            print("ementa:", atualizacao["ementa"])
            print("despacho:", atualizacao["despacho"])
            print("datahora:", atualizacao["datahora"])
            print("nome:", atualizacao["nome"])
            print("autor:", atualizacao["autor"])
            print("url:", atualizacao["url"])
            print("número de sequência:", atualizacao["sequencia"])
            print("tipo de tramitação:", atualizacao["tipo_de_tramitacao"])
            print("--")

    numero_de_atualizacoes = sum([len(itens) for tipo, itens in atualizacoes.items()])
    logger.info(f"{numero_de_atualizacoes} atualizações encontradas")


def baixar_ultimos_posts(maximo):
    logger.info(f"baixando os últimos {maximo} posts")
    posts = []
    for i, post in enumerate(cliente_do_reddit.subreddit(SUBREDDIT).new(limit=maximo)):
        if i >= maximo:
            break
        posts.append(post)

    return posts


def deletar_posts(maximo):
    posts = baixar_ultimos_posts(maximo)
    logger.info(f"deletando os últimos {maximo} posts")
    for post in posts:
        logger.debug(f"removing post {post.id}: {post.title[:30]}...")
        post.mod.remove(mod_note="auto-removed")
    logger.info(f"{len(posts)} posts removidos")


def listar_posts(maximo):
    posts = baixar_ultimos_posts(maximo)
    for post in posts:
        print("título:", post.title)
        print("url:", post.url)
        print("datahora:", datetime.fromtimestamp(post.created_utc), "UTC")
        print("--")


def cortar(texto, maximo_de_caracteres):
    if len(texto) > maximo_de_caracteres:
        texto = texto[: maximo_de_caracteres - 3] + "..."
    return texto


def postar(atualizacao):
    url = atualizacao["url"]
    autor = atualizacao["autor"]
    if atualizacao["partido"]:
        autor += f' - {atualizacao["partido"]}'
    title = f"[{autor}] {atualizacao['nome']}: {atualizacao['ementa']}"
    title = cortar(title, 300)

    flair = atualizacao["tipo_de_tramitacao"]
    flair = cortar(flair, 64)
    datahora = datetime.strptime(atualizacao["datahora"], "%Y-%m-%dT%H:%M")
    datahora = datahora.strftime("%d/%m/%Y")
    comment = f'Despacho ({datahora}):\n\n"{atualizacao["despacho"]}"'

    cliente_do_reddit.validate_on_submit = True
    logger.info(
        f'postando "{title[:50]}..." (id {atualizacao["id"]}, seq {atualizacao["sequencia"]})'
    )
    post = cliente_do_reddit.subreddit(SUBREDDIT).submit(title, url=url)
    post.mod.flair(text=flair)
    post.reply(comment)


def postar_atualizacoes(dia, tipos):
    postadas = 0
    total = 0
    for tipo in tipos:
        atualizacoes = baixar_atualizacoes(dia, tipo)
        for atualizacao in atualizacoes:
            if not atualizacao["url"]:
                logger.debug(
                    f"pulando atualizacao sem link de {atualizacao['nome']} do dia {dia}"
                )
                continue
            postar(atualizacao)
            postadas += 1
        total += len(atualizacoes)
    logger.info(f"{postadas}/{total} atualizações postadas")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--log",
        "-l",
        choices=["debug", "info", "warning", "error", "critical"],
        default="debug",
    )

    parser.add_argument(
        "--maximo",
        "-m",
        help="numero de posts para listar",
        default=10,
        type=int,
    )

    parser.add_argument(
        "--dia",
        "-d",
        help="data das atualizações no formato YYYY-MM-DD",
        default="2021-08-31",
    )

    parser.add_argument(
        "--tipo",
        "-t",
        default=["PL", "PLV", "MPV", "PLP", "PEC"],
        help="tipo de atualização a ser listado",
        action="append",
        choices=["PL", "PLV", "MPV", "PLP", "PEC"],
    )

    parser.add_argument(
        "comando",
        choices=[
            "listar-atualizacoes",
            "listar-posts",
            "deletar-posts",
            "postar-atualizacoes",
            "rodar-servidor",
        ],
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

    if args.comando == "listar-atualizacoes":
        assert args.dia and args.tipo
        listar_atualizacoes(args.dia, args.tipo)
        return

    if args.comando == "postar-atualizacoes":
        assert args.dia and args.tipo
        postar_atualizacoes(args.dia, args.tipo)
        return

    if args.comando == "listar-posts":
        assert args.maximo
        listar_posts(args.maximo)
        return

    if args.comando == "deletar-posts":
        assert args.maximo
        deletar_posts(args.maximo)
        return


if __name__ == "__main__":
    main()
