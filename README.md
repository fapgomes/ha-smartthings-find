<p align="center">
  <img src="custom_components/smartthings_find/brand/logo@2x.png" alt="SmartThings Find" width="420">
</p>

<p align="center">
  <a href="https://github.com/fapgomes/ha-smartthings-find/releases"><img src="https://img.shields.io/github/v/release/fapgomes/ha-smartthings-find?style=flat-square&label=Release" alt="Release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5?style=flat-square" alt="HACS Custom"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/fapgomes/ha-smartthings-find?style=flat-square&label=License" alt="License"></a>
</p>

# SmartThings Find para Home Assistant

Mostra no Home Assistant a localização e a bateria dos dispositivos Samsung
registados no [SmartThings Find](https://smartthingsfind.samsung.com):
telemóveis, tablets, relógios, auriculares e SmartTags. Também permite
fazê-los tocar e pedir-lhes a posição atual.

> [!NOTE]
> A Samsung não tem uma API pública para o SmartThings Find. A integração usa
> a sessão do site, autenticada com o cabeçalho `Cookie` do browser. Quando a
> sessão expira, o Home Assistant pede um cookie novo.

## Instalação

### Com HACS (recomendado)

[![Abrir o repositório no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fapgomes&repository=ha-smartthings-find&category=integration)

1. Carregar no botão acima (ou, no HACS: ⋮ → **Repositórios personalizados**
   → `https://github.com/fapgomes/ha-smartthings-find`, categoria
   **Integração**).
2. Descarregar **SmartThings Find** e reiniciar o Home Assistant.

### Manual

Copiar a pasta `custom_components/smartthings_find` deste repositório para
`<config>/custom_components/` e reiniciar o Home Assistant.

## Configuração

[![Adicionar a integração ao Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartthings_find)

Ou em **Definições → Dispositivos e serviços → Adicionar integração →
SmartThings Find**. É pedido o cabeçalho `Cookie`:

1. Abrir uma **janela privada** do browser, num computador, e iniciar sessão
   em [smartthingsfind.samsung.com](https://smartthingsfind.samsung.com).
2. Abrir as ferramentas de programador (F12) → **Rede** e recarregar a
   página.
3. Selecionar o pedido `chkLogin.do` e copiar o cabeçalho `Cookie` completo
   do pedido.
4. Fechar a janela privada **sem terminar a sessão** e não voltar a usar
   essa sessão no browser.

> [!IMPORTANT]
> Não partilhe a mesma sessão entre o browser e o Home Assistant: a Samsung
> pode terminá-la. E trate o cookie como uma palavra-passe, porque dá acesso
> à localização dos seus dispositivos enquanto a sessão estiver ativa.

### Opções

| Opção | Omissão | Descrição |
|---|---|---|
| Intervalo de atualização | 120 s | De quanto em quanto tempo lê os dispositivos |
| Manter a sessão ativa | 180 s | Só é usado quando o intervalo de atualização é maior do que este valor |
| Modo das SmartTags | Passivo | Ativo pede a posição à tag em cada atualização |
| Modo dos restantes dispositivos | Passivo | Ativo pede a posição em cada atualização (gasta bateria do dispositivo) |

O cookie pode ser trocado nas opções ou em **Reconfigurar**.

## Entidades

Para cada dispositivo:

| Entidade | Descrição |
|---|---|
| `device_tracker` | Última posição conhecida, com a hora e a origem nos atributos |
| `sensor` Bateria | Nível de bateria; a Samsung envia degraus (100, 50, 15, 5 %) |
| `sensor` Última atualização | Quando o dispositivo reportou a posição à Samsung |
| `button` Tocar / Parar de tocar | Faz tocar o dispositivo |
| `button` Atualizar localização | Pede a posição e a bateria ao dispositivo e aguarda a resposta |

## Migrar da integração 1bobby-git/HA-SmartThings-Find

Esta integração usa o mesmo domínio (`smartthings_find`) e os mesmos
identificadores de entidade, por isso pode substituir a original sem perder
entidades, automações nem histórico:

1. No HACS, remover o repositório `1bobby-git/HA-SmartThings-Find`. **Não
   apagar** a integração em Definições → Dispositivos e serviços.
2. Instalar esta integração pelo HACS (secção [Instalação](#instalação)) e
   reiniciar.
3. A entrada existente passa a usar o código novo. Se a sessão tiver
   expirado, fazer **Reautenticar** com um cookie novo.

Se preferir começar do zero, apague também a integração antiga antes de a
adicionar outra vez. Nesse caso as entidades podem ficar com IDs diferentes.

## Resolução de problemas

- **Dispositivos que deixam de aparecer:** às vezes a Samsung continua a
  aceitar a sessão, mas deixa de devolver alguns dispositivos. A integração
  deteta isso e pede um cookie novo. Um dispositivo removido de propósito da
  conta Samsung pode ser apagado na página do dispositivo no Home Assistant.
- **Diagnósticos:** na página da integração, ⋮ → **Descarregar
  diagnósticos**. Inclui as respostas da API, com o cookie, os IDs de
  utilizador e as coordenadas ocultados.
- **Registos detalhados:**

  ```yaml
  logger:
    logs:
      custom_components.smartthings_find: debug
  ```

## Desenvolvimento

```sh
python3 -m pytest tests
```

O cliente da API (`api.py`) não depende do Home Assistant. Os testes correm
contra um servidor local que simula a Samsung, sem conta nem cookie.
`tools/extract_site_js.py` descarrega o JavaScript público do site e lista
os endpoints e as operações que ele usa.

## Créditos e licença

Reescrita a partir do trabalho de
[1bobby-git/HA-SmartThings-Find](https://github.com/1bobby-git/HA-SmartThings-Find).
Licença [MIT](LICENSE). Projeto não oficial, sem ligação à Samsung.
SmartThings é uma marca da Samsung Electronics.
