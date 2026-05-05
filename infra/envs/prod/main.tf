terraform {
  required_version = ">= 1.0.0" # Boa prática

  backend "s3" {
    bucket = "augusto-omena-tfstate"
    key    = "backend/terraform.tfstate" # O caminho dentro do bucket (organização)
    region = "us-east-1"

    # Opcional (Recomendado): Encripta o arquivo em repouso
    encrypt = true
  }
}

provider "aws" {
  region  = "us-east-1"
}

# --- 1. LAYER DE DEPENDÊNCIAS (PIP INSTALL) ---
resource "null_resource" "install_layer" {
  triggers = {
    # requirements = filemd5("../../../src/layers/requirements.txt") <-- COMENTE ISSO
    always_run = timestamp() # <-- ADICIONE ISSO
  }

  # MANTENHA COMENTADO: Como você fez a instalação manual para Linux no Mac,
  # não queremos que o Terraform rode o pip local novamente e estrague os binários.
  # provisioner "local-exec" {
  #   command = "pip install -r ../../../src/layers/requirements.txt -t ../../../src/layers/python"
  # }
}

data "archive_file" "layer_zip" {
  type        = "zip"
  source_dir  = "../../../src/layers"
  output_path = "${path.module}/build/layer.zip"
  excludes    = ["requirements.txt"]
  depends_on  = [null_resource.install_layer]
}

resource "aws_lambda_layer_version" "main_dependencies" {
  layer_name          = "loja-omena-deps"
  filename            = data.archive_file.layer_zip.output_path
  source_code_hash    = data.archive_file.layer_zip.output_base64sha256
  compatible_runtimes = ["python3.11"]
}

# --- 1.1 LAYER DE CÓDIGO COMPARTILHADO (SHARED) ---
resource "null_resource" "package_shared" {
  triggers = {
    # Sempre roda para pegar alterações no código compartilhado
    always_run = timestamp()
  }
  provisioner "local-exec" {
    # Cria pasta python/ e copia o shared para dentro. Lambda adiciona 'python' ao PATH automaticamente.
    command = "mkdir -p ${path.module}/build/shared_layer/python && cp -R ../../../src/shared ${path.module}/build/shared_layer/python/"
  }
}

data "archive_file" "shared_code_zip" {
  type        = "zip"
  source_dir  = "${path.module}/build/shared_layer"
  output_path = "${path.module}/build/shared_code.zip"
  depends_on  = [null_resource.package_shared]
}

resource "aws_lambda_layer_version" "shared_code" {
  layer_name          = "loja-omena-shared-code"
  filename            = data.archive_file.shared_code_zip.output_path
  source_code_hash    = data.archive_file.shared_code_zip.output_base64sha256
  compatible_runtimes = ["python3.11"]
}

# --- 2. API GATEWAY ---
resource "aws_apigatewayv2_api" "main" {
  name          = "loja-omena-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["POST", "GET", "OPTIONS", "PUT", "DELETE"]
    allow_headers = ["content-type", "authorization", "x-idempotency-key", "x-backoffice", "x-me-signature"]
    max_age       = 300
  }
}

resource "aws_apigatewayv2_stage" "prod" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true
}

# --- 3. MICROSERVIÇO: PAYMENT ---
module "payment_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-payment"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/payment"
  # API Gateway HTTP API ~30s máx na integração Lambda; timeout da função alinha com esse teto.
  timeout     = 30
  memory_size = 512

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  ssm_app_secrets_prefix = local.ssm_prefix

  environment_variables = {
    MP_ACCESS_TOKEN         = "TEST-3645506064282139-010508-daf199203ea82aa3e7ed6e2daf9e4edb-424720501"
    SUPABASE_URL            = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY            = data.aws_ssm_parameter.app["supabase_key"].value
    SUPABASE_SERVICE_ROLE_KEY = data.aws_ssm_parameter.app["supabase_service_role_key"].value
    MELHOR_ENVIO_TOKEN      = data.aws_ssm_parameter.app["melhor_envio_token"].value
    MELHOR_ENVIO_API_URL    = data.aws_ssm_parameter.app["melhor_envio_api_url"].value
    CEP_ORIGEM              = data.aws_ssm_parameter.app["cep_origem"].value
    ME_SENDER_PROFILE       = data.aws_ssm_parameter.app["me_sender_profile"].value
    SSM_APP_SECRETS_PREFIX  = local.ssm_prefix
    POWERTOOLS_SERVICE_NAME = "payment"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

# Integração Payment
resource "aws_apigatewayv2_integration" "payment" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.payment_lambda.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "payment" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /pagamento"
  target    = "integrations/${aws_apigatewayv2_integration.payment.id}"
}

resource "aws_lambda_permission" "api_gw_payment" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.payment_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/pagamento"
}


# --- 4. MICROSERVIÇO: PRODUCTS ---
module "products_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-products"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/products"

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  ssm_app_secrets_prefix = local.ssm_prefix

  environment_variables = {
    SUPABASE_URL                 = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY                 = data.aws_ssm_parameter.app["supabase_key"].value
    SUPABASE_SERVICE_ROLE_KEY    = data.aws_ssm_parameter.app["supabase_service_role_key"].value
    MELHOR_ENVIO_TOKEN           = data.aws_ssm_parameter.app["melhor_envio_token"].value
    CEP_ORIGEM                   = data.aws_ssm_parameter.app["cep_origem"].value
    SSM_APP_SECRETS_PREFIX       = local.ssm_prefix
    POWERTOOLS_SERVICE_NAME      = "products"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

# Integração Products
resource "aws_apigatewayv2_integration" "products" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.products_lambda.invoke_arn
  payload_format_version = "2.0"
}

# Rota 1: Raiz (/produtos) para Listar e Criar (POST)
resource "aws_apigatewayv2_route" "products_root" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /produtos"
  target    = "integrations/${aws_apigatewayv2_integration.products.id}"
}

# Rota 2: Proxy (/produtos/123) para Editar (PUT) e Deletar (DELETE)
resource "aws_apigatewayv2_route" "products_proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /produtos/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.products.id}"
}

# Permissão Genérica (O * no final permite sub-rotas como /produtos/123)
resource "aws_lambda_permission" "api_gw_products" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.products_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/produtos*"
}

# --- 5. MICROSERVIÇO: PROFILES (Gerenciamento de Usuários - Backoffice) ---
module "profiles_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-profiles"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/profiles"

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  environment_variables = {
    SUPABASE_URL            = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY            = data.aws_ssm_parameter.app["supabase_key"].value
    POWERTOOLS_SERVICE_NAME = "profiles"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

# Integração Profiles
resource "aws_apigatewayv2_integration" "profiles" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.profiles_lambda.invoke_arn
  payload_format_version = "2.0"
}

# Rota 1: Raiz (/usuarios) para Listar (GET), Atualizar (PUT) e Deletar (DELETE)
resource "aws_apigatewayv2_route" "profiles_root" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /usuarios"
  target    = "integrations/${aws_apigatewayv2_integration.profiles.id}"
}

# Rota 2: Proxy (/usuarios/{id}) para operações específicas (se necessário)
resource "aws_apigatewayv2_route" "profiles_proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /usuarios/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.profiles.id}"
}

# Permissão Genérica
resource "aws_lambda_permission" "api_gw_profiles" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.profiles_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/usuarios*"
}

# --- 6. MICROSERVIÇO: ORDERS (Pedidos) ---
module "orders_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-orders"
  handler       = "api/orders_controller.lambda_handler"
  source_dir    = "../../../src/orders"

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  environment_variables = {
    SUPABASE_URL            = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY            = data.aws_ssm_parameter.app["supabase_key"].value
    SUPABASE_SERVICE_ROLE_KEY = data.aws_ssm_parameter.app["supabase_service_role_key"].value
    SUPABASE_ANON_KEY       = data.aws_ssm_parameter.app["supabase_anon_key"].value
    MP_ACCESS_TOKEN         = "TEST-3645506064282139-010508-daf199203ea82aa3e7ed6e2daf9e4edb-424720501"
    POWERTOOLS_SERVICE_NAME = "orders"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

# Integração Orders
resource "aws_apigatewayv2_integration" "orders" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.orders_lambda.invoke_arn
  payload_format_version = "2.0"
}

# Rota 1: Raiz (/pedidos) para Listar (GET)
resource "aws_apigatewayv2_route" "orders_root" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /pedidos"
  target    = "integrations/${aws_apigatewayv2_integration.orders.id}"
}

# Rota 2: Proxy (/pedidos/{id}) para Detalhe (GET), solicitar cancelamento (POST), Backoffice (PUT)
resource "aws_apigatewayv2_route" "orders_proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /pedidos/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.orders.id}"
}

# Rota 3: Backoffice (/backoffice/pedidos) para listar pedidos no painel admin
resource "aws_apigatewayv2_route" "orders_backoffice_root" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /backoffice/pedidos"
  target    = "integrations/${aws_apigatewayv2_integration.orders.id}"
}

# Rota 4: Backoffice proxy (/backoffice/pedidos/{id}) para detalhe/atualizacao no painel admin
resource "aws_apigatewayv2_route" "orders_backoffice_proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /backoffice/pedidos/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.orders.id}"
}

# Permissão Genérica (O * no final permite sub-rotas como /pedidos/{id} e /pedidos/{id}/solicitar-cancelamento)
resource "aws_lambda_permission" "api_gw_orders" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.orders_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/*pedidos*"
}

# --- 7. MICROSERVIÇO: SHIPPING (Cálculo de frete - Melhor Envio) ---
module "shipping_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-shipping"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/shipping"

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  environment_variables = {
    MELHOR_ENVIO_TOKEN    = data.aws_ssm_parameter.app["melhor_envio_token"].value
    MELHOR_ENVIO_API_URL  = data.aws_ssm_parameter.app["melhor_envio_api_url"].value
    CEP_ORIGEM            = data.aws_ssm_parameter.app["cep_origem"].value
    POWERTOOLS_SERVICE_NAME = "shipping"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

resource "aws_apigatewayv2_integration" "shipping" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.shipping_lambda.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "shipping" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /frete"
  target    = "integrations/${aws_apigatewayv2_integration.shipping.id}"
}

resource "aws_lambda_permission" "api_gw_shipping" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.shipping_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/frete"
}

# --- 7b. MICROSERVIÇO: WEBHOOK (Melhor Envio) ---
module "webhook_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-webhook"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/webhook"
  timeout       = 30
  memory_size   = 256

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  ssm_app_secrets_prefix = local.ssm_prefix

  environment_variables = {
    SUPABASE_URL               = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY               = data.aws_ssm_parameter.app["supabase_key"].value
    SUPABASE_SERVICE_ROLE_KEY  = data.aws_ssm_parameter.app["supabase_service_role_key"].value
    SMTP_HOST                  = var.smtp_host != "" ? var.smtp_host : data.aws_ssm_parameter.app["smtp_host"].value
    SMTP_PORT                  = var.smtp_port != "" ? var.smtp_port : data.aws_ssm_parameter.app["smtp_port"].value
    SMTP_USER                  = var.smtp_user != "" ? var.smtp_user : data.aws_ssm_parameter.app["smtp_user"].value
    SMTP_PASS                  = var.smtp_pass != "" ? var.smtp_pass : data.aws_ssm_parameter.app["smtp_pass"].value
    MELHOR_ENVIO_CLIENT_SECRET = var.melhor_envio_client_secret != "" ? var.melhor_envio_client_secret : data.aws_ssm_parameter.app["melhor_envio_client_secret"].value
    POWERTOOLS_SERVICE_NAME    = "webhook"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

resource "aws_apigatewayv2_integration" "webhook" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.webhook_lambda.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.webhook.id}"
}

resource "aws_lambda_permission" "api_gw_webhook" {
  statement_id  = "AllowExecutionFromAPIGatewayWebhook"
  action        = "lambda:InvokeFunction"
  function_name = module.webhook_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*/webhook"
}

# --- 8. TRIGGER: Cleanup imagens órfãs (diário 03:00 UTC = meia-noite BRT) ---
module "cleanup_orphan_images_lambda" {
  source = "../../modules/lambda_function"

  function_name = "loja-omena-cleanup-orphan-images"
  handler       = "handler.lambda_handler"
  source_dir    = "../../../src/triggers/cleanup_orphan_images"
  timeout       = 60
  memory_size   = 192

  layers = [
    aws_lambda_layer_version.main_dependencies.arn,
    aws_lambda_layer_version.shared_code.arn
  ]

  environment_variables = {
    SUPABASE_URL            = data.aws_ssm_parameter.app["supabase_url"].value
    SUPABASE_KEY            = data.aws_ssm_parameter.app["supabase_key"].value
    POWERTOOLS_SERVICE_NAME = "cleanup-orphan-images"
  }

  tags = { Project = "LojaOmena", Env = "Prod" }
}

resource "aws_cloudwatch_event_rule" "cleanup_orphan_images_schedule" {
  name                = "loja-omena-cleanup-orphan-images"
  description         = "Executa limpeza de imagens órfãs diariamente às 13:13 BRT (16:13 UTC)"
  schedule_expression = "cron(13 16 * * ? *)"
}

resource "aws_cloudwatch_event_target" "cleanup_orphan_images" {
  rule      = aws_cloudwatch_event_rule.cleanup_orphan_images_schedule.name
  target_id = "CleanupOrphanImages"
  arn       = module.cleanup_orphan_images_lambda.arn
}

resource "aws_lambda_permission" "eventbridge_cleanup_orphan_images" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = module.cleanup_orphan_images_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cleanup_orphan_images_schedule.arn
}

# --- OUTPUT ---
output "api_url" {
  value = aws_apigatewayv2_api.main.api_endpoint
}