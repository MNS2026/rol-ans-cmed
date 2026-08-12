# Rol da ANS × CMED

Consulta dos medicamentos citados nominalmente no **Anexo II da RN 465/2021** (Rol de Procedimentos
e Eventos em Saúde da ANS) cruzados com os preços da **tabela CMED/Anvisa**.

**Protótipo em validação técnica.** Os dados ainda passam por curadoria e não substituem as fontes oficiais.

## Por que existe

Hoje, para saber se um medicamento consta do Rol e quanto ele custa, é preciso abrir o Anexo II da
RN 465 no portal da ANS, baixar a lista de preços no portal da CMED e cruzar as duas coisas à mão.
Esta página faz esse cruzamento e mostra o resultado em um só lugar.

## Limite importante

A lista traz os medicamentos **citados nominalmente** no Anexo II. Pela Lei 9.656/1998, medicamentos
registrados na Anvisa, para uso não *off label* e de administração em ambiente hospitalar, têm
cobertura obrigatória mesmo sem constar do Rol. **A ausência de um medicamento aqui não significa
ausência de cobertura.**

## Sobre os preços

A CMED omite o PMC (Preço Máximo ao Consumidor) das apresentações em embalagem hospitalar e de uso
restrito a hospitais e clínicas (Resolução CMED nº 03/2009). Como boa parte dos medicamentos do Rol
é hospitalar, a referência utilizável na maioria dos casos é o **PF (Preço Fábrica)**, que é o teto
de venda às operadoras e à Administração Pública.

## Fontes

- **Rol:** RN 465/2021, Anexo II, atualizado pela RN 676/2026 (publicado em 03/08/2026)
- **Preços:** lista de conformidade CMED/Anvisa publicada em 11/08/2026

## Como funciona

`index.html` é um arquivo único e autônomo: os dados vêm embutidos nele. Abre em qualquer navegador,
inclusive sem internet. Não depende de servidor nem de banco de dados.

Os scripts que geram os dados (download da CMED, leitura do Anexo II e cruzamento) estão na pasta
do projeto e serão incorporados ao repositório quando a atualização automática for implantada.
