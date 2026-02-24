from fastapi import FastAPI

from app.core.database import supabase
from app.modules.submissions import router as submissions_router

app = FastAPI(title="Sunbeat Core API", version="0.1.0")


@app.get("/")
def root():
    return {"ok": True, "service": "sunbeat-core-api"}


# IMPORTANTE:
# Esta rota precisa ser registrada ANTES das rotas dinâmicas /{draft_token}
# senão o FastAPI pode interpretar "test-supabase" como draft_token.
@app.get("/test-supabase")
def test_supabase():
    # Teste simples: lista buckets (Storage)
    # Se falhar aqui, o problema é credencial/permissão.
    buckets = supabase.storage.list_buckets()
    return {"ok": True, "buckets": buckets}


# Rotas do produto
app.include_router(submissions_router)