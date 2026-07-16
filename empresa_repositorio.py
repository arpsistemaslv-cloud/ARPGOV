"""
Repositório central de documentos da empresa (upload, busca por categoria, download).
Arquivos em static/uploads/empresa_repositorio/
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from models import EmpresaDocumentoRepositorio, db


def _safe_empresa_next(nxt: str | None, default: str) -> str:
    try:
        from app import _safe_internal_redirect

        return _safe_internal_redirect(nxt, default, ())
    except Exception:
        t = (nxt or "").strip()
        if t.startswith("/") and not t.startswith("//"):
            return t
        return default


EXT_OK = frozenset(
    {
        ".pdf",
        ".xml",
        ".zip",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".csv",
        ".txt",
        ".odt",
        ".ods",
    }
)
MAX_BYTES = 25 * 1024 * 1024

# Sugestões de categoria (o usuário pode digitar outra)
CATEGORIAS_SUGESTAO: tuple[str, ...] = (
    "Certidões e regularidade fiscal",
    "Procurações e poderes",
    "Ata / contrato social",
    "Balanço e demonstrações",
    "Qualificação técnica / ATPC",
    "Modelos de proposta",
    "Outros",
)


def register_repositorio_routes(bp, app) -> None:
    from empresa_intranet import _current_employee, empresa_login_required

    upload_root = Path(app.root_path) / "static" / "uploads" / "empresa_repositorio"
    upload_root.mkdir(parents=True, exist_ok=True)

    @bp.route("/documentos/")
    @empresa_login_required
    def documentos_repositorio_hub():
        q = (request.args.get("q") or "").strip()
        cat = (request.args.get("categoria") or "").strip()
        query = EmpresaDocumentoRepositorio.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    EmpresaDocumentoRepositorio.titulo.ilike(like),
                    EmpresaDocumentoRepositorio.descricao.ilike(like),
                    EmpresaDocumentoRepositorio.nome_original.ilike(like),
                )
            )
        if cat:
            query = query.filter(EmpresaDocumentoRepositorio.categoria == cat)
        rows = query.order_by(EmpresaDocumentoRepositorio.id.desc()).limit(500).all()
        cats_rows = (
            db.session.query(EmpresaDocumentoRepositorio.categoria)
            .filter(EmpresaDocumentoRepositorio.categoria.isnot(None))
            .distinct()
            .order_by(EmpresaDocumentoRepositorio.categoria)
            .limit(100)
            .all()
        )
        categorias_usadas = [r[0] for r in cats_rows if r[0]]
        n = EmpresaDocumentoRepositorio.query.count()
        return render_template(
            "empresa/documentos/repositorio.html",
            staff=_current_employee(),
            documentos=rows,
            q=q,
            categoria_filtro=cat,
            categorias_usadas=categorias_usadas,
            categorias_sugestao=CATEGORIAS_SUGESTAO,
            n_documentos=n,
        )

    @bp.route("/documentos/upload", methods=["POST"])
    @empresa_login_required
    def documentos_repositorio_upload():
        nxt = _safe_empresa_next(
            request.form.get("next"),
            url_for("empresa.documentos_repositorio_hub"),
        )
        titulo = (request.form.get("titulo") or "").strip()
        categoria = (request.form.get("categoria") or "").strip()[:120] or None
        descricao = (request.form.get("descricao") or "").strip() or None
        if not titulo:
            flash("Informe o título do documento.", "error")
            return redirect(nxt)
        f = request.files.get("arquivo")
        if not f or not f.filename:
            flash("Selecione um arquivo.", "error")
            return redirect(nxt)
        raw = secure_filename(f.filename)
        if not raw:
            flash("Nome de arquivo inválido.", "error")
            return redirect(nxt)
        ext = Path(raw).suffix.lower()
        if ext not in EXT_OK:
            flash("Tipo de arquivo não permitido.", "error")
            return redirect(nxt)
        f.stream.seek(0, os.SEEK_END)
        sz = f.stream.tell()
        f.stream.seek(0)
        if sz > MAX_BYTES:
            flash("Arquivo grande demais (máx. 25 MB).", "error")
            return redirect(nxt)
        sub = upload_root / "docs"
        sub.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex[:16]}_{raw}"
        path = sub / fname
        f.save(str(path))
        rel = f"uploads/empresa_repositorio/docs/{fname}".replace("\\", "/")
        user = _current_employee()
        row = EmpresaDocumentoRepositorio(
            titulo=titulo[:400],
            categoria=categoria,
            descricao=descricao,
            nome_original=raw[:260],
            caminho_relativo=rel,
            mime_type=f.mimetype,
            created_by_id=user.id if user else None,
        )
        db.session.add(row)
        db.session.commit()
        flash("Documento publicado no repositório.", "ok")
        return redirect(nxt)

    @bp.route("/documentos/<int:did>/excluir", methods=["POST"])
    @empresa_login_required
    def documentos_repositorio_delete(did: int):
        row = EmpresaDocumentoRepositorio.query.get_or_404(did)
        nxt = _safe_empresa_next(
            request.form.get("next"),
            url_for("empresa.documentos_repositorio_hub"),
        )
        try:
            fp = Path(app.root_path) / "static" / row.caminho_relativo.replace("/", os.sep)
            if fp.is_file():
                fp.unlink()
        except OSError:
            pass
        db.session.delete(row)
        db.session.commit()
        flash("Documento removido do repositório.", "ok")
        return redirect(nxt)

    @bp.route("/documentos/<int:did>/download")
    @empresa_login_required
    def documentos_repositorio_download(did: int):
        row = EmpresaDocumentoRepositorio.query.get_or_404(did)
        rel = (row.caminho_relativo or "").replace("\\", "/").strip()
        if not rel.startswith("uploads/empresa_repositorio/"):
            abort(404)
        directory = os.path.join(app.root_path, "static", os.path.dirname(rel))
        fname = os.path.basename(rel)
        if not os.path.isfile(os.path.join(directory, fname)):
            abort(404)
        return send_from_directory(
            directory,
            fname,
            as_attachment=True,
            download_name=row.nome_original or fname,
        )
