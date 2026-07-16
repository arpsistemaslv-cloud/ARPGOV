"""
Anexos genéricos da intranet (qualquer módulo): upload, listagem e download.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import abort, flash, redirect, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from models import EmpresaAnexo, db


def _safe_empresa_next(nxt: str | None, default: str) -> str:
    try:
        from app import _safe_internal_redirect

        return _safe_internal_redirect(nxt, default, ())
    except Exception:
        t = (nxt or "").strip()
        if t.startswith("/") and not t.startswith("//"):
            return t
        return default


# contexto deve coincidir com o usado nos templates
CONTEXTOS_VALIDOS = frozenset(
    {
        "produto",
        "empenho",
        "remessa",
        "chamado_tecnico",
        "processo_juridico",
        "protocolo_garantia",
        "contrato",
        "nota_fiscal",
        "pendencia",
        "projeto",
        "licitacao",
        "fechamento_preco",
        "imposto_uf",
    }
)

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
MAX_BYTES = 15 * 1024 * 1024


def listar_anexos(contexto: str, ref_id: int) -> list[EmpresaAnexo]:
    return (
        EmpresaAnexo.query.filter_by(contexto=contexto, ref_id=ref_id)
        .order_by(EmpresaAnexo.id.desc())
        .all()
    )


def register_anexos_routes(bp, app) -> None:
    from empresa_intranet import _current_employee, empresa_login_required

    upload_root = Path(app.root_path) / "static" / "uploads" / "empresa_anexos"
    upload_root.mkdir(parents=True, exist_ok=True)

    @bp.route("/anexos/upload", methods=["POST"])
    @empresa_login_required
    def anexos_upload():
        ctx = (request.form.get("contexto") or "").strip()
        ref_id = request.form.get("ref_id", type=int)
        nxt = _safe_empresa_next(
            request.form.get("next"), url_for("empresa.dashboard")
        )
        if ctx not in CONTEXTOS_VALIDOS or not ref_id or ref_id < 1:
            flash("Anexo: dados inválidos.", "error")
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
            flash("Arquivo grande demais (máx. 15 MB).", "error")
            return redirect(nxt)
        sub = upload_root / ctx / str(ref_id)
        sub.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex[:16]}_{raw}"
        path = sub / fname
        f.save(str(path))
        rel = f"uploads/empresa_anexos/{ctx}/{ref_id}/{fname}".replace("\\", "/")
        user = _current_employee()
        row = EmpresaAnexo(
            contexto=ctx,
            ref_id=ref_id,
            nome_original=raw[:260],
            caminho_relativo=rel,
            mime_type=f.mimetype,
            created_by_id=user.id if user else None,
        )
        db.session.add(row)
        db.session.commit()
        flash("Arquivo enviado.", "ok")
        return redirect(nxt)

    @bp.route("/anexos/<int:aid>/excluir", methods=["POST"])
    @empresa_login_required
    def anexos_delete(aid: int):
        row = EmpresaAnexo.query.get_or_404(aid)
        nxt = _safe_empresa_next(
            request.form.get("next"), url_for("empresa.dashboard")
        )
        ctx = row.contexto
        rid = row.ref_id
        try:
            fp = Path(app.root_path) / "static" / row.caminho_relativo.replace("/", os.sep)
            if fp.is_file():
                fp.unlink()
        except OSError:
            pass
        db.session.delete(row)
        db.session.commit()
        flash("Anexo removido.", "ok")
        return redirect(nxt)

    @bp.route("/anexos/<int:aid>/download")
    @empresa_login_required
    def anexos_download(aid: int):
        row = EmpresaAnexo.query.get_or_404(aid)
        rel = (row.caminho_relativo or "").replace("\\", "/").strip()
        if not rel.startswith("uploads/empresa_anexos/"):
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
