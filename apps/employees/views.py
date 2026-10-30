from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts import security
from apps.accounts.models import Role, User
from apps.audit import services as audit
from apps.core import permissions as perms
from apps.core.utils import paginate, private_file_response
from apps.core.validators import sha256_of
from apps.payroll.forms import PayrollProfileForm
from apps.payroll.models import PayrollProfile

from . import services
from .forms import (AccountForm, AddressForm, BankForm, ConfirmEmployeeForm, ContactHRForm, ContactSelfForm, DepartmentForm,
                    DesignationForm, DirectoryFilterForm, DocumentForm, EmployeeCreateForm, EmploymentForm, FamilyForm, IdentityForm,
                    PersonalHRForm, PersonalSelfForm, QualificationForm, ReasonForm)
from .models import Address, BankDetail, Department, Designation, Employee, EmployeeDocument, FamilyMember, IdentityDetail, Qualification

SECTIONS = [
    ("personal", "Personal Details", "bi-person"),
    ("contact", "Contact Details", "bi-telephone"),
    ("identity", "Passport, PAN, Aadhaar & IDs", "bi-credit-card-2-front"),
    ("qualifications", "Qualifications", "bi-mortarboard"),
    ("employment", "Employment", "bi-briefcase"),
    ("bank", "Bank", "bi-bank"),
    ("family", "Family Members", "bi-people"),
    ("payroll", "Exemption & Payroll", "bi-cash-stack"),
    ("documents", "Documents", "bi-folder2"),
    ("history", "History", "bi-clock-history"),
]


def _viewer(user, emp):
    me = perms.own_employee(user)
    return {
        "hr": perms.is_hr(user),
        "self": bool(me and me.pk == emp.pk),
        "manager": perms.is_reporting_manager_of(user, emp),
        "payroll": perms.can_view_payroll(user, emp),
    }


def _can_see(section, v):
    if v["hr"] and section != "payroll":
        return True
    return {
        "personal": True, "contact": True, "employment": True,
        "qualifications": v["self"] or v["manager"],
        "identity": v["self"], "bank": v["self"], "family": v["self"], "documents": v["self"], "history": v["self"],
        "payroll": v["payroll"],
    }.get(section, False)


def _can_edit(section, v):
    if section == "payroll":
        return v["payroll"]
    if v["hr"]:
        return section not in ("history",)
    if v["self"]:
        return section in ("personal", "contact", "family", "documents")
    return False


@login_required
def directory(request):
    if not perms.is_manager(request.user):
        raise PermissionDenied
    form = DirectoryFilterForm(request.GET or None)
    qs = perms.employee_scope(request.user).select_related("department", "designation", "reporting_manager").distinct()
    if form.is_valid():
        d = form.cleaned_data
        if d["q"]:
            qs = qs.filter(Q(employee_id__icontains=d["q"]) | Q(first_name__icontains=d["q"]) | Q(last_name__icontains=d["q"]))
        if d["department"]:
            qs = qs.filter(department=d["department"])
        if d["status"]:
            qs = qs.filter(employment_status=d["status"])
        if d["active"] in ("0", "1"):
            qs = qs.filter(is_active=d["active"] == "1")
    else:
        qs = qs.filter(is_active=True)
    sort = request.GET.get("sort", "employee_id")
    allowed = {"employee_id", "-employee_id", "first_name", "-first_name", "date_of_joining", "-date_of_joining", "department__name", "-department__name"}
    qs = qs.order_by(sort if sort in allowed else "employee_id")
    return render(request, "employees/directory.html", {"form": form, "page_obj": paginate(request, qs), "sort": sort})


@login_required
def me(request):
    emp = perms.own_employee(request.user)
    if not emp:
        raise Http404
    return redirect("employees:detail", pk=emp.pk, section="personal")


def _save_with_audit(request, form, instance_label, emp, action):
    before = model_to_dict(form.instance, fields=form.fields.keys()) if form.instance.pk else {}
    obj = form.save()
    after = model_to_dict(obj, fields=form.fields.keys())
    b, a = audit.diff({k: str(v) for k, v in before.items()}, {k: str(v) for k, v in after.items()})
    if b or not before:
        audit.log(request.user, action, obj, employee_ref=emp.employee_id, before=b, after=a)
    return obj


@login_required
def detail(request, pk, section="personal"):
    emp = perms.get_scoped_employee(request.user, pk)
    v = _viewer(request.user, emp)
    if section not in dict((s[0], s[1]) for s in SECTIONS) or not _can_see(section, v):
        raise Http404
    can_edit = _can_edit(section, v)
    editing = can_edit and request.GET.get("edit") == "1"
    ctx = {"emp": emp, "section": section, "v": v, "can_edit": can_edit, "editing": editing,
           "sections": [s for s in SECTIONS if _can_see(s[0], v)], "section_title": dict((s[0], s[1]) for s in SECTIONS)[section]}
    url = reverse("employees:detail", args=[emp.pk, section])
    handler = globals()[f"_section_{section}"]
    result = handler(request, emp, v, ctx, url)
    if result is not None:
        return result
    return render(request, f"employees/sections/{section}.html", ctx)


def _section_personal(request, emp, v, ctx, url):
    form_cls = PersonalHRForm if v["hr"] else PersonalSelfForm
    if ctx["editing"]:
        form = form_cls(request.POST or None, request.FILES or None, instance=emp)
        if request.method == "POST" and form.is_valid():
            _save_with_audit(request, form, "personal", emp, "employee.personal_updated")
            messages.success(request, "Personal details saved.")
            return redirect(url)
        ctx["form"] = form


def _section_contact(request, emp, v, ctx, url):
    addrs = {a.kind: a for a in emp.addresses.all()}
    ctx["addresses"] = addrs
    if ctx["editing"]:
        form_cls = ContactHRForm if v["hr"] else ContactSelfForm
        form = form_cls(request.POST or None, instance=emp, prefix="c")
        cur = AddressForm(request.POST or None, instance=addrs.get("CURRENT") or Address(employee=emp, kind="CURRENT"), prefix="cur")
        perm = AddressForm(request.POST or None, instance=addrs.get("PERMANENT") or Address(employee=emp, kind="PERMANENT"), prefix="per")
        if request.method == "POST":
            fill_perm = any(request.POST.get(f"per-{f}") for f in ("line1", "city", "state", "pin_code"))
            fill_cur = any(request.POST.get(f"cur-{f}") for f in ("line1", "city", "state", "pin_code"))
            ok = form.is_valid() and (not fill_cur or cur.is_valid()) and (not fill_perm or perm.is_valid())
            if ok:
                with transaction.atomic():
                    _save_with_audit(request, form, "contact", emp, "employee.contact_updated")
                    if fill_cur:
                        _save_with_audit(request, cur, "address", emp, "employee.address_updated")
                    if fill_perm:
                        _save_with_audit(request, perm, "address", emp, "employee.address_updated")
                messages.success(request, "Contact details saved.")
                return redirect(url)
        ctx.update(form=form, cur_form=cur, per_form=perm)


def _section_identity(request, emp, v, ctx, url):
    ident = IdentityDetail.objects.filter(employee=emp).first()
    reveal = request.GET.get("reveal") == "1" and perms.can_view_sensitive(request.user, emp)
    if reveal and ident:
        audit.log(request.user, "sensitive.identity_viewed", ident, employee_ref=emp.employee_id)
    ctx.update(ident=ident, reveal=reveal, can_reveal=perms.can_view_sensitive(request.user, emp))
    if ctx["editing"]:
        form = IdentityForm(request.POST or None, instance=ident or IdentityDetail(employee=emp))
        if request.method == "POST" and form.is_valid():
            obj = form.save()
            audit.log(request.user, "employee.identity_updated", obj, employee_ref=emp.employee_id,
                      after={"fields_changed": list(form.changed_data)})  # values never written to the log
            messages.success(request, "Identity details saved.")
            return redirect(url)
        ctx["form"] = form


def _list_section(request, emp, ctx, url, model, form_cls, label, related_qs):
    ctx["items"] = related_qs
    if not ctx["can_edit"]:
        return None
    edit_id = request.GET.get("item")
    instance = get_object_or_404(model, pk=edit_id, employee=emp) if edit_id else model(employee=emp)
    if request.method == "POST":
        if request.POST.get("delete"):
            obj = get_object_or_404(model, pk=request.POST["delete"], employee=emp)
            audit.log(request.user, f"employee.{label}_deleted", obj, employee_ref=emp.employee_id, before=model_to_dict(obj))
            obj.delete()
            messages.success(request, "Record removed.")
            return redirect(url)
        form = form_cls(request.POST, instance=instance)
        if form.is_valid():
            _save_with_audit(request, form, label, emp, f"employee.{label}_saved")
            messages.success(request, "Saved.")
            return redirect(url)
        ctx["editing"] = True
    else:
        form = form_cls(instance=instance)
    ctx["form"] = form
    ctx["editing_item"] = edit_id
    return None


def _section_qualifications(request, emp, v, ctx, url):
    return _list_section(request, emp, ctx, url, Qualification, QualificationForm, "qualification", emp.qualifications.all())


def _section_family(request, emp, v, ctx, url):
    return _list_section(request, emp, ctx, url, FamilyMember, FamilyForm, "family", emp.family_members.all())


def _section_employment(request, emp, v, ctx, url):
    ctx["user_account"] = getattr(emp, "user", None)
    ctx["confirm_form"] = ConfirmEmployeeForm()
    if ctx["editing"]:
        form = EmploymentForm(request.POST or None, instance=emp)
        if request.method == "POST" and form.is_valid():
            try:
                services.save_employee(form.instance, request.user, reason=form.cleaned_data["reason"],
                                       effective_date=form.cleaned_data["effective_date"])
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(request, "Employment details saved and recorded in history.")
                return redirect(url)
        ctx["form"] = form


def _section_bank(request, emp, v, ctx, url):
    bank = BankDetail.objects.filter(employee=emp).first()
    reveal = request.GET.get("reveal") == "1" and perms.can_view_sensitive(request.user, emp)
    if reveal and bank:
        audit.log(request.user, "sensitive.bank_viewed", bank, employee_ref=emp.employee_id)
    ctx.update(bank=bank, reveal=reveal, can_reveal=perms.can_view_sensitive(request.user, emp))
    if ctx["editing"]:
        form = BankForm(request.POST or None, instance=bank or BankDetail(employee=emp))
        if request.method == "POST" and form.is_valid():
            obj = form.save()
            audit.log(request.user, "employee.bank_updated", obj, employee_ref=emp.employee_id, after={"fields_changed": list(form.changed_data)})
            messages.success(request, "Bank details saved.")
            return redirect(url)
        ctx["form"] = form


def _section_payroll(request, emp, v, ctx, url):
    profile = PayrollProfile.objects.filter(employee=emp).first()
    ctx["profile"] = profile
    if ctx["editing"]:
        form = PayrollProfileForm(request.POST or None, instance=profile or PayrollProfile(employee=emp))
        if request.method == "POST" and form.is_valid():
            before = model_to_dict(profile) if profile else {}
            obj = form.save(commit=False)
            obj.updated_by = request.user
            obj.save()
            b, a = audit.diff({k: str(x) for k, x in before.items()}, {k: str(x) for k, x in model_to_dict(obj).items()})
            audit.log(request.user, "payroll.profile_updated", obj, employee_ref=emp.employee_id, before=b, after=a, reason=form.cleaned_data["reason"])
            messages.success(request, "Payroll information saved.")
            return redirect(url)
        ctx["form"] = form


def _section_documents(request, emp, v, ctx, url):
    docs = emp.documents.select_related("uploaded_by")
    if not v["hr"]:
        docs = docs.filter(visible_to_employee=True)
    ctx["items"] = docs
    if ctx["can_edit"]:
        form = DocumentForm(request.POST or None, request.FILES or None, hr=v["hr"])
        if request.method == "POST":
            if request.POST.get("delete") and v["hr"]:
                doc = get_object_or_404(EmployeeDocument, pk=request.POST["delete"], employee=emp)
                audit.log(request.user, "document.deleted", doc, employee_ref=emp.employee_id, before={"title": doc.title, "category": doc.category})
                doc.file.delete(save=False)
                doc.delete()
                messages.success(request, "Document deleted.")
                return redirect(url)
            if form.is_valid():
                doc = form.save(commit=False)
                f = form.cleaned_data["file"]
                doc.employee, doc.uploaded_by = emp, request.user
                doc.original_name, doc.size_bytes, doc.sha256 = f.name[:200], f.size, sha256_of(f)
                if not v["hr"]:
                    doc.visible_to_employee = True
                doc.save()
                audit.log(request.user, "document.uploaded", doc, employee_ref=emp.employee_id, after={"title": doc.title, "category": doc.category})
                messages.success(request, "Document uploaded.")
                return redirect(url)
        ctx["form"] = form


def _section_history(request, emp, v, ctx, url):
    ctx["records"] = emp.employment_history.select_related("changed_by")[:200]
    ctx["reporting"] = emp.reporting_history.select_related("manager")


@login_required
def download_document(request, pk, doc_id):
    emp = perms.get_scoped_employee(request.user, pk)
    v = _viewer(request.user, emp)
    doc = get_object_or_404(EmployeeDocument, pk=doc_id, employee=emp)
    if not (v["hr"] or (v["self"] and doc.visible_to_employee)):
        raise Http404
    audit.log(request.user, "document.downloaded", doc, employee_ref=emp.employee_id, after={"title": doc.title})
    return private_file_response(doc.file, doc.original_name)


# ---------------------------------------------------------------------------
# HR administration
# ---------------------------------------------------------------------------
@perms.hr_required
def create(request):
    form = EmployeeCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                emp = form.save(commit=False)
                services.save_employee(emp, request.user, is_new=True)
                if form.cleaned_data["create_login"]:
                    services.create_login(emp, request.user, role=form.cleaned_data["role"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Employee {emp.employee_id} created." + (" A password set-up link was emailed (if email is configured)."
                                                                                  if form.cleaned_data["create_login"] else ""))
            return redirect("employees:detail", pk=emp.pk, section="personal")
    return render(request, "employees/create.html", {"form": form})


@perms.hr_required
@require_POST
def confirm(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    form = ConfirmEmployeeForm(request.POST)
    if form.is_valid():
        try:
            services.confirm_employee(emp, form.cleaned_data["confirmation_date"], request.user, form.cleaned_data["reason"])
            messages.success(request, f"{emp.full_name} confirmed with effect from {form.cleaned_data['confirmation_date']:%d %b %Y}.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    else:
        messages.error(request, "Enter a confirmation date and reason.")
    return redirect("employees:detail", pk=emp.pk, section="employment")


@perms.hr_required
@require_POST
def set_active(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    form = ReasonForm(request.POST)
    active = request.POST.get("active") == "1"
    if form.is_valid():
        try:
            services.set_active(emp, active, request.user, form.cleaned_data["reason"], form.cleaned_data.get("effective_date"))
            messages.success(request, f"{emp.full_name} {'reactivated' if active else 'deactivated'}.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    else:
        messages.error(request, "A reason is required.")
    return redirect(request.POST.get("next") or reverse("employees:detail", args=[emp.pk, "employment"]))


@perms.hr_required
def admin_users(request):
    q = request.GET.get("q", "").strip()
    qs = Employee.objects.select_related("department", "reporting_manager", "user").order_by("employee_id")
    if q:
        qs = qs.filter(Q(employee_id__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    if request.GET.get("filter") == "no_login":
        qs = qs.filter(user__isnull=True)
    if request.GET.get("filter") == "managers":
        qs = qs.filter(direct_reports__isnull=False).distinct()
    ctx = {"page_obj": paginate(request, qs, 30), "q": q, "departments": Department.objects.all(), "designations": Designation.objects.all(),
           "dept_form": DepartmentForm(prefix="dept"), "desig_form": DesignationForm(prefix="desig"),
           "unlinked_users": User.objects.filter(employee__isnull=True)}
    return render(request, "employees/admin_users.html", ctx)


@perms.hr_required
def account(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    user = getattr(emp, "user", None)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create_login":
                services.create_login(emp, request.user, role=request.POST.get("role", Role.EMPLOYEE))
                messages.success(request, "Login created. A password set-up email has been queued (sent only if email is configured).")
            elif action == "send_setup" and user:
                entry = services.send_account_setup_email(user)
                status = entry.get_status_display() if entry else "no email address on file"
                messages.info(request, f"Password set-up email: {status}.")
            elif action == "unlock" and user:
                security.clear_failures(user.employee_code)
                audit.log(request.user, "account.unlocked", user)
                messages.success(request, "Sign-in lock cleared.")
            elif action == "save" and user:
                if not perms.is_admin(request.user):
                    raise PermissionDenied("Only administrators can change roles and permissions.")
                form = AccountForm(request.POST)
                if form.is_valid():
                    d = form.cleaned_data
                    if d["role"] != user.role:
                        services.change_role(user, d["role"], request.user, d["reason"])
                    for name, flag in (("HR", d["is_hr"]), ("Finance", d["is_finance"])):
                        g, _ = Group.objects.get_or_create(name=name)
                        has = user.groups.filter(pk=g.pk).exists()
                        if flag and not has:
                            user.groups.add(g)
                            audit.log(request.user, "account.group_added", user, after={"group": name}, reason=d["reason"])
                        elif not flag and has:
                            user.groups.remove(g)
                            audit.log(request.user, "account.group_removed", user, before={"group": name}, reason=d["reason"])
                    messages.success(request, "Account permissions saved.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("employees:account", pk=emp.pk)
    form = None
    if user:
        form = AccountForm(initial={"role": user.role, "is_hr": user.groups.filter(name="HR").exists(),
                                    "is_finance": user.groups.filter(name="Finance").exists()})
    return render(request, "employees/account.html", {"emp": emp, "account": user, "form": form, "roles": Role.choices,
                                                      "locked": user and security.is_locked(user.employee_code, ""),
                                                      "reason_form": ReasonForm()})


@perms.hr_required
@require_POST
def master_data(request):
    kind = request.POST.get("kind")
    form = DepartmentForm(request.POST, prefix="dept") if kind == "dept" else DesignationForm(request.POST, prefix="desig")
    if form.is_valid():
        obj = form.save()
        audit.log(request.user, f"masterdata.{kind}_saved", obj)
        messages.success(request, f"{obj} saved.")
    else:
        messages.error(request, "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
    return redirect("employees:admin_users")
