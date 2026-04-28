import sys
from pathlib import Path
from unittest.mock import MagicMock

# Garante que src e src/shared estejam no path
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root / "src"))

# Mock supabase para permitir import de shared.database (usado por repository)
# sem precisar instalar o pacote em ambiente de teste
if "supabase" not in sys.modules:
    sys.modules["supabase"] = MagicMock()

# Mock aws_lambda_powertools apenas quando o pacote nao estiver instalado.
try:
    import aws_lambda_powertools  # noqa: F401
except ImportError:
    _awsp = MagicMock()
    _awsp.Logger.return_value.inject_lambda_context = lambda f: f
    sys.modules["aws_lambda_powertools"] = _awsp
    sys.modules["aws_lambda_powertools.event_handler"] = MagicMock()
    sys.modules["aws_lambda_powertools.event_handler.api_gateway"] = MagicMock()
    sys.modules["aws_lambda_powertools.utilities"] = MagicMock()
    sys.modules["aws_lambda_powertools.utilities.parser"] = MagicMock()
    sys.modules["aws_lambda_powertools.utilities.typing"] = MagicMock()

# Mock mercadopago para testes de payment
if "mercadopago" not in sys.modules:
    _mp = MagicMock()
    _mp.config.RequestOptions = MagicMock
    sys.modules["mercadopago"] = _mp
