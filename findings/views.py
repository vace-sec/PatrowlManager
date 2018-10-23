from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.forms.models import model_to_dict
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .models import Finding, RawFinding
from .forms import ImportFindingsForm, FindingForm
from assets.models import Asset
from scans.models import Scan, ScanDefinition
from events.models import Event
from rules.models import Rule
from engines.tasks import importfindings_task
import json
import os
import time
import collections
import csv
from datetime import date, datetime
from uuid import UUID


def list_findings_view(request):
    filter_by_asset = request.GET.get('_asset_value', None)
    filter_by_asset_cond = request.GET.get('_asset_value_cond', None)
    filter_by_title = request.GET.get('_title', None)
    filter_by_title_cond = request.GET.get('_title_cond', None)
    filter_by_type = request.GET.get('_type', None)
    filter_by_type_cond = request.GET.get('_type_cond', None)
    filter_by_severity = request.GET.get('_severity', None)
    filter_by_severity_cond = request.GET.get('_severity_cond', None)
    # filter_by_startdate = request.GET.get('_startdate', None)
    # filter_by_enddate = request.GET.get('_enddate', None)
    filter_by_status = request.GET.get('_status', None)
    filter_by_asset_id = request.GET.get('_asset_id', None)
    filter_by_asset_group_id = request.GET.get('_asset_group_id', None)
    filter_by_asset_group_name = request.GET.get('_asset_group_name', None)
    filter_by_engine = request.GET.get('_engine', None)
    filter_by_type = request.GET.get('_type', None)
    # filter_by_asset_tags = request.GET.get('_tags', None)
    filter_by_scope = request.GET.get('_scope', None)
    filter_by_reference = request.GET.get('_reference', None)
    # filter_by_reference_cond = request.GET.get('_reference_cond', None)

    filters = {}
    excludes = {}
    # Filter by asset value
    if filter_by_asset and filter_by_asset_cond:
        if filter_by_asset_cond in ["exact", "icontains", "istartwith", "iendwith"]:
            filters.update({"asset_name__{}".format(filter_by_asset_cond): filter_by_asset})
        elif filter_by_asset_cond in ["not_exact", "not_icontains", "not_istartwith", "not_iendwith"]:
            excludes.update({"asset_name__{}".format(filter_by_asset_cond[4:]): filter_by_asset})

    # Filter by finding type
    if filter_by_type:
        if filter_by_type_cond in ["exact", "icontains", "istartwith", "iendwith"]:
            filters.update({"type__{}".format(filter_by_type_cond): filter_by_type})
        elif filter_by_asset_cond in ["not_exact", "not_icontains", "not_istartwith", "not_iendwith"]:
            excludes.update({"type__{}".format(filter_by_type_cond[4:]): filter_by_type})

    # Filter by finding title
    if filter_by_title:
        if filter_by_title_cond in ["exact", "icontains", "istartwith", "iendwith"]:
            filters.update({"title__{}".format(filter_by_title_cond): filter_by_title})
        elif filter_by_asset_cond in ["not_exact", "not_icontains", "not_istartwith", "not_iendwith"]:
            excludes.update({"title__{}".format(filter_by_title_cond[4:]): filter_by_title})

    # Filter by finding severity
    if filter_by_severity and filter_by_severity in ["info", "low", "medium", "high", "critical"]:
        if filter_by_severity_cond == "exact":
            filters.update({"severity__{}".format(filter_by_severity_cond): filter_by_severity})
        elif filter_by_asset_cond == "not_exact":
            excludes.update({"severity__{}".format(filter_by_severity_cond[4:]): filter_by_severity})

    if filter_by_status and filter_by_status in ["new", "ack"]:
        filters.update({"status": filter_by_status})
    if filter_by_asset_id:
        filters.update({"asset_id": filter_by_asset_id})
    if filter_by_asset_group_id:
        filters.update({"asset__assetgroup": filter_by_asset_group_id})
    if filter_by_asset_group_name:
        filters.update({"asset__assetgroup__name__icontains": filter_by_asset_group_name})

    if filter_by_engine:
        filters.update({"engine_type": filter_by_engine})
    if filter_by_scope:
        filters.update({"scan__engine_policy__scopes__in": filter_by_scope})
    if filter_by_reference:
        filters.update({"vuln_refs__icontains": filter_by_reference})

    findings = Finding.objects.filter(**filters).exclude(**excludes).order_by(
             'asset_name', 'severity', 'status', 'type')

    nb_findings = findings.count()

    # Pagination findings
    nb_rows = request.GET.get('n', 50)
    findings_paginator = Paginator(findings, nb_rows)
    page = request.GET.get('page')
    try:
        findings_p = findings_paginator.page(page)
    except PageNotAnInteger:
        findings_p = findings_paginator.page(1)
    except EmptyPage:
        findings_p = findings_paginator.page(findings_paginator.num_pages)

    return render(request, 'list-findings.html', {
        'findings': findings_p, 'nb_findings': nb_findings})


def list_asset_findings_view(request, asset_name):
    findings = None
    filter_by_status = request.GET.get('_status', None)

    filters = {
        #"owner_id": request.user.id,
        "asset_name": asset_name
    }
    if filter_by_status and filter_by_status in ["new", "ack"]:
        filters.update({"status": filter_by_status})

    findings = Finding.objects.filter(**filters).order_by(
             'asset_name', 'severity', 'status', 'type')

    return render(request, 'list-findings.html', {'findings': findings})


def delete_finding_view(request, finding_id):
    finding = get_object_or_404(Finding, id=finding_id)
    if request.method == 'POST':
        asset_id = finding.asset.id
        finding.delete()

        # reevaluate related asset critity
        #Asset.objects.get(owner_id=request.user.id, asset_id=finding.asset_id).evaluate_risk()
        Asset.objects.get(id=asset_id).evaluate_risk()
        messages.success(request, 'Finding successfully deleted!')
        return redirect('list_findings_view')
    return render(request, 'delete-findings.html', {'finding': finding})


def import_findings_view(request):
    if request.method == 'POST':
        form = ImportFindingsForm(request.POST, request.FILES)

        if form.is_valid():
            # store the file in /media/imports/<owner_id>/<tmp_file>
            user_report_dir = settings.MEDIA_ROOT + "/imports/"+str(request.user.id)+"/"
            if not os.path.exists(user_report_dir):
                os.makedirs(user_report_dir)
            filename = user_report_dir+"import_" + str(request.user.id) + "_" + str(int(time.time() * 1000)) + "." + form.cleaned_data['engine']
            with open(filename, 'wb+') as destination:
                for chunk in request.FILES['file'].chunks():
                    destination.write(chunk)
            # enqueue the import processing
            importfindings_task.apply_async(
                args=[filename, request.user.id, form.cleaned_data['engine'], form.cleaned_data['min_level']],
                queue='default',
                routing_key='default',
                retry=False
            )

            messages.success(request, 'Findings successfully imported!')
        return redirect('list_findings_view')
    else:
        form = ImportFindingsForm()
    return render(request, 'import-findings.html', {'form': form})


def details_finding_view(request, finding_id):
    finding = None

    if request.GET.get("raw", None) and request.GET.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
        return render(request, 'details-finding.html', {'finding': finding, 'raw': True})

    finding = get_object_or_404(Finding, id=finding_id)

    # Inject tracking data
    tracking_timeline = {}
    tracking_timeline.update({finding.created_at: {"level": "info", "message": "First identification in scan <a href='/scans/details/{}'>'{}'</a> ({}). Definition <a href='/scans/defs/details/{}'>here</a>".format(finding.scan.id, finding.scan, finding.scan.engine_type, finding.scan.scan_definition.id)}})

    # Identify changes
    for event in Event.objects.filter(finding=finding):
        tracking_timeline.update({event.created_at: {"level": "info", "message": event.message}})

    # Identify finding occurrences in related scan results (excluding the 1st)
    for scan in finding.scan.scan_definition.scan_set.filter(status="finished").exclude(id=finding.scan.id):
        finding_notfound = True
        for f in scan.rawfinding_set.all():
            if f.hash == finding.hash:
                finding_notfound = False
                tracking_timeline.update({f.created_at: {
                    "level": "info",
                    "message": "Identified in scan <a href='/scans/details/{}'>{}</a>".format(scan.id, scan)}})
            break
        if finding_notfound:
            tracking_timeline.update({scan.created_at: {
                "level": "warning",
                "message": "Not in scan <a href='/scans/details/{}'>{}</a>".format(scan.id, scan)}})

    return render(request, 'details-finding.html', {
        'finding': finding,
        'raw': False,
        'tracking_timeline': collections.OrderedDict(sorted(tracking_timeline.items(), key=lambda t: t[0]))})


def raw_finding(request, finding_id):
    finding = get_object_or_404(Finding, id=finding_id)
    return JsonResponse(finding.raw_data, json_dumps_params={'indent': 2}, safe=False)


def list_findings(request):
    findings_list = []
    for f in Finding.objects.all():
        findings_list.append(model_to_dict(f))

    return JsonResponse(findings_list, json_dumps_params={'indent': 2}, safe=False)


def edit_finding_view(request, finding_id):
    form = None
    finding = get_object_or_404(Finding, id=finding_id)
    form = FindingForm()
    if request.method == 'GET':
        form = FindingForm(instance=finding)
    elif request.method == 'POST':
        form = FindingForm(request.POST, instance=finding)

        if form.is_valid():
            finding.title = form.cleaned_data['title']
            finding.description = form.cleaned_data['description']
            finding.confidence = form.cleaned_data['confidence']
            finding.type = form.cleaned_data['type']
            finding.severity = form.cleaned_data['severity']
            finding.solution = form.cleaned_data['solution']
            finding.risk_info = form.cleaned_data['risk_info']
            finding.vuln_refs = form.cleaned_data['vuln_refs']
            finding.links = form.cleaned_data['links']
            finding.tags = form.cleaned_data['tags'].split(',')
            finding.status = form.cleaned_data['status']

            finding.save()
            messages.success(request, 'Update submission successful')
            return redirect('list_findings_view')

    return render(request, 'edit-finding.html', {'form': form, 'finding': finding})


def add_finding_view(request):
    form = None

    form = FindingForm()
    if request.method == 'GET':
        form = FindingForm()
    elif request.method == 'POST':
        form = FindingForm(request.POST)

        if form.is_valid():
            finding_args = {
                'title': form.cleaned_data['title'],
                'description': form.cleaned_data['description'],
                'confidence': form.cleaned_data['confidence'],
                'type': form.cleaned_data['type'],
                'severity': form.cleaned_data['severity'],
                'solution': form.cleaned_data['solution'],
                'risk_info': form.cleaned_data['risk_info'],
                'vuln_refs': form.cleaned_data['vuln_refs'],
                'links': form.cleaned_data['links'],
                'tags': str(json.dumps(form.cleaned_data['tags'].split(','))),
                'status': form.cleaned_data['status'],
                # 'owner': form.cleaned_data['owner'],
                'asset': form.cleaned_data['asset'],
                'asset_name': form.cleaned_data['asset'].value,
                'scan': form.cleaned_data['scan']
            }

            finding = Finding(**finding_args)
            finding.save()
            messages.success(request, 'Creation submission successful')
            return redirect('list_findings_view')

    return render(request, 'add-finding.html', {'form': form})


def add_finding(request):
    res = {"page": "add_finding"}

    allowed_fields = [f.name for f in Finding._meta.get_fields()]
    if request.method == 'GET':
        new_finding = Finding.objects.create(
            title=request.GET.get("title")
        )
        res.update({"finding": model_to_dict(new_finding)})
    elif request.method == 'POST':
        new_finding = Finding()
        for field_key in request.POST.iterkeys():
            if field_key in allowed_fields:
                setattr(new_finding, field_key, request.POST.get(field_key))
            else:
                print("not allowed: {}/{}".format(field_key, request.POST.get(field_key)))
        new_finding.save()
        res.update({"asset": model_to_dict(new_finding)})

    return JsonResponse(res, json_dumps_params={'indent': 2})


def get_finding(request, finding_id):
    res = {"page": "get_finding"}
    finding = get_object_or_404(Finding, id=finding_id)
    res.update({"finding": model_to_dict(finding)})

    return JsonResponse(res, json_dumps_params={'indent': 2})


@csrf_exempt #not secure!!!
def delete_findings(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    findings = json.loads(request.body)
    for finding_id in findings:
        f = Finding.objects.get(id=finding_id)

        # reevaluate related asset critity
        Asset.objects.get(id=f.asset.id).evaluate_risk()
        f.delete()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


@csrf_exempt #not secure!!!
def export_findings_csv(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename=export_findings.csv'
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'asset_value', 'asset_type',
        'engine_type', 'engine_name',
        'scan_title', 'scan_policy',
        'finding_id', 'finding_type', 'finding_status', 'finding_tags',
        'finding_severity', 'finding_description', 'finding_solution',
        'finding_hash', 'finding_creation_date', 'finding_risk_info',
        'finding_cvss', 'finding_links'
        ])

    findings = json.loads(request.body)
    for finding_id in findings:
        finding = Finding.objects.get(id=finding_id)
        if 'links' in finding.risk_info.keys():
            finding_links = ", ".join(finding.risk_info['links'])
        else:
            finding_links = None

        writer.writerow([
            finding.asset.value, finding.asset.type,
            finding.scan.engine_type.name, finding.scan.engine.name,
            finding.scan.title, finding.scan.engine_policy.name,
            finding.id, finding.type, finding.status, ','.join(finding.tags),
            finding.severity, finding.description, finding.solution,
            finding.hash, finding.created_at, finding.risk_info,
            finding.risk_info['cvss_base_score'],
            finding_links
        ])

    return response


@csrf_exempt #not secure!!!
def delete_rawfindings(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    findings = json.loads(request.body)
    for finding_id in findings:
        f = RawFinding.objects.get(id=finding_id)

        # reevaluate related asset critity
        Asset.objects.get(id=f.asset.id).evaluate_risk()
        f.delete()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


@csrf_exempt
def change_findings_status(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    findings = json.loads(request.body)
    for finding in findings:
        f = Finding.objects.filter(id=finding['ack']).first()
        f.status = "ack"
        f.save()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})

@csrf_exempt
def change_rawfindings_status(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    findings = json.loads(request.body)
    for finding in findings:
        f = RawFinding.objects.filter(id=finding['ack']).first()
        f.status = "ack"
        f.save()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


def remove_finding(request, finding_id):
    res = {"page": "remove_finding"}
    finding = get_object_or_404(Finding, id=finding_id)

    # reevaluate related asset critity
    Asset.objects.get(id=finding.asset.id).evaluate_risk()

    res.update({"finding_id": finding.id})
    finding.delete()

    return JsonResponse(res, json_dumps_params={'indent': 2})


def update_finding(request, finding_id):
    res = {"page": "update_finding"}
    finding = Finding.objects.filter(finding_id=finding_id)[0]

    allowed_fields = [f.name for f in Finding._meta.get_fields()]
    if request.method == 'GET':
        for field_key in request.GET.iterkeys():
            if field_key in allowed_fields:
                setattr(finding, field_key, request.GET.get(field_key))

    finding.save()

    # reevaluate related asset critity
    Asset.objects.get(owner_id=request.user.id, asset_id=finding.asset_id).evaluate_risk()

    res.update({"finding": model_to_dict(Finding.objects.filter(finding_id=finding_id)[0])})
    return JsonResponse(res, json_dumps_params={'indent': 2})


def get_findings_stats(request):
    scope = request.GET.get('scope', None)
    data = {}
    if not scope:
        findings = Finding.objects.all()
    else:
        scan_id = request.GET.get('scan_id', None)
        if not scan_id:
            return JsonResponse({})
        if scope == "scan_def":
            scan_def = get_object_or_404(ScanDefinition, id=scan_id)
            findings = RawFinding.objects.filter(scan__scan_definition_id=scan_id)
        elif scope == "scan":
            scan = get_object_or_404(Scan, id=scan_id)
            findings = RawFinding.objects.filter(scan=scan)

    data = {
        "nb_findings": findings.count(),
        "nb_info": findings.filter(severity="info").count(),
        "nb_low": findings.filter(severity="low").count(),
        "nb_medium": findings.filter(severity="medium").count(),
        "nb_high": findings.filter(severity="high").count(),
        "nb_critical": findings.filter(severity="critical").count(),
        "nb_new_findings": findings.filter(status="new").count(),
        "nb_new_info": findings.filter(status="new", severity="info").count(),
        "nb_new_low": findings.filter(status="new", severity="low").count(),
        "nb_new_medium": findings.filter(status="new", severity="medium").count(),
        "nb_new_high": findings.filter(status="new", severity="high").count(),
        "nb_new_critical": findings.filter(status="new", severity="critical").count(),
    }

    return JsonResponse(data, json_dumps_params={'indent': 2})


def compare_rawfindings_view(request):
    finding_a_id = request.GET.get("finding_a_id", None)
    finding_b_id = request.GET.get("finding_b_id", None)
    raw_finding = request.GET.get("raw", None)
    if raw_finding:
        finding_a = get_object_or_404(RawFinding, id=finding_a_id)
        finding_b = get_object_or_404(RawFinding, id=finding_b_id)
    else:
        finding_a = get_object_or_404(Finding, id=finding_a_id)
        finding_b = get_object_or_404(Finding, id=finding_b_id)

    return render(request, 'compare-findings.html', {
        'finding_a': finding_a,
        'finding_b': finding_b})


def send_finding_alerts(request, finding_id):
    if request.GET.get("raw", None) and request.GET.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
    else:
        finding = get_object_or_404(Finding, id=finding_id)

    # Create a new rule
    rule = Rule(title="manual", severity=finding.severity.capitalize(), owner_id=request.user.id)
    if request.GET.get("type", None) and request.GET.get("type") == "slack":
        rule.target = "slack"
        rule.notify(finding.title)
    elif request.GET.get("type", None) and request.GET.get("type") == "thehive":
        rule.target = "thehive"
        rule.notify(message=finding.title, asset=finding.asset, description=finding.description)
    elif request.GET.get("type", None) and request.GET.get("type") == "mail":
        rule.target = "mail"
        rule.notify(message=finding.title, asset=finding.asset, description=finding.description)

    rule.delete()

    return JsonResponse({"status": "success"}, json_dumps_params={'indent': 2})


def generate_finding_alerts(request, finding_id):
    if request.GET.get("raw", None) and request.GET.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
    else:
        finding = get_object_or_404(Finding, id=finding_id)

    nb_matches = finding.evaluate_alert_rules()
    return JsonResponse({"status": "success", "nb_matches": nb_matches}, json_dumps_params={'indent': 2})


def update_finding_comments(request, finding_id):
    if request.method != 'POST':
        return JsonResponse({"status": "error", "reason": "invalid finding id"})

    new_comments = request.POST.get("comments", None)
    if new_comments is None:
        return JsonResponse({"status": "error", "reason": "invalid parameter"})

    if request.POST.get("raw", None) and request.POST.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
    else:
        finding = get_object_or_404(Finding, id=finding_id)

    finding.comments = new_comments
    finding.save()
    return JsonResponse({"status": "success"}, json_dumps_params={'indent': 2})


def update_finding_api(request, finding_id):
    is_raw = False
    if request.GET.get("raw", None) and request.GET.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
        is_raw = True
    else:
        finding = get_object_or_404(Finding, id=finding_id)

    allowed_fields = [f.name for f in Finding._meta.get_fields()]
    if request.method == 'GET':
        for field_key in request.GET.iterkeys():
            if field_key in allowed_fields:
                if is_raw:
                    Event.objects.create(message="Finding updated on field {}: from '{}' to '{}'".format(
                        field_key, getattr(finding, field_key), request.GET.get(field_key)
                    ), type="UPDATE", severity="INFO", rawfinding=finding)
                    setattr(finding, field_key, request.GET.get(field_key))

                    # Update the related Finding too
                    if field_key != "status":
                        _finding = Finding.objects.get(title=finding.title, asset=finding.asset)
                        Event.objects.create(message="Finding updated on field {}: from '{}' to '{}'".format(
                            field_key, getattr(finding, field_key), request.GET.get(field_key)
                        ), type="UPDATE", severity="INFO", finding=_finding)
                        setattr(_finding, field_key, request.GET.get(field_key))
                        _finding.save()

                else:
                    Event.objects.create(message="Finding updated on field {}: from '{}' to '{}'".format(
                        field_key, getattr(finding, field_key), request.GET.get(field_key)
                    ), type="UPDATE", severity="INFO", finding=finding)
                    setattr(finding, field_key, request.GET.get(field_key))

    finding.save()

    return JsonResponse({"status": "success"}, json_dumps_params={'indent': 2})


def export_finding_api(request, finding_id):
    if request.GET.get("raw", None) and request.GET.get("raw") == "true":
        finding = get_object_or_404(RawFinding, id=finding_id)
        prefix = "raw-"
    else:
        finding = get_object_or_404(Finding, id=finding_id)
        prefix = ""
    export_format = request.GET.get("format", 'csv')
    if not export_format or export_format not in ['json', 'html', 'stix', 'pdf', 'csv']:
        return JsonResponse({"status": "error", "reason": "bad format"}, json_dumps_params={'indent': 2})

    res = {}
    if export_format == 'json':
        res = model_to_dict(finding, exclude="scopes")
        res.update({"scopes": list(finding.scopes.values())})
        response = HttpResponse(
            json.dumps(res, default=json_serial),
            content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename=export_finding_{}{}.json'.format(prefix, finding.id)
        return response

    elif export_format == 'stix':
        res = {
            "type": "vulnerability",
            "name": finding.title,
            "description": finding.description,
            "id": "patrowl-{}{}".format(prefix, finding.id)
        }
        response = HttpResponse(
            json.dumps(res, default=json_serial),
            content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename=export_finding_{}{}.stix.json'.format(prefix, finding.id)
        return response

    elif export_format == 'html':
        return render(request, 'report-finding.html', {'finding': finding})

    elif export_format == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename=export_finding_{}{}.csv'.format(prefix, finding.id)
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'asset_value', 'asset_type',
            'engine_type', 'engine_name',
            'scan_title', 'scan_policy',
            'finding_id', 'finding_type', 'finding_status', 'finding_tags',
            'finding_severity', 'finding_description', 'finding_solution',
            'finding_hash', 'finding_creation_date', 'finding_risk_info',
            'finding_cvss', 'finding_links'
            ])
        if 'links' in finding.risk_info.keys():
            finding_links = ", ".join(finding.risk_info['links'])
        else:
            finding_links = None
        writer.writerow([
            finding.asset.value, finding.asset.type,
            finding.scan.engine_type.name, finding.scan.engine.name,
            finding.scan.title, finding.scan.engine_policy.name,
            finding.id, finding.type, finding.status, ','.join(finding.tags),
            finding.severity, finding.description, finding.solution,
            finding.hash, finding.created_at, finding.risk_info,
            finding.risk_info['cvss_base_score'], finding_links
        ])

        return response


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        # if the obj is uuid, we simply return the value of uuid
        return obj.hex
    raise TypeError("Type %s not serializable" % type(obj))
