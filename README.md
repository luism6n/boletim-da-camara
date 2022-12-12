# Boletim da Câmara

Este é um script em Python que baixa atualizações da API da Câmara (https://dadosabertos.camara.leg.br/swagger/api.html) e as posta em algum subreddit.

## Instalação e uso

Clonar repositório e rodar:

`pip install -r requirements`

Settar variáveis de ambiente. Pode copiar .env.exemplo e mudar para os valores desejados. Para acessar a API do Reddit, você precisa criar um app em https://www.reddit.com/prefs/apps e obter uma chave e um segredo.

Para ver os comandos disponíveis, rode:

`python main.py -h`

## Algoritmo

Para postar as atualizações de um dia específico:

- Obter o ID de todas as proposições que sofreram atualizações naquela data via /proposicoes
- Para cada uma, obter as atualizações naquela data via /proposicoes/{id}/tramitacoes
- Baixar o autor e partido da proposição via /proposicoes/{id}/autores
- Filtrar atualizações pelo tipo de tramitação de acordo com tramitacoes-selecionadas.txt
