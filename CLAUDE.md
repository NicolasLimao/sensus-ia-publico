# Sensus IA — instruções do projeto

Produto **RAG genérico e multi-tenant**: o cliente traz seus próprios documentos e o Sensus responde por **chat**, com respostas fundamentadas e rastreáveis à fonte, sem alucinar o que não está na base. Projeto **greenfield**, em desenvolvimento.

Leia **[docs/SDD.md](docs/SDD.md)** para arquitetura, modelo de dados e roadmap.

## Guardrails (importante)

- **Reimplementação limpa.** O Sensus reaproveita *padrões de engenharia* de RAG comprovados num projeto anterior, mas é reconstruído do zero como produto genérico. **NÃO copiar** código, prompt, schema, dataset, marca, dados ou credenciais de nenhum projeto/empregador anterior. Nada específico de cliente entra aqui.
- **Segredos nunca no repositório.** Config por variáveis de ambiente; `.env` é gitignored. Nenhuma chave/token/credencial versionada nem embutida em imagem.
- **Multi-tenant desde o design.** Dados de um cliente jamais vazam para outro; isolamento é requisito, não opcional.

## Como trabalhar aqui

- **Projeto novo → desenhar antes de codar.** Use o fluxo superpowers: **brainstorming → writing-plans → execução com TDD**, em **fatias pequenas** (cada fatia entrega algo testável).
- **TDD:** teste que falha → implementação mínima → teste passa → commit. Commits pequenos e descritivos.
- **Medir antes de crer:** decisões de qualidade (chunking, prompt, recuperação) validadas por medição, não por achismo — como no projeto de origem.
- Specs/planos de brainstorming vão para `docs/superpowers/` (gitignored). O **SDD** (`docs/SDD.md`) é o design versionado do produto.

## Convenções

- **Idioma:** docs e comentários em **PT-BR**; identificadores de código em **inglês**. Não misturar.
- Nada de "TODO" solto: o que fica pendente vira uma fatia no roadmap do SDD.
