"""Tidying the files of a library: renaming and moving books by a scheme, with a preview and a way back.

**Optional, and off from the start.** nexbooks reads the libraries and changes nothing in them. The operator can
turn tidying on (``organize_enabled``); then, and only for the operator, a library can be tidied by a scheme:
1. *preview* (``preview``): the plan is made and kept as a run in the state ``preview`` - nothing on the disk is
  touched;
2. *start* (``start``, after the operator confirmed): the plan is made once more and must be the one that was
  shown (its digest is the same: ``preview_stale`` else), then a job does exactly that (``run.py``), book by
  book, in the background;
3. *undo* (``undo``): the moves of the run are put back, newest first.

One run at a time for a library, and a run only while the library is not being read (both are jobs of the same
lane, so they take turns). The libraries are mounted read-only from the start (``:ro``): the interface says so
(``status``) instead of offering what cannot work.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...errors import error
from ...models import Library, OrganizeMove, OrganizeRun
from .. import jobs, settings_service
from . import plan as planning
from . import run as running
from . import scheme as schemes

#: Runs kept for a library (the newest); older ones, with their journals, are forgotten.
KEEP_RUNS = 10
ACTIVE = ("queued", "running", "undoing")
MOVES_PER_PAGE = 100


def enabled(db: Session) -> bool:
    return bool(settings_service.get(db, "organize_enabled"))


def require_enabled(db: Session) -> None:
    if not enabled(db):
        raise error("organize_off", "Tidying is switched off.", 409)


def scheme_error(exc: schemes.SchemeError) -> Exception:
    return error("scheme_invalid", "That scheme cannot be used.", 422, reason=exc.code, detail=exc.detail,
                 field="scheme")


def active_run(db: Session, library_id: int) -> OrganizeRun | None:
    return db.scalar(select(OrganizeRun).where(OrganizeRun.library_id == library_id, OrganizeRun.state.in_(ACTIVE))
                     .order_by(OrganizeRun.id.desc()).limit(1))


def runs_of(db: Session, library_id: int, limit: int = KEEP_RUNS) -> list[OrganizeRun]:
    return list(db.scalars(select(OrganizeRun).where(OrganizeRun.library_id == library_id)
                           .order_by(OrganizeRun.id.desc()).limit(limit)))


def status(db: Session, library: Library) -> dict[str, object]:
    """Whether tidying is on, whether nexbooks may write in the library, and what runs there."""
    ok, why = planning.writable(library.path)
    return {"enabled": enabled(db), "writable": ok, "reason": why, "scheme": schemes.DEFAULT,
            "active": active_run(db, library.id)}


def _moving(db: Session, library_id: int) -> bool:
    """Whether an edition is being moved into or out of the library (``services/relocate.py``): tidying waits."""
    from ..relocate import busy_library

    return busy_library(db, library_id)


def preview(db: Session, library: Library, scheme_text: str, operator_id: int) -> OrganizeRun:
    """Makes the plan and keeps it as a run in the state ``preview`` (the
    older previews of the library go). Nothing on the disk changes."""
    require_enabled(db)
    if active_run(db, library.id) is not None or _moving(db, library.id):
        raise error("organize_busy", "A tidying of this library is running.", 409)
    try:
        plan = planning.build(db, library, scheme_text)
    except schemes.SchemeError as exc:
        raise scheme_error(exc) from exc
    scheme = schemes.parse(scheme_text).text
    db.execute(delete(OrganizeRun).where(OrganizeRun.library_id == library.id, OrganizeRun.state == "preview"))
    run = OrganizeRun(library_id=library.id, created_by=operator_id, scheme=scheme, state="preview",
                      summary=plan.summary(), total=sum(1 for m in plan.moves if m.state == "planned"))
    db.add(run)
    db.flush()
    db.add_all(OrganizeMove(run_id=run.id, file_id=m.file_id, edition_id=m.edition_id, source=m.source,
                            target=m.target, state=m.state, reason=m.reason, size=m.size, mtime_ns=m.mtime_ns)
               for m in plan.moves)
    old = db.scalars(select(OrganizeRun.id).where(OrganizeRun.library_id == library.id)
                     .order_by(OrganizeRun.id.desc()).offset(KEEP_RUNS)).all()
    if old:
        db.execute(delete(OrganizeRun).where(OrganizeRun.id.in_(old)))
    db.commit()
    return run


def load(db: Session, run_id: int) -> OrganizeRun:
    run = db.get(OrganizeRun, run_id)
    if run is None:
        raise error("not_found", "Not found.", 404)
    return run


def start(db: Session, run: OrganizeRun) -> OrganizeRun:
    """Starts a previewed run, after the operator confirmed it."""
    require_enabled(db)
    library = db.get(Library, run.library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    if run.state != "preview":
        raise error("organize_state", "This tidying was started already.", 409)
    if active_run(db, library.id) is not None or _moving(db, library.id):
        raise error("organize_busy", "A tidying of this library is running.", 409)
    ok, why = planning.writable(library.path)
    if not ok:
        raise error("organize_not_writable", "nexbooks may not write in this library.", 409, reason=why)
    try:
        again = planning.build(db, library, run.scheme)
    except schemes.SchemeError as exc:
        raise scheme_error(exc) from exc
    shown = run.summary.get("digest") if isinstance(run.summary, dict) else None
    if again.digest != shown:
        raise error("preview_stale", "The library changed since the preview. Make a new preview.", 409)
    if run.total == 0:
        raise error("organize_nothing", "There is nothing to move.", 409)
    run.state = "queued"
    run.done = 0
    jobs.enqueue(db, running.KIND, ref=run.id)
    db.commit()
    return run


def undo(db: Session, run: OrganizeRun) -> OrganizeRun:
    """Puts back what a run moved. Only the newest finished run
    of a library can be undone (an older one would meet files
    that a newer one moved), and only while nothing runs there."""
    require_enabled(db)
    if run.state not in ("done", "failed"):
        raise error("organize_state", "This tidying cannot be undone now.", 409)
    if active_run(db, run.library_id) is not None or _moving(db, run.library_id):
        raise error("organize_busy", "A tidying of this library is running.", 409)
    newer = db.scalar(select(OrganizeRun.id).where(OrganizeRun.library_id == run.library_id, OrganizeRun.id > run.id,
                                                   OrganizeRun.state.in_(("done", "failed"))).limit(1))
    if newer is not None:
        raise error("organize_not_latest", "A later tidying has to be undone first.", 409)
    library = db.get(Library, run.library_id)
    if library is None:
        raise error("not_found", "Not found.", 404)
    ok, why = planning.writable(library.path)
    if not ok:
        raise error("organize_not_writable", "nexbooks may not write in this library.", 409, reason=why)
    if not db.scalar(select(func.count(OrganizeMove.id)).where(OrganizeMove.run_id == run.id,
                                                               OrganizeMove.state == "moved")):
        raise error("organize_nothing", "There is nothing to put back.", 409)
    run.state = "undoing"
    run.error = ""
    jobs.enqueue(db, running.KIND, ref=run.id)
    db.commit()
    return run


def discard(db: Session, run: OrganizeRun) -> None:
    """Throws a preview away (a run that was started stays: it is the journal of what happened)."""
    if run.state != "preview":
        raise error("organize_state", "Only a preview can be thrown away.", 409)
    db.delete(run)
    db.commit()


def moves(db: Session, run: OrganizeRun, state: str | None, offset: int, limit: int) -> tuple[list[OrganizeMove], int]:
    filters = [OrganizeMove.run_id == run.id]
    if state:
        filters.append(OrganizeMove.state == state)
    total = db.scalar(select(func.count(OrganizeMove.id)).where(*filters)) or 0
    rows = db.scalars(select(OrganizeMove).where(*filters).order_by(OrganizeMove.id).offset(offset).limit(limit))
    return list(rows), int(total)

