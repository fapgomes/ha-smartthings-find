# Changelog

## 2.0.0 — por lançar

Reescrita completa, compatível com as entradas e entidades do upstream v1.4.4.

- Lê o resultado dos pedidos de localização (`getOperationResult.do`) e a
  localização atual das SmartTags (`getTagLocation.do`).
- Pede um cookie novo quando dispositivos conhecidos deixam de ser devolvidos
  (sessão degradada) e permite apagar dispositivos que já não existem.
- Reautenticação só depois de 3 ciclos seguidos rejeitados; comandos (tocar,
  localizar) nunca são repetidos automaticamente.
- Keepalive só quando o intervalo de atualização é maior do que o da sessão.
- Diagnósticos com respostas cruas da API, com dados sensíveis ocultados.
- Removida a autenticação por conta Samsung e a dependência `samsung-re-find`.
- Traduções em inglês e português.
