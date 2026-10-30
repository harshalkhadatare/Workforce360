from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit import services as audit
from apps.core import permissions as perms
from apps.core.forms import StyledForm, StyledModelForm
from apps.core.utils import paginate, private_file_response
from apps.notifications.services import hr_users, notify_users

from .models import EmployeeQuery, QueryComment

S = EmployeeQuery.Status


class QueryForm(StyledModelForm):
    class Meta:
        model = EmployeeQuery
        fields = ["category", "subject", "description", "attachment"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}
        help_texts = {"attachment": "Optional. PDF, JPG or PNG, up to 5 MB."}


class ReplyForm(StyledForm):
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 3}))
    attachment = forms.FileField(required=False)
    is_internal = forms.BooleanField(required=False, label="Internal note (not visible to the employee)")

    def clean_attachment(self):
        from apps.core.validators import validate_document

        f = self.cleaned_data.get("attachment")
        if f:
            validate_document(f)
        return f


class ManageForm(StyledForm):
    status = forms.ChoiceField(choices=S.choices)
    assigned_to = forms.ModelChoiceField(User.objects.none(), required=False, empty_label="Unassigned")
    resolution = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), help_text="Shown to the employee when resolved.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = hr_users().distinct().order_by("employee_code")


def _scope(user):
    if perms.is_hr(user):
        return EmployeeQuery.objects.all()
    emp = perms.own_employee(user)
    return EmployeeQuery.objects.filter(employee=emp) if emp else EmployeeQuery.objects.none()


def _link(q):
    return reverse("queries:detail", args=[q.pk])


@login_required
def query_list(request):
    qs = _scope(request.user).select_related("employee", "assigned_to")
    status = request.GET.get("status", "")
    if status == "open":
        qs = qs.exclude(status__in=[S.RESOLVED, S.CLOSED])
    elif status:
        qs = qs.filter(status=status)
    if request.GET.get("category"):
        qs = qs.filter(category=request.GET["category"])
    if request.GET.get("q"):
        t = request.GET["q"]
        qs = qs.filter(Q(subject__icontains=t) | Q(employee__employee_id__icontains=t) | Q(employee__first_name__icontains=t))
    if request.GET.get("mine") and perms.is_hr(request.user):
        qs = qs.filter(assigned_to=request.user)
    return render(request, "queries/list.html", {"page_obj": paginate(request, qs), "statuses": S.choices,
                                                 "categories": EmployeeQuery.Category.choices, "is_hr": perms.is_hr(request.user)})


@perms.employee_required
def create(request):
    form = QueryForm(request.POST or None, request.FILES or None, initial={"category": request.GET.get("category")})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            q = form.save(commit=False)
            q.employee = perms.own_employee(request.user)
            q.save()
            audit.log(request.user, "query.created", q)
            transaction.on_commit(lambda: notify_users(
                list(hr_users().distinct()), event_type="query.created", title=f"New query {q.reference}: {q.subject}",
                intro=f"{q.employee.full_name} ({q.employee.employee_id}) raised a {q.get_category_display().lower()} query.",
                link_path=_link(q), dedupe=f"query-created:{q.pk}", related=q))
        messages.success(request, f"Query {q.reference} submitted. You'll be notified when HR responds.")
        return redirect("queries:detail", pk=q.pk)
    return render(request, "queries/create.html", {"form": form})


@login_required
def detail(request, pk):
    q = get_object_or_404(_scope(request.user).select_related("employee", "assigned_to"), pk=pk)
    is_hr = perms.is_hr(request.user)
    is_owner = perms.own_employee(request.user) and perms.own_employee(request.user).pk == q.employee_id
    comments = q.comments.select_related("author")
    if not is_hr:
        comments = comments.filter(is_internal=False)
    reply = ReplyForm(request.POST or None, request.FILES or None, prefix="r")
    if not is_hr:
        del reply.fields["is_internal"]
    manage = ManageForm(request.POST or None, prefix="m", initial={"status": q.status, "assigned_to": q.assigned_to, "resolution": q.resolution}) if is_hr else None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply" and reply.is_valid():
            if q.status == S.CLOSED:
                messages.error(request, "This query is closed.")
                return redirect(_link(q))
            internal = is_hr and reply.cleaned_data.get("is_internal")
            c = QueryComment(query=q, author=request.user, body=reply.cleaned_data["body"], is_internal=bool(internal))
            if reply.cleaned_data.get("attachment"):
                c.attachment = reply.cleaned_data["attachment"]
            c.save()
            if is_owner and q.status == S.AWAITING_EMPLOYEE:
                q.status = S.IN_PROGRESS
                q.save(update_fields=["status", "updated_at"])
            if not internal:
                targets = [q.assigned_to] if (is_owner and q.assigned_to) else (list(hr_users().distinct()) if is_owner else [getattr(q.employee, "user", None)])
                notify_users([t for t in targets if t], event_type="query.updated", title=f"Query {q.reference} updated",
                             intro=f"New reply on '{q.subject}'.", link_path=_link(q), dedupe=f"query-reply:{c.pk}", related=q)
            messages.success(request, "Note added." if internal else "Reply sent.")
            return redirect(_link(q))
        if action == "manage" and is_hr and manage.is_valid():
            d = manage.cleaned_data
            before = {"status": q.status, "assigned_to": str(q.assigned_to or "")}
            assigned_changed = d["assigned_to"] != q.assigned_to
            q.status, q.assigned_to = d["status"], d["assigned_to"]
            if d["assigned_to"] and q.status == S.OPEN:
                q.status = S.ASSIGNED
            if q.status == S.RESOLVED:
                if not (d["resolution"] or "").strip():
                    messages.error(request, "Enter a resolution before marking the query resolved.")
                    return redirect(_link(q))
                q.resolved_at = timezone.now()
            q.resolution = d["resolution"]
            q.save()
            audit.log(request.user, "query.updated", q, before=before, after={"status": q.status, "assigned_to": str(q.assigned_to or "")})
            if assigned_changed and q.assigned_to:
                notify_users([q.assigned_to], event_type="query.assigned", title=f"Query {q.reference} assigned to you", intro=q.subject,
                             link_path=_link(q), dedupe=f"query-assigned:{q.pk}:{q.assigned_to_id}:{timezone.now():%Y%m%d%H%M%S}", related=q)
            emp_user = getattr(q.employee, "user", None)
            if before["status"] != q.status and emp_user:
                notify_users([emp_user], event_type="query.resolved" if q.status == S.RESOLVED else "query.updated",
                             title=f"Query {q.reference}: {q.get_status_display()}",
                             intro=(q.resolution if q.status == S.RESOLVED else f"Status changed to {q.get_status_display()}."),
                             link_path=_link(q), dedupe=f"query-status:{q.pk}:{q.status}:{timezone.now():%Y%m%d%H%M%S}", related=q)
            messages.success(request, "Query updated.")
            return redirect(_link(q))
    return render(request, "queries/detail.html", {"q": q, "comments": comments, "reply": reply, "manage": manage, "is_hr": is_hr})


@login_required
def attachment(request, pk, comment_id=None):
    q = get_object_or_404(_scope(request.user), pk=pk)
    if comment_id:
        c = get_object_or_404(q.comments, pk=comment_id)
        if c.is_internal and not perms.is_hr(request.user):
            raise Http404
        return private_file_response(c.attachment)
    return private_file_response(q.attachment)
