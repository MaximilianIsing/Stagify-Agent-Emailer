import base64
import logging
import os
import secrets

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import DATA_DIR, load_config, normalize_password
from csv_store import delete_csv, get_csv, get_parse_report, list_csvs, reconcile_csv_registry, save_upload, set_csv_active
from debug_settings import load_debug_settings, save_debug_settings
from row_range import format_row_range
from draft_store import (
    DISCARDABLE_STATUSES,
    RESTAGEABLE_STATUSES,
    count_by_status,
    discard_draft,
    get_draft,
    get_email_html,
    get_original_image,
    get_staged_data,
    list_drafts,
    reject_all_pending,
    restage_all_failed,
    restage_all_rejected,
    restage_draft,
    save_original_image,
    update_draft,
)
from draft_worker import get_worker_state, start_worker
from email_engine import (
    build_html_email,
    download_listing_image,
    extract_b64_and_mime,
    send_email,
)
from sent_registry import (
    DuplicateSendError,
    finalize_send,
    list_sent,
    release_reservation,
    reserve_send,
    sent_count,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.filters["format_row_range"] = format_row_range

CSV_PURGE_CONFIRM_PHRASE = "DELETE PERMANENTLY"


def _app_config():
    return load_config()


app.secret_key = _app_config()["session_secret"]


def login_required(view):
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = normalize_password(request.form.get("password", ""))
        expected = _app_config()["admin_password"]
        if password and secrets.compare_digest(password, expected):
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        flash("Invalid password.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    status_filter = request.args.get("status", "pending")
    if status_filter == "all":
        drafts = list_drafts()
    else:
        drafts = list_drafts(status=status_filter)

    return render_template(
        "dashboard.html",
        drafts=drafts,
        counts=count_by_status(),
        sent_records=list_sent()[:50],
        sent_total=sent_count(),
        worker=get_worker_state(),
        status_filter=status_filter,
        debug_settings=load_debug_settings(),
        csv_uploads=list_csvs(),
        active_nav="dashboard",
    )


@app.post("/settings/debug")
@login_required
def update_debug_settings():
    enabled = request.form.get("debug_enabled") == "on"
    email = request.form.get("debug_email", "").strip()
    try:
        save_debug_settings(enabled, email)
        if enabled:
            flash(f"Debug mode on — emails will go to {email}.", "success")
        else:
            flash("Debug mode off — emails will go to real recipients.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard", status=request.form.get("status", "pending")))


@app.post("/csvs/upload")
@login_required
def upload_csv():
    file = request.files.get("csv_file")
    try:
        entry = save_upload(
            file,
            row_start=request.form.get("row_start", ""),
            row_end=request.form.get("row_end", ""),
            row_start_bound=request.form.get("row_start_bound", "inclusive"),
            row_end_bound=request.form.get("row_end_bound", "inclusive"),
        )
        msg = (
            f"Uploaded {entry['original_name']} — "
            f"{entry['total_row_count']} row(s) in file, "
            f"{entry['valid_row_count']} valid, "
            f"{entry['selected_row_count']} selected"
        )
        if entry.get("skipped_row_count"):
            msg += f", {entry['skipped_row_count']} skipped (see errors)"
        msg += ". Draft worker will process it shortly."
        flash(msg, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("CSV upload failed")
        flash(f"Upload failed: {exc}", "error")
    return redirect(url_for("dashboard", status=request.form.get("status", "pending")))


@app.post("/csvs/<csv_id>/toggle")
@login_required
def toggle_csv(csv_id):
    entry = next((c for c in list_csvs() if c["id"] == csv_id), None)
    if not entry:
        flash("CSV not found.", "error")
        return redirect(url_for("dashboard"))
    try:
        set_csv_active(csv_id, not entry.get("active", False))
        state = "active" if not entry.get("active") else "inactive"
        flash(f"Marked {entry['original_name']} as {state}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard", status=request.form.get("status", "pending")))


@app.post("/csvs/<csv_id>/delete")
@login_required
def remove_csv(csv_id):
    entry = next((c for c in list_csvs() if c["id"] == csv_id), None)
    if not entry:
        flash("CSV not found.", "error")
        return redirect(url_for("dashboard"))
    flash(
        f"Use CSV History to delete {entry['original_name']} — "
        "deletion requires full confirmation.",
        "error",
    )
    return redirect(url_for("csv_history"))


@app.route("/csvs/history")
@login_required
def csv_history():
    return render_template(
        "csv_history.html",
        csv_uploads=list_csvs(),
        active_nav="csvs",
        confirm_phrase=CSV_PURGE_CONFIRM_PHRASE,
    )


@app.post("/csvs/<csv_id>/purge")
@login_required
def purge_csv(csv_id):
    entry = get_csv(csv_id)
    if not entry:
        flash("CSV not found.", "error")
        return redirect(url_for("csv_history"))

    confirm_name = request.form.get("confirm_name", "").strip()
    confirm_phrase = request.form.get("confirm_phrase", "").strip()

    if confirm_phrase != CSV_PURGE_CONFIRM_PHRASE:
        flash(
            f'Confirmation phrase incorrect. Type "{CSV_PURGE_CONFIRM_PHRASE}" exactly.',
            "error",
        )
        return redirect(url_for("csv_history"))

    if confirm_name != entry["original_name"]:
        flash("Filename does not match. Type the exact filename to confirm.", "error")
        return redirect(url_for("csv_history"))

    try:
        delete_csv(csv_id)
        flash(
            f'Removed "{entry["original_name"]}" from history. You may upload it again.',
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("csv_history"))


@app.route("/csvs/<csv_id>/errors")
@login_required
def csv_errors(csv_id):
    entry = get_csv(csv_id)
    if not entry:
        abort(404)
    report = get_parse_report(csv_id)
    if not report:
        abort(404)
    return render_template(
        "csv_errors.html",
        csv_entry=entry,
        skipped=report.get("skipped", []),
        valid_count=len(report.get("rows", [])),
        active_nav="csvs",
    )


@app.route("/drafts/<draft_id>")
@login_required
def draft_detail(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        abort(404)
    html = get_email_html(draft_id)
    return render_template(
        "draft_detail.html",
        draft=draft,
        preview_html=html,
        debug_settings=load_debug_settings(),
    )


@app.route("/drafts/<draft_id>/preview")
@login_required
def draft_preview(draft_id):
    html = get_email_html(draft_id)
    if not html:
        abort(404)
    return html


@app.route("/drafts/<draft_id>/image")
@login_required
def draft_image(draft_id):
    staged = get_staged_data(draft_id)
    if not staged:
        abort(404)
    raw_b64, mime = extract_b64_and_mime(staged)
    return base64.b64decode(raw_b64), 200, {"Content-Type": mime}


@app.route("/drafts/<draft_id>/original")
@login_required
def draft_original_image(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        abort(404)

    content, mime = get_original_image(draft_id)
    if content is None and draft.get("image_url"):
        downloaded = download_listing_image(draft["image_url"])
        if downloaded:
            content, mime, _ = downloaded
            save_original_image(draft_id, content, mime)

    if content is None:
        abort(404)
    return content, 200, {"Content-Type": mime}


def _send_draft_email(draft_id, draft):
    """Send a draft. Returns: sent, debug, duplicate, failed, missing_staged, config_error."""
    staged = get_staged_data(draft_id)
    if not staged:
        flash("Staged image missing.", "error")
        return "missing_staged"

    html = build_html_email(draft["name"], draft["address"], email=draft["email"])
    debug_settings = load_debug_settings()

    try:
        if debug_settings["enabled"]:
            if not debug_settings["email"]:
                flash("Debug mode is on but no debug email is set.", "error")
                return "config_error"
            result = send_email(draft["email"], draft["subject"], html, staged)
            update_draft(
                draft_id,
                last_debug_sent_at=result.get("created_at"),
                last_debug_to=debug_settings["email"],
                error=None,
            )
            flash(
                f"Debug send to {debug_settings['email']} "
                f"(not sent to {draft['email']}).",
                "success",
            )
            return "debug"

        reserve_send(draft["email"], draft["address"], draft_id, draft["name"])
        result = send_email(draft["email"], draft["subject"], html, staged)
        resend_id = result.get("id")
        finalize_send(draft["email"], draft["address"], resend_id)
        update_draft(
            draft_id,
            status="sent",
            sent_at=result.get("created_at"),
            resend_id=resend_id,
            error=None,
        )
        flash(f"Sent to {draft['email']}.", "success")
        return "sent"
    except DuplicateSendError as exc:
        update_draft(draft_id, status="skipped", error=str(exc))
        flash(str(exc), "error")
        return "duplicate"
    except Exception as exc:
        release_reservation(draft["email"], draft["address"])
        logger.exception("Send failed for %s", draft_id)
        update_draft(draft_id, status="failed", error=str(exc))
        flash(f"Send failed: {exc}", "error")
        return "failed"


@app.post("/drafts/<draft_id>/approve")
@login_required
def approve_draft(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        flash("Draft not found.", "error")
        return redirect(url_for("dashboard"))

    if draft["status"] != "pending":
        flash(f"Draft is already {draft['status']}.", "error")
        return redirect(url_for("dashboard"))

    _send_draft_email(draft_id, draft)
    return redirect(url_for("dashboard", status="pending"))


@app.post("/drafts/<draft_id>/send-anyway")
@login_required
def send_anyway_draft(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        flash("Draft not found.", "error")
        return redirect(url_for("dashboard", status="rejected"))

    return_to = request.form.get("return_to", "rejected")

    if draft["status"] != "rejected":
        flash(f"Only rejected drafts can be sent anyway (current: {draft['status']}).", "error")
        return redirect(url_for("dashboard", status=draft["status"]))

    result = _send_draft_email(draft_id, draft)
    if result == "sent":
        return redirect(url_for("dashboard", status="sent"))
    if return_to == "detail":
        return redirect(url_for("draft_detail", draft_id=draft_id))
    return redirect(url_for("dashboard", status="rejected"))


@app.post("/drafts/<draft_id>/reject")
@login_required
def reject_draft(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        flash("Draft not found.", "error")
        return redirect(url_for("dashboard"))

    if draft["status"] != "pending":
        flash(f"Draft is already {draft['status']}.", "error")
        return redirect(url_for("dashboard"))

    update_draft(draft_id, status="rejected", error=None)
    flash(f"Rejected draft for {draft['email']}.", "success")
    return redirect(url_for("dashboard", status="pending"))


@app.post("/drafts/<draft_id>/restage")
@login_required
def restage_draft_route(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        flash("Draft not found.", "error")
        return redirect(url_for("dashboard", status="rejected"))

    return_to = request.form.get("return_to", draft.get("status", "rejected"))

    if draft["status"] not in RESTAGEABLE_STATUSES:
        flash(
            f"Only rejected or failed drafts can be restaged (current: {draft['status']}).",
            "error",
        )
        return redirect(url_for("dashboard", status=draft["status"]))

    additional_prompt = request.form.get("additional_prompt", "")

    try:
        restage_draft(draft_id, additional_prompt)
        flash(
            f"Restaged {draft['email']} — moved back to pending for review.",
            "success",
        )
        return redirect(url_for("dashboard", status="pending"))
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        logger.exception("Restage failed for %s", draft_id)
        flash(f"Restage failed: {exc}", "error")

    if return_to == "detail":
        return redirect(url_for("draft_detail", draft_id=draft_id))
    return redirect(url_for("dashboard", status=return_to if return_to in RESTAGEABLE_STATUSES else "rejected"))


@app.post("/drafts/<draft_id>/discard")
@login_required
def discard_draft_route(draft_id):
    draft = get_draft(draft_id)
    if not draft:
        flash("Draft not found.", "error")
        return redirect(url_for("dashboard", status="rejected"))

    return_to = request.form.get("return_to", draft.get("status", "rejected"))

    if draft["status"] not in DISCARDABLE_STATUSES:
        flash(
            f"Only rejected or failed drafts can be discarded (current: {draft['status']}).",
            "error",
        )
        return redirect(url_for("dashboard", status=draft["status"]))

    try:
        discard_draft(draft_id)
        flash(f"Discarded draft for {draft['email']}.", "success")
        return redirect(url_for("dashboard", status="discarded"))
    except ValueError as exc:
        flash(str(exc), "error")

    if return_to == "detail":
        return redirect(url_for("draft_detail", draft_id=draft_id))
    return redirect(url_for("dashboard", status=return_to))


@app.post("/drafts/reject-all")
@login_required
def reject_all_drafts():
    count = reject_all_pending()
    if count:
        flash(f"Rejected {count} pending draft(s).", "success")
    else:
        flash("No pending drafts to reject.", "error")
    return redirect(url_for("dashboard", status="pending"))


@app.post("/drafts/restage-all")
@login_required
def restage_all_drafts():
    scope = request.form.get("scope", "rejected")
    if scope == "failed":
        restaged, errors = restage_all_failed()
        label = "failed"
    else:
        restaged, errors = restage_all_rejected()
        label = "rejected"

    if restaged:
        flash(
            f"Restaged {restaged} {label} draft(s) — moved to pending for review.",
            "success",
        )
    if errors:
        sample = "; ".join(f"{email}: {err}" for email, err in errors[:3])
        extra = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
        flash(f"{len(errors)} restage(s) failed. {sample}{extra}", "error")
    if not restaged and not errors:
        flash(f"No {label} drafts to restage.", "error")
    return redirect(url_for("dashboard", status="pending" if restaged else label))


def create_app():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    reconcile_csv_registry()
    start_worker()
    return app


create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
