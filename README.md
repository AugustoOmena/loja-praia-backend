
<img width="2912" height="1335" alt="image" src="https://github.com/user-attachments/assets/81837932-163e-43d7-aae9-e4c7c5d584c0" />

## 📉 Estratégia de Custos (Zero-to-Scale)

O projeto foi inteiramente desenhado ao redor do conceito de otimização de recursos. Utilizando os limites gratuitos ("Free Tiers") generosos da AWS, Vercel, Firebase e Supabase, o custo operacional é praticamente **$0** para as primeiras centenas de usuários. Mesmo quando a aplicação escala para milhares de acessos, a arquitetura *serverless* garante que você pague apenas pelo que consumir, mantendo as despesas operacionais (OpEx) extremamente baixas.

## ⚙️ Configuração e Execução

### Pré-requisitos
- Python 3.10+
- [Terraform](https://developer.hashicorp.com/terraform/downloads) configurado
- Node.js & Angular CLI (para o frontend)
- Contas e credenciais configuradas: AWS, Firebase, Supabase e Mercado Pago.

### Instalação (Backend)
```bash
# Clone o repositório
git clone https://github.com/AugustoOmena/loja-praia-backend.git

# Acesse a pasta do projeto
cd loja-praia-backend

# Crie e ative um ambiente virtual
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate no Windows

# Instale as dependências
pip install -r requirements.txt
```

*(Adicione instruções específicas sobre como inicializar o Terraform: `terraform init`, `terraform apply`, etc., conforme a estrutura das pastas).*

## 👨‍💻 Autor

**Augusto do Nascimento Omena**  
Engenheiro de Software | Especialista em Arquitetura de Microsserviços e Cloud
