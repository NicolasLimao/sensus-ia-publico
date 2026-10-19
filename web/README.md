# Sensus IA — Interface web

Cliente React e TypeScript do Sensus IA. Reúne chat em streaming, fontes, feedback, autenticação, convites, confirmação de cadastro, redefinição de senha e configuração de IA para administradores.

## Desenvolvimento

Requer Node.js 24.x, npm e a API do projeto em `http://localhost:8001`.

```sh
npm ci
npm run dev
```

Abra **http://localhost:5173**. O Vite encaminha `/api` para o backend, de modo que o cookie de sessão viaja na mesma origem do navegador. O passo a passo para configurar a API e experimentar sem provedores está no [README principal](../README.md).

## Estrutura

- `src/components/`: telas de identidade, chat e painéis administrativos.
- `src/api/`: cliente HTTP e interpretação dos eventos de streaming.
- `src/state/`: estado da conversa e ações de feedback.
- `src/types.ts`: contratos usados pela interface.

## Verificações

```sh
npm run lint
npm run typecheck
npm test
npm run build
```

Os testes usam Vitest, Testing Library e jsdom. O build é gerado em `dist/`; o servidor que hospedar a SPA deve devolver `index.html` também para `/confirmar`, `/convite` e `/redefinir`. Nenhum segredo de provedor deve ser colocado no bundle do navegador.
