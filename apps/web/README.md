# web

PWA sem etapa de build: `public/` é servido como está por `nginx-unprivileged`.
Rotas por hash (`#/`, `#/novo`, `#/alerta/:id`, `#/historico`, `#/ajustes`); a API fica na
mesma origem, em `/api`. Fontes (Inter e Space Grotesk, OFL) servidas localmente.

```bash
npm test   # formatação e gráficos (Node 20+); os textos batem com os do push do checker
```

Ao mudar algum arquivo de `public/`, suba a `VERSION` em `public/sw.js` para o PWA
instalado buscar a versão nova.
