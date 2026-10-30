import json

from django.db.models import Q
from django.shortcuts import render

from apps.core.permissions import admin_required
from apps.core.utils import paginate

from .models import AuditLog


@admin_required
def audit_list(request):
    qs = AuditLog.objects.select_related("actor")
    g = request.GET
    if g.get("q"):
        qs = qs.filter(Q(action__icontains=g["q"]) | Q(target_repr__icontains=g["q"]) | Q(reason__icontains=g["q"]))
    if g.get("actor"):
        qs = qs.filter(actor_label__iexact=g["actor"].strip())
    if g.get("employee"):
        qs = qs.filter(employee_ref__iexact=g["employee"].strip())
    if g.get("action"):
        qs = qs.filter(action__startswith=g["action"])
    if g.get("from"):
        qs = qs.filter(created_at__date__gte=g["from"])
    if g.get("to"):
        qs = qs.filter(created_at__date__lte=g["to"])
    page = paginate(request, qs, 50)
    for e in page:
        e.before_json = json.dumps(e.before, indent=1, default=str) if e.before else ""
        e.after_json = json.dumps(e.after, indent=1, default=str) if e.after else ""
    prefixes = sorted({a.split(".")[0] for a in AuditLog.objects.values_list("action", flat=True).distinct()[:500]})
    return render(request, "audit/list.html", {"page_obj": page, "prefixes": prefixes})
