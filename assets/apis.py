# -*- coding: utf-8 -*-

from django.http import JsonResponse, HttpResponse
from django.forms.models import model_to_dict
from django.utils.encoding import smart_str
from django.db.models import Value, CharField, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import FileSystemStorage

from wsgiref.util import FileWrapper
from rest_framework.decorators import api_view

from .models import Asset, AssetGroup, AssetCategory
from .models import AssetOwner, AssetOwnerContact, AssetOwnerDocument
from .forms import AssetOwnerContactForm, AssetOwnerDocumentForm
from app.settings import MEDIA_ROOT
from findings.models import Finding
from events.models import Event

import json
import csv
import os
import mimetypes
import datetime
import urllib


@api_view(['GET'])
def get_assets_stats_api(request):
    assets = Asset.objects.all()
    data = {
        "nb_assets": assets.count(),
        "nb_assets_high": assets.filter(criticity="high").count(),
        "nb_assets_medium": assets.filter(criticity="medium").count(),
        "nb_assets_low": assets.filter(criticity="low").count(),
        "nb_assets_info": assets.filter(criticity="info").count(),
    }
    return JsonResponse(data, json_dumps_params={'indent': 2})


@api_view(['GET'])
def get_asset_details_api(request, asset_name):
    asset = get_object_or_404(Asset, value=asset_name)

    # Asset details
    response = model_to_dict(asset, fields=[field.name for field in asset._meta.fields])

    # Related asset groups
    asset_groups = []
    for asset_group in asset.assetgroup_set.all():
        asset_group_dict = model_to_dict(asset_group, fields=[field.name for field in asset_group._meta.fields])
        asset_groups.append(asset_group_dict)
    response.update({
        "asset_groups": asset_groups
    })

    # Related findings
    findings = []
    for finding in asset.finding_set.all():
        finding_dict = model_to_dict(finding, fields=[field.name for field in finding._meta.fields])
        findings.append(finding_dict)
    response.update({
        "findings": findings
    })

    # Last 10 scans
    scans = []
    for scan in asset.scan_set.all()[:10]:
        scan_dict = model_to_dict(scan, fields=[field.name for field in scan._meta.fields])
        scans.append(scan_dict)

    response.update({
        "last10scans": scans
    })

    return JsonResponse(response, json_dumps_params={'indent': 2}, safe=False)


@api_view(['GET'])
def get_asset_trends_api(request, asset_id):
    asset = get_object_or_404(Asset, id=asset_id)
    data = []
    ticks_by_period = {'week': 7, 'month': 30, 'trimester': 120, 'year': 365}
    grade_levels = {'A': 6, 'B': 5, 'C': 4, 'D': 3, 'E': 2, 'F': 1, 'n/a': 0}

    # period = x-axis
    period = request.GET.get('period_by', None)
    if period not in ticks_by_period.keys():
        period = 7
    else:
        period = ticks_by_period[period]

    nb_ticks = int(request.GET.get('max_ticks', 15))
    if nb_ticks < period:
        delta = period // nb_ticks
    else:
        delta = 1

    startdate = datetime.datetime.today()
    for i in range(0, nb_ticks):
        enddate = startdate - datetime.timedelta(days=i*delta)
        findings_stats = {
            'total': 0,
            'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0,
            'new': 0,
            'risk_grade': 'n/a',
            'date': str(enddate.date())}
        for f in asset.finding_set.filter(created_at__lte=enddate):
            findings_stats['total'] = findings_stats.get('total') + 1
            findings_stats[f.severity] = findings_stats.get(f.severity) + 1
            if f.status == 'new':
                findings_stats['new'] = findings_stats.get('new') + 1

        if findings_stats['total'] != 0:
            findings_stats['risk_grade'] = grade_levels[asset.get_risk_grade(history=i)]
            # findings_stats['risk_grade'] = grade_levels[asset.get_risk_grade(history=i)['grade']]
        else:
            findings_stats['risk_grade'] = 0
        data.append(findings_stats)

    return JsonResponse(data[::-1], json_dumps_params={'indent': 2}, safe=False)


@api_view(['GET'])
def list_assets_api(request):
    q = request.GET.get("q", None)
    if q:
        assets = list(Asset.objects
            .filter(Q(value__icontains=q) | Q(name__icontains=q))
            .annotate(format=Value("asset", output_field=CharField()))
            .values('id', 'value', 'format', 'name'))
        assetgroups = list(AssetGroup.objects.filter(name__icontains=q)
            .extra(select={'value': 'name'})
            .annotate(format=Value("assetgroup", output_field=CharField()))
            .values('id', 'value', 'format', 'name'))
    else:
        assets = list(Asset.objects
            .annotate(format=Value("asset", output_field=CharField()))
            .values('id', 'value', 'format', 'name'))
        assetgroups = list(AssetGroup.objects
            .extra(select={'value': 'name'})
            .annotate(format=Value("assetgroup", output_field=CharField()))
            .values('id', 'value', 'format', 'name'))
    return JsonResponse(assets + assetgroups, safe=False)
#
#
# @api_view(['GET', 'PUT', 'DELETE'])
# @csrf_exempt
# def asset_details(request, asset_id):
#     asset = get_object_or_404(Asset, id=asset_id)
#
#     if request.method == 'GET':
#         ser = AssetSerializer(asset)
#         return JsonResponse(ser.data, safe=False)
#
#     elif request.method == 'PUT':
#         data = JSONParser().parse(request)
#         ser = AssetSerializer(asset, data=data)
#         if ser.is_valid():
#             ser.save()
#             return JsonResponse(ser.data)
#         return JsonResponse(ser.errors, status=400)
#
#     elif request.method == 'DELETE':
#         asset.delete()
#         return HttpResponse(status=204)


@api_view(['GET'])
def refresh_all_asset_grade_api(request):
    for asset in Asset.objects.all():
        asset.calc_risk_grade()
    for assetgroup in AssetGroup.objects.all():
        assetgroup.calc_risk_grade()
    return redirect('list_assets_view')


@api_view(['GET'])
def refresh_asset_grade_api(request, asset_id=None):
    if asset_id:
        asset = get_object_or_404(Asset, id=asset_id)
        asset.calc_risk_grade()
    else:
        # update all
        for asset in Asset.objects.all():
            asset.calc_risk_grade()
    return redirect('list_assets_view')


@api_view(['GET'])
def refresh_assetgroup_grade_api(request, assetgroup_id=None):
    if assetgroup_id:
        assetgroup = get_object_or_404(AssetGroup, id=assetgroup_id)
        assetgroup_id.calc_risk_grade()
    else:
        # update all
        for assetgroup in AssetGroup.objects.all():
            assetgroup.calc_risk_grade()
    return JsonResponse({"status": "success"}, safe=False)


@api_view(['GET'])
def export_assets_api(request, assetgroup_id=None):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="patrowl_assets.csv"'
    writer = csv.writer(response, delimiter=';')

    assets = []
    if assetgroup_id:
        asset_group = AssetGroup.objects.get(id=assetgroup_id)
        for asset in asset_group.assets.all():
            assets.append(asset)
    else:
        # assets = Asset.objects.filter(owner_id=request.user.id)
        assets = Asset.objects.all()

    writer.writerow(['asset_value', 'asset_name', 'asset_type', 'asset_description', 'asset_criticity', 'asset_tags'])
    for asset in assets:
        writer.writerow([smart_str(asset.value), asset.name, asset.type, smart_str(asset.description), asset.criticity, ",".join([a.value for a in asset.categories.all()])])
    return response


@api_view(['POST'])
@csrf_exempt  # not secure!!!
def delete_assets_api(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error'})

    assets = json.loads(request.body)
    for asset_id in assets:
        a = Asset.objects.get(id=asset_id)
        a.delete()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


@api_view(['GET'])
def get_asset_tags_api(request):
    tags = AssetCategory.objects.values_list('value', flat=True)
    return JsonResponse(list(tags), safe=False)


def _add_asset_tags(asset, new_value):
    new_tag = AssetCategory.objects.filter(value__iexact=new_value).first()
    if not new_tag:
        if not AssetCategory.objects.filter(value="Custom").first():
            AssetCategory.objects.create(value="Custom", comments="custom tags")
        custom_tags = AssetCategory.objects.get(value="Custom")
        new_tag = custom_tags.add_child(value=new_value)

        Event.objects.create(message="[AssetCategory/add_asset_tags()] New AssetCategory created: '{}' with id: {}.".format(new_value, new_tag.id),
                     type="INFO", severity="INFO")

    if new_tag not in asset.categories.all():  # Not already set
        # Check if futures parents has been already selected. If True: delete them
        cats = list(asset.categories.all().values_list('value', flat=True))
        if new_tag.get_all_parents():
            pars = [t.value for t in new_tag.get_all_parents()]
        else:
            pars = []
        intersec_par = set(pars).intersection(cats)
        if intersec_par:
            asset.categories.remove(AssetCategory.objects.get(value=list(intersec_par)[0]))

        # Check if current tags are not children of the new tag.
        # If True: delete them
        chis = [t.value for t in new_tag.get_children()]
        for c in set(chis).intersection(cats):
            asset.categories.remove(AssetCategory.objects.get(value=c))

    return new_tag


@api_view(['POST'])
def add_asset_tags_api(request, asset_id):
    if request.method == 'POST':
        asset = get_object_or_404(Asset, id=asset_id)
        new_tag = _add_asset_tags(asset, request.POST.getlist('input-search-tags')[0])
        asset.categories.add(new_tag)
        asset.save()
        # messages.success(request, 'Tag successfully added!')

    return redirect('detail_asset_view', asset_id=asset_id)


@api_view(['POST'])
def add_asset_group_tags_api(request, assetgroup_id):
    if request.method == 'POST':
        asset_group = get_object_or_404(AssetGroup, id=assetgroup_id)
        new_tag = _add_asset_tags(asset_group, request.POST.getlist('input-search-tags')[0])
        asset_group.categories.add(new_tag)
        # messages.success(request, 'Tag successfully added!')

    return redirect('detail_asset_group_view', assetgroup_id=assetgroup_id)


@api_view(['POST'])
def delete_asset_tags_api(request, asset_id):
    tag_id = request.POST.get('tag_id', None)
    try:
        tag = AssetCategory.objects.get(id=tag_id)
    except AssetCategory.DoesNotExist:
        Event.objects.create(message="[AssetCategory/delete_asset_tags_api()] Asset with id '{}' was not found.".format(asset_id),
                     type="ERROR", severity="ERROR")
        return redirect('detail_asset_view', asset_id=asset_id)

    if request.method == 'POST':
        asset = get_object_or_404(Asset, id=asset_id)
        asset.categories.remove(tag)  # @todo: check error cases

    return redirect('detail_asset_view', asset_id=asset_id)


@api_view(['POST'])
def delete_asset_group_tags_api(request, assetgroup_id):
    tag_id = request.POST.get('tag_id', None)
    try:
        tag = AssetCategory.objects.get(id=tag_id)
    except AssetCategory.DoesNotExist:
        Event.objects.create(message="[AssetCategory/delete_asset_group_tags_api()] AssetGroup with id '{}' was not found.".format(assetgroup_id),
                     type="ERROR", severity="ERROR")
        return redirect('detail_asset_group_view', assetgroup_id=assetgroup_id)

    if request.method == 'POST':
        assetgroup = get_object_or_404(AssetGroup, id=assetgroup_id)
        assetgroup.categories.remove(tag)  # @todo: check error cases

    return redirect('detail_asset_group_view', assetgroup_id=assetgroup_id)


@api_view(['GET'])
def get_asset_report_html_api(request, asset_id):
    asset = get_object_or_404(Asset, id=asset_id)

    findings_tmp = list()
    findings_stats = {}

    # @todo: invert loops
    for sev in ["critical", "high", "medium", "low", "info"]:
        tmp = Finding.objects.filter(asset=asset.id, severity=sev).order_by('type')
        if tmp.count() > 0:
            findings_tmp += tmp
        findings_stats.update({sev: tmp.count()})
    findings_stats.update({"total": len(findings_tmp)})

    return render(request, 'report-asset-findings.html', {
        'asset': asset,
        'findings': findings_tmp,
        'findings_stats': findings_stats})


@api_view(['GET'])
def get_asset_report_json_api(request, asset_id):
    asset = get_object_or_404(Asset, id=asset_id)

    findings_tmp = list()
    findings_stats = {}

    # @todo: invert loops
    for sev in ["critical", "high", "medium", "low", "info"]:
        tmp = Finding.objects.filter(asset=asset.id, severity=sev).order_by('type')
        findings_stats.update({sev: tmp.count()})
        if tmp.count() > 0:
            for f in tmp:
                tmp_f = model_to_dict(f, exclude=["scopes"])
                tmp_f.update({"scopes": [ff.name for ff in f.scopes.all()]})
                findings_tmp.append(tmp_f)

    asset_dict = model_to_dict(asset, exclude=["categories"])
    asset_tags = [tag.value for tag in asset.categories.all()]
    asset_dict.update({"categories": asset_tags})

    return JsonResponse({
        'asset': asset_dict,
        'findings': findings_tmp,
        'findings_stats': findings_stats
        }, safe=False)


@api_view(['GET'])
def get_asset_group_report_html_api(request, asset_group_id):
    asset_group = get_object_or_404(AssetGroup, id=asset_group_id)
    return render(request, 'report-assetgroup-findings.html', {
        'asset_group': asset_group})


@api_view(['GET'])
def get_asset_owner_doc_api(request, asset_owner_doc_id):
    doc = get_object_or_404(AssetOwnerDocument, id=asset_owner_doc_id)
    fp = urllib.unquote(doc.filepath)
    fn = urllib.unquote(doc.filename)

    file_wrapper = FileWrapper(file(fp, 'rb'))
    file_mimetype = mimetypes.guess_type(fp)
    response = HttpResponse(file_wrapper, content_type=file_mimetype)
    response['X-Sendfile'] = fp
    response['Content-Length'] = os.stat(fp).st_size
    response['Content-Disposition'] = 'attachment; filename=%s' % smart_str(fn)
    return response


@api_view(['POST'])
def edit_asset_owner_comments_api(request, asset_owner_id):
    if request.method != "POST" or not request.POST.get('new_comments', None):
        return HttpResponse(status=400)

    owner = get_object_or_404(AssetOwner, id=asset_owner_id)
    owner.comments = request.POST.get('new_comments')
    owner.save()
    return HttpResponse(status=200)


@api_view(['POST'])
def delete_asset_owner_contact_api(request, asset_owner_id):
    # if request.method != 'POST':
    #     return HttpResponse(status=400)

    contact = get_object_or_404(AssetOwnerContact, id=asset_owner_id)
    contact.delete()
    return redirect('details_asset_owner_view', asset_owner_id=asset_owner_id)


@api_view(['POST'])
def delete_asset_owner_document_api(request, asset_owner_id):
    if request.method != 'POST' or not request.POST.get('doc_id', None):
        return HttpResponse(status=400)

    doc_id = request.POST.get('doc_id')
    document = get_object_or_404(AssetOwnerDocument, id=doc_id)
    document.delete()
    return redirect('details_asset_owner_view', asset_owner_id=asset_owner_id)


@api_view(['POST'])
def add_asset_owner_document_api(request, asset_owner_id):
    owner = get_object_or_404(AssetOwner, id=asset_owner_id)
    form = AssetOwnerDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        doc_args = {
            'doctitle': form.cleaned_data['doctitle'],
            'tlp_color': form.cleaned_data['tlp_color'],
            'comments': form.cleaned_data['comments'],
            'owner': request.user,
        }
        if request.FILES:
            # Create /media/ folders if not exists
            if not os.path.exists(MEDIA_ROOT+"/owners_docs"):
                os.makedirs(MEDIA_ROOT+"/owners_docs")
            if not os.path.exists(MEDIA_ROOT+"/owners_docs/"+str(request.user.id)):
                os.makedirs(MEDIA_ROOT+"/owners_docs/"+str(request.user.id))

            myfile = request.FILES['file']
            fs = FileSystemStorage(location=MEDIA_ROOT+"/owners_docs/"+str(request.user.id), base_url=MEDIA_ROOT+"/owners_docs/"+str(request.user.id))
            filename = fs.save(myfile.name, myfile)
            uploaded_file_url = fs.url(filename)
            doc_args.update({
                'filename': filename,
                'filepath': uploaded_file_url
            })

        doc = AssetOwnerDocument(**doc_args)
        doc.save()

        # Add this document to the asset owner
        owner.documents.add(doc)
        owner.save()

    return redirect('details_asset_owner_view', asset_owner_id=asset_owner_id)


@api_view(['POST'])
def add_asset_owner_contact_api(request, asset_owner_id):
    owner = get_object_or_404(AssetOwner, id=asset_owner_id)

    form = AssetOwnerContactForm(request.POST)
    if form.is_valid():
        contact_args = {
            'name': form.cleaned_data['name'],
            'title': form.cleaned_data['title'],
            'email': form.cleaned_data['email'],
            'phone': form.cleaned_data['phone'],
            'address': form.cleaned_data['address'],
            'url': form.cleaned_data['url'],
            'comments': form.cleaned_data['comments'],
            'owner': request.user,
        }

        contact = AssetOwnerContact(**contact_args)
        contact.save()

        # Add this contact to the asset owner
        owner.contacts.add(contact)
        owner.save()

    return redirect('details_asset_owner_view', asset_owner_id=asset_owner_id)
