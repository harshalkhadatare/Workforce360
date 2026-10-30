from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core.permissions import admin_required, hr_required
from apps.leave_management.models import LeaveType

from .defaults import DEFAULT_APPROVAL_CHAINS, DEFAULTS
from .forms import ApprovalChainForm, HolidayForm, LeaveTypeForm, PolicyVersionForm, ScheduleForm
from .models import ApprovalChainConfig, Holiday, PolicyVersion, WorkSchedule


def _unconfirmed(version):
    return [(k, s) for k, s in DEFAULTS[version.kind].items() if s["needs_hr"] and not version.is_confirmed(k)]


@hr_required
def index(request):
    versions = PolicyVersion.objects.prefetch_related("departments").select_related("created_by", "activated_by")
    groups = []
    for kind, label in PolicyVersion.Kind.choices:
        vs = [v for v in versions if v.kind == kind]
        active = [v for v in vs if v.status == PolicyVersion.Status.ACTIVE]
        current = max(active, key=lambda v: (v.effective_from <= timezone.localdate(), v.effective_from), default=None)
        groups.append({"kind": kind, "label": label, "versions": vs, "current": current,
                       "params": [(k, s, current.get(k), current.is_confirmed(k), (current.hr_confirmations or {}).get(k)) for k, s in DEFAULTS[kind].items()] if current else [],
                       "unconfirmed": len(_unconfirmed(current)) if current else 0})
    chains = {c.request_type: c.steps for c in ApprovalChainConfig.objects.all()}
    chain_rows = [(k, chains.get(k, v), k in chains) for k, v in DEFAULT_APPROVAL_CHAINS.items()]
    return render(request, "policies/index.html", {"groups": groups, "chain_rows": chain_rows, "chain_form": ApprovalChainForm(),
                                                   "schedules": WorkSchedule.objects.all()})


@admin_required
def new_version(request, kind):
    kind = kind.upper()
    if kind not in DEFAULTS:
        return redirect("policies:index")
    base = PolicyVersion.objects.filter(kind=kind, status=PolicyVersion.Status.ACTIVE).order_by("-effective_from").first()
    form = PolicyVersionForm(kind, request.POST or None, base=base,
                             initial={"name": base.name if base else f"{kind.title()} policy", "source_document": base.source_document if base else ""})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            v = PolicyVersion.objects.create(kind=kind, name=d["name"], source_document=d["source_document"], effective_from=d["effective_from"],
                                             parameters=form.parameters(), change_reason=d["change_reason"], created_by=request.user,
                                             status=PolicyVersion.Status.DRAFT)
            v.departments.set(d["departments"])
            # Carry forward HR confirmations for parameters whose value did not change.
            if base:
                v.hr_confirmations = {k: c for k, c in (base.hr_confirmations or {}).items() if base.get(k) == v.get(k)}
                v.save(update_fields=["hr_confirmations"])
            if d["activate"]:
                v.status, v.activated_by, v.activated_at = PolicyVersion.Status.ACTIVE, request.user, timezone.now()
                v.save()
            audit.log(request.user, "policy.version_created", v, reason=d["change_reason"],
                      before=base.parameters if base else None, after=v.parameters)
        messages.success(request, f"Policy version saved as {'active' if d['activate'] else 'draft'}. Existing records keep the version they were processed with.")
        return redirect("policies:index")
    return render(request, "policies/version_form.html", {"form": form, "kind": kind, "base": base})


@admin_required
@require_POST
def activate(request, pk):
    v = get_object_or_404(PolicyVersion, pk=pk)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "A reason is required.")
        return redirect("policies:index")
    new_status = PolicyVersion.Status.ACTIVE if request.POST.get("to") == "ACTIVE" else PolicyVersion.Status.RETIRED
    old = v.status
    v.status = new_status
    if new_status == PolicyVersion.Status.ACTIVE:
        v.activated_by, v.activated_at = request.user, timezone.now()
    v.save()
    audit.log(request.user, "policy.status_changed", v, before={"status": old}, after={"status": new_status}, reason=reason)
    messages.success(request, f"{v} is now {v.get_status_display().lower()}.")
    return redirect("policies:index")


@admin_required
@require_POST
def confirm_parameter(request, pk):
    """Record that HR has confirmed the interpretation of one parameter."""
    v = get_object_or_404(PolicyVersion, pk=pk)
    key = request.POST.get("key")
    note = request.POST.get("note", "").strip()
    if key not in DEFAULTS[v.kind] or not note:
        messages.error(request, "Enter who confirmed it and the reference (e.g. 'HR Head email 12-Nov-2026').")
        return redirect("policies:index")
    conf = dict(v.hr_confirmations or {})
    conf[key] = {"confirmed_by": request.user.employee_code, "confirmed_at": timezone.now().isoformat(), "note": note, "value": v.get(key)}
    v.hr_confirmations = conf
    v.save(update_fields=["hr_confirmations", "updated_at"])
    audit.log(request.user, "policy.parameter_confirmed", v, after={"key": key, "value": v.get(key), "note": note})
    messages.success(request, f"Interpretation of “{DEFAULTS[v.kind][key]['label']}” recorded as confirmed by HR.")
    return redirect("policies:index")


@admin_required
@require_POST
def save_chain(request):
    form = ApprovalChainForm(request.POST)
    if form.is_valid():
        steps = form.steps()
        obj, _ = ApprovalChainConfig.objects.update_or_create(request_type=form.cleaned_data["request_type"],
                                                              defaults={"steps": steps, "updated_by": request.user})
        audit.log(request.user, "policy.approval_chain_saved", obj, after={"steps": steps})
        messages.success(request, f"Approval chain for {obj.request_type.replace('_', ' ').lower()}: {' → '.join(steps)}. Applies to new requests.")
    return redirect("policies:index")


@hr_required
def leave_types(request):
    edit = get_object_or_404(LeaveType, pk=request.GET["edit"]) if request.GET.get("edit") else None
    form = LeaveTypeForm(request.POST or None, instance=edit)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit.log(request.user, "policy.leave_type_saved", obj, after={k: str(v) for k, v in form.cleaned_data.items()})
        messages.success(request, f"Leave type {obj.name} saved.")
        return redirect("policies:leave_types")
    return render(request, "policies/leave_types.html", {"form": form, "types": LeaveType.objects.all(), "edit": edit})


@hr_required
def holidays(request):
    edit = get_object_or_404(Holiday, pk=request.GET["edit"]) if request.GET.get("edit") else None
    form = HolidayForm(request.POST or None, instance=edit)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        audit.log(request.user, "policy.holiday_saved", obj, after={"date": obj.date, "name": obj.name, "active": obj.is_active})
        messages.success(request, f"Holiday {obj.name} saved. Recalculate affected months if attendance was already processed.")
        return redirect("policies:holidays")
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = timezone.localdate().year
    return render(request, "policies/holidays.html", {"form": form, "holidays": Holiday.objects.filter(date__year=year), "year": year, "edit": edit})


@admin_required
def schedules(request):
    form = ScheduleForm(request.POST or None, initial={"effective_from": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.weekly_off_days = sorted(int(x) for x in form.cleaned_data["weekly_offs"])
        with transaction.atomic():
            if obj.is_default:
                WorkSchedule.objects.exclude(code=obj.code).update(is_default=False)
            obj.save()
        audit.log(request.user, "policy.schedule_saved", obj, after={"code": obj.code, "start": obj.start_time, "end": obj.end_time,
                                                                     "offs": obj.weekly_off_days, "effective_from": obj.effective_from},
                  reason=obj.change_reason)
        messages.success(request, f"Schedule {obj.name} effective {obj.effective_from:%d %b %Y} saved.")
        return redirect("policies:index")
    return render(request, "policies/schedule_form.html", {"form": form, "schedules": WorkSchedule.objects.all()})
