"""Endpoints para servir documentação Markdown do repo (whitelist).

A whitelist evita path traversal e permite curar a ordem/títulos exibidos
na UI. Conteúdo retornado em texto cru — o frontend renderiza markdown +
sanitiza antes de injetar no DOM (defesa em profundidade).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from megalog.api.deps import CurrentUser, get_current_user

# Resolve a raiz do repo a partir deste arquivo:
# megalog/api/routes/docs.py → parents[3] = /usr/local/src/megalog/
REPO_ROOT = Path(__file__).resolve().parents[3]

# (id, título exibido, caminho relativo ao repo)
_DOC_LIST: list[tuple[str, str, str]] = [
    ("readme",          "Visão geral",          "README.md"),
    ("install",         "Instalação",           "docs/INSTALL.md"),
    ("manual",          "Manual de uso",        "docs/MANUAL.md"),
    ("operations",      "Operação",             "docs/OPERATIONS.md"),
    ("troubleshoot",    "Troubleshooting",      "docs/TROUBLESHOOTING.md"),
    ("security",        "Segurança",            "docs/SECURITY.md"),
    ("api",             "API",                  "docs/API.md"),
    ("architecture",    "Arquitetura",          "docs/ARCHITECTURE.md"),
    ("changelog-v5",    "Changelog v4 → v5",    "docs/CHANGELOG-v4-to-v5.md"),
]

DOCS: dict[str, tuple[str, Path]] = {
    doc_id: (title, REPO_ROOT / rel_path)
    for doc_id, title, rel_path in _DOC_LIST
}


router = APIRouter(prefix="/api/docs", tags=["docs"])


class DocSummary(BaseModel):
    id: str
    title: str
    size_bytes: int


class DocContent(BaseModel):
    id: str
    title: str
    content: str


@router.get("", response_model=list[DocSummary])
def list_docs(user: CurrentUser = Depends(get_current_user)):
    out: list[DocSummary] = []
    for doc_id, (title, path) in DOCS.items():
        if path.is_file():
            out.append(DocSummary(
                id=doc_id, title=title, size_bytes=path.stat().st_size,
            ))
    return out


@router.get("/{doc_id}", response_model=DocContent)
def get_doc(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    if doc_id not in DOCS:
        raise HTTPException(status_code=404, detail="Documento não cadastrado")
    title, path = DOCS[doc_id]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo ausente no servidor")
    return DocContent(id=doc_id, title=title, content=path.read_text(encoding="utf-8"))
