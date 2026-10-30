from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core.forms import StyledForm
from apps.core.permissions import hr_required
from apps.core.utils import paginate

from . import services
from .models import SYSTEM_FIELDS, AttendanceImportBatch, AttendanceImportRow, ImportFormat, ImportTemplate

RS = AttendanceImportRow.RowStatus


class UploadForm(StyledForm):
    file = forms.FileField(label="Attendance workbook (.xlsx)", help_text="Maximum 10 MB. Only .xlsx is accepted.",
                           widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}))
    template = forms.ModelChoiceField(ImportTemplate.objects.all(), required=False, empty_label="No saved template — map columns manually",
                                      label="Saved column mapping")


class MappingForm(StyledForm):
    sheet_name = forms.ChoiceField(label="Worksheet")
    header_row = forms.IntegerField(min_value=1, max_value=50, initial=1, help_text="Row number that contains the column headings.")
    format_mode = forms.ChoiceField(label="File layout", choices=ImportFormat.choices)
    dayfirst = forms.BooleanField(required=False, initial=True, label="Dates are day-first (DD/MM/YYYY)")
    save_template_as = forms.CharField(required=False, max_length=100, label="Save this mapping as a template (optional)")

    def __init__(self, *args, sheets=(), columns=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sheet_name"].choices = [(s, s) for s in sheets]
        choices = [("", "— not in this file —")] + [(c, c) for c in columns]
        for key, label, required in SYSTEM_FIELDS:
            self.fields[f"map_{key}"] = forms.ChoiceField(label=label + (" *" if required else ""), choices=choices, required=False,
                                                          widget=forms.Select(attrs={"class": "form-select"}))

    def mapping(self):
        return {k: self.cleaned_data.get(f"map_{k}") for k, _, _ in SYSTEM_FIELDS if self.cleaned_data.get(f"map_{k}")}


@hr_required
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            batch = services.create_batch(form.cleaned_data["file"], request.user)
        except ValidationError as e:
            form.add_error("file", e)
        else:
            tpl = form.cleaned_data.get("template")
            if tpl:
                batch.template, batch.format_mode, batch.mapping = tpl, tpl.format_mode, tpl.mapping
                batch.header_row, batch.dayfirst = tpl.header_row, tpl.dayfirst
                batch.save()
            if batch.same_file_batch:
                messages.warning(request, f"This exact file was already imported as {batch.same_file_batch.batch_code}. "
                                          "Rows already imported will be detected as duplicates.")
            return redirect("imports:mapping", pk=batch.pk)
    recent = AttendanceImportBatch.objects.select_related("uploaded_by")[:5]
    return render(request, "imports/upload.html", {"form": form, "recent": recent})


@hr_required
def mapping(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    if batch.is_final:
        return redirect("imports:results", pk=batch.pk)
    if request.GET.get("sheet") in batch.sheet_names:
        batch.sheet_name = request.GET["sheet"]
    if request.GET.get("header_row", "").isdigit():
        batch.header_row = max(1, min(50, int(request.GET["header_row"])))
    try:
        columns, sample = services.detect_columns(batch)
    except ValidationError as e:
        columns, sample = [], None
        messages.error(request, " ".join(e.messages))
    initial = {"sheet_name": batch.sheet_name, "header_row": batch.header_row, "format_mode": batch.format_mode or ImportFormat.DAILY,
               "dayfirst": batch.dayfirst}
    current = batch.mapping or services.suggest_mapping(columns)
    for k, _, _ in SYSTEM_FIELDS:
        if current.get(k) in columns:
            initial[f"map_{k}"] = current[k]
    form = MappingForm(request.POST or None, sheets=batch.sheet_names, columns=columns, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        batch.sheet_name, batch.header_row, batch.format_mode, batch.dayfirst = d["sheet_name"], d["header_row"], d["format_mode"], d["dayfirst"]
        batch.mapping = form.mapping()
        batch.save()
        try:
            services.validate_mapping(batch.format_mode, batch.mapping, batch.detected_columns)
            if d.get("save_template_as"):
                tpl, _ = ImportTemplate.objects.update_or_create(name=d["save_template_as"], defaults=dict(
                    format_mode=batch.format_mode, mapping=batch.mapping, header_row=batch.header_row, dayfirst=batch.dayfirst, created_by=request.user))
                batch.template = tpl
                batch.save(update_fields=["template"])
                audit.log(request.user, "import.template_saved", tpl, after=tpl.mapping)
            services.validate_batch(batch, request.user)
            return redirect("imports:preview", pk=batch.pk)
        except ValidationError as e:
            form.add_error(None, e)
    sample_rows = sample.fillna("").astype(str).values.tolist() if sample is not None else []
    return render(request, "imports/mapping.html", {"batch": batch, "form": form, "columns": columns, "sample_rows": sample_rows,
                                                    "system_fields": SYSTEM_FIELDS})


@hr_required
def preview(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    if batch.status != AttendanceImportBatch.Status.VALIDATED:
        return redirect("imports:results" if batch.is_final else "imports:mapping", pk=batch.pk)
    rows = batch.rows.select_related("employee")
    f = request.GET.get("show", "problems")
    if f == "problems":
        rows = rows.exclude(status=RS.VALID)
    elif f in RS.values:
        rows = rows.filter(status=f)
    employees = batch.rows.exclude(employee=None).values("employee").distinct().count()
    unknown = sorted(set(batch.rows.filter(status=RS.ERROR, employee=None).exclude(employee_code="").values_list("employee_code", flat=True)))[:30]
    return render(request, "imports/preview.html", {"batch": batch, "page_obj": paginate(request, rows, 50), "show": f,
                                                    "employees": employees, "unknown_ids": unknown})


@hr_required
@require_POST
def confirm(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    if request.POST.get("discard"):
        try:
            services.discard_batch(batch, request.user)
            messages.info(request, "Import discarded. Nothing was imported.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("imports:history")
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the confirmation box to import.")
        return redirect("imports:preview", pk=batch.pk)
    try:
        batch = services.import_batch(batch, request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect("imports:results", pk=batch.pk)
    if batch.status == AttendanceImportBatch.Status.FAILED:
        messages.error(request, f"Import failed and was fully rolled back — nothing was imported. {batch.failure_message}")
    else:
        messages.success(request, f"{batch.batch_code}: {batch.imported_rows} rows imported ({batch.punches_created} punches). Attendance recalculated.")
    return redirect("imports:results", pk=batch.pk)


@hr_required
def results(request, pk):
    batch = get_object_or_404(AttendanceImportBatch.objects.select_related("uploaded_by", "confirmed_by", "rolled_back_by"), pk=pk)
    rows = batch.rows.select_related("employee")
    show = request.GET.get("show", "")
    if show in RS.values:
        rows = rows.filter(status=show)
    return render(request, "imports/results.html", {"batch": batch, "page_obj": paginate(request, rows, 50), "show": show,
                                                    "row_statuses": RS.choices, "conflicts": batch.rows.filter(status=RS.CONFLICT)[:100]})


@hr_required
@require_POST
def reconcile(request, pk, row_id):
    row = get_object_or_404(AttendanceImportRow, pk=row_id, batch_id=pk)
    try:
        services.reconcile_row(row, request.user, request.POST.get("decision") == "accept", request.POST.get("note", ""))
        messages.success(request, f"Row {row.row_number} reconciled.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("imports:results", pk=pk)


@hr_required
@require_POST
def retry(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    try:
        n = services.retry_rejected_rows(batch, request.user)
        messages.success(request, f"{n} previously rejected row(s) are now valid and were imported." if n else "No rejected rows became valid.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("imports:results", pk=pk)


@hr_required
@require_POST
def rollback(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    try:
        n = services.rollback_batch(batch, request.user, request.POST.get("reason", ""))
        messages.success(request, f"Import rolled back: {n} punches voided (kept for audit). Attendance recalculated.")
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("imports:results", pk=pk)


@hr_required
def error_report(request, pk):
    batch = get_object_or_404(AttendanceImportBatch, pk=pk)
    audit.log(request.user, "import.error_report_downloaded", batch)
    return FileResponse(services.error_report_workbook(batch), as_attachment=True, filename=f"{batch.batch_code}_results.xlsx")


@hr_required
def history(request):
    qs = AttendanceImportBatch.objects.select_related("uploaded_by").order_by("-created_at")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    return render(request, "imports/history.html", {"page_obj": paginate(request, qs), "statuses": AttendanceImportBatch.Status.choices,
                                                    "templates": ImportTemplate.objects.all()})


@hr_required
def sample(request):
    return FileResponse(services.sample_workbook(), as_attachment=True, filename="attendance_import_sample.xlsx")
