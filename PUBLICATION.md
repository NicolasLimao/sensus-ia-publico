# Edição pública

Esta edição usa exemplos fictícios. Dados de contas, pessoas e clientes, documentos preenchidos, bancos locais e relatórios operacionais ficam fora do repositório. Configure suas próprias credenciais em `.env`, usando `.env.example` quando disponível.

Dados locais devem ficar em `private-data/`, `local-data/`, `uploads/`, `exports/` ou `backups/`, todos ignorados. Exemplos sintéticos necessários à execução e aos testes continuam versionados. O `.gitignore` não remove conteúdo de commits antigos; esta edição tem histórico novo, sem os commits dos repositórios privados.

Antes de cada publicação, revise os arquivos preparados com `git diff --cached` e execute `gitleaks dir . --redact`, além dos testes indicados no README. O scanner usa regras padrão e uma regra adicional para `internalChatToken`; dados pessoais em imagens e documentos também exigem revisão manual.

As credenciais existentes na origem privada não foram usadas nem revogadas por esta preparação. Sua validade não foi testada. Exportações que possam contê-las não devem ser copiadas para esta edição.
