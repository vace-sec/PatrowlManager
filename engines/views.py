# -*- coding: utf-8 -*-

from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.core.files import File
from django.shortcuts import render, redirect, get_object_or_404
from django.forms.models import model_to_dict
from django.views.decorators.csrf import csrf_exempt
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from .models import Engine, EngineInstance, EnginePolicy, EnginePolicyScope
from .tasks import refresh_engines_status_task, get_engine_status_task
from .forms import EnginePolicyForm, EngineInstanceForm, EngineForm, EnginePolicyImportForm
from scans.models import Scan
from scans.views import _update_celerybeat
import os
import requests
import json
import time
import base64


def list_engines(request):
    list_engines = []
    for engine in Engine.objects.all():
        list_engines.append({
            "id": getattr(engine, 'id'),
            "name": getattr(engine, 'name'),
            "description": getattr(engine, 'description'),
            "created_at": getattr(engine, 'created_at'),
            "updated_at": getattr(engine, 'updated_at')
        })
    return JsonResponse(list_engines, safe=False)


def list_instances_by_id(request, engine_id):
    list_instances = []
    for instance in EngineInstance.objects.filter(engine=engine_id):
        list_instances.append({
            "id": getattr(instance, 'id'),
            "name": getattr(instance, 'name'),
            "version": getattr(instance, 'version'),
            "api_url": getattr(instance, 'api_url'),
            "created_at": getattr(instance, 'created_at'),
            "updated_at": getattr(instance, 'updated_at')
        })
    return JsonResponse(list_instances, safe=False)


def list_instances_by_name(request, engine_name):
    list_instances = []
    for instance in EngineInstance.objects.filter(engine__name=str(engine_name).upper()):
        list_instances.append({
            "id": getattr(instance, 'id'),
            "name": getattr(instance, 'name'),
            "version": getattr(instance, 'version'),
            "api_url": getattr(instance, 'api_url'),
            "created_at": getattr(instance, 'created_at'),
            "updated_at": getattr(instance, 'updated_at')
        })
    return JsonResponse(list_instances, safe=False)


def list_policies_by_engine(request, engine_name):
    list_policies = []
    for policy in EnginePolicy.objects.filter(owner_id=request.user.id):
        tmp_policy = model_to_dict(policy, exclude='file')
        tmp_policy['filename'] = policy.file.name
        list_policies.append(tmp_policy)
    return JsonResponse(list_policies, safe=False)


def get_engine_status(request, engine_id):
    res = {}
    inst = get_object_or_404(EngineInstance, id=engine_id)

    # try:
    #     resp = requests.get(url=str(inst.api_url)+"status", verify=False)
    #
    #     if resp.status_code == 200:
    #         engine_status = json.loads(resp.text)['status'].strip().upper()
    #         res.update({"id": engine_id, "status": engine_status})
    #         inst.status = engine_status
    #         #print("INFO: New available Engine Instance: {}".format(inst))
    #     else:
    #         inst.status = "STOPPED"
    # except requests.exceptions.RequestException:
    #     inst.status = "ERROR"
    #     res.update({"id": engine_id, "status": "error"})
    #
    # inst.save()
    get_engine_status_task.apply_async(
        args=[inst.id], queue='default', retry=False)
    return JsonResponse(res)


#Todo: to review
def get_engine_info(request, engine_id):
    res = {}
    engine = get_object_or_404(EngineInstance, id=engine_id)
    engine_info = None
    try:
        resp = requests.get(url=str(engine.api_url)+"info", verify=False)

        if resp.status_code == 200:
            engine_info = json.loads(resp.text)
            engine_info["api_url"] = engine.api_url

    except requests.exceptions.RequestException:
        res.update({"engine_id": engine_id, "status": "error"})

    res.update({"engine_id": engine_id, "info": engine_info})
    return JsonResponse(res)


def refresh_engines_status(request):
    refresh_engines_status_task.apply_async(
        queue='default',
        retry=False
    )

    return JsonResponse({"status": "success"})


def toggle_autorefresh_engine_status(request):
    autorefresh_task_name = '[PO] Auto-refresh engines status'
    autorefresh_tasks = PeriodicTask.objects.filter(name=autorefresh_task_name)
    if autorefresh_tasks.count() == 0:
        schedule, created = IntervalSchedule.objects.get_or_create(
            every=5,
            period=IntervalSchedule.SECONDS,
        )

        periodic_task = PeriodicTask.objects.create(
            interval=schedule,
            name=autorefresh_task_name,
            task='engines.tasks.refresh_engines_status_task',
            queue='default',
            last_run_at=None
        )
        periodic_task.enabled = True
        periodic_task.save()

        _update_celerybeat()
        return JsonResponse({"autorefresh_task_status": True}, status=200)
    else:
        autorefresh_task = autorefresh_tasks.first()
        autorefresh_task.enabled = not autorefresh_task.enabled
        autorefresh_task.save()

        _update_celerybeat()
        return JsonResponse({"autorefresh_task_status": autorefresh_task.enabled}, status=200)


def list_engines_view(request):
    engines = EngineInstance.objects.all().order_by('name')
    autorefresh_task = PeriodicTask.objects.filter(name='[PO] Auto-refresh engines status')
    if autorefresh_task.count() > 0:
        autorefresh_status = autorefresh_task.first().enabled
    else:
        autorefresh_status = False
    return render(request, 'list-scan-engines.html',
                  {'engines': engines, 'autorefresh_status': autorefresh_status})


def list_engines_api(requests):
    engines = []
    for engine in EngineInstance.objects.all().order_by("name"):
        engines.append({
            "id": engine.id,
            "name": engine.name,
            "status": engine.status,
            "enabled": engine.enabled,
            "version": engine.version,
            "type": engine.engine.name
           })
    running_scans = Scan.objects.filter(status__in=["enqueued", "started"]).count()
    return JsonResponse(
        {
            "engines": engines,
            "running_scans": running_scans
        }, safe=False)


@csrf_exempt
def toggle_engine_status(request, engine_id):
    engine_instance = get_object_or_404(EngineInstance, id=engine_id)
    engine_instance.enabled = not engine_instance.enabled
    engine_instance.save()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


def add_engine_view(request):
    form = None

    if request.method == 'GET':
        form = EngineInstanceForm()
    elif request.method == 'POST':
        form = EngineInstanceForm(request.POST)

        if form.is_valid():
            engine_args = {
                'engine': form.cleaned_data['engine'],
                'name': form.cleaned_data['name'],
                'api_url': form.cleaned_data['api_url'],
                'enabled': form.cleaned_data['enabled'] is True,
                'authentication_method': form.cleaned_data['authentication_method'],
                'api_key': form.cleaned_data['api_key'],
                'username': form.cleaned_data['username'],
                'password': form.cleaned_data['password'],
            }

            engine = EngineInstance(**engine_args)
            engine.save()
            messages.success(request, 'Creation submission successful')
            return redirect('list_engines_view')

    return render(request, 'add-scan-engine.html', {'form': form})


def delete_engine_view(request, engine_id):
    engine = get_object_or_404(EngineInstance, id=engine_id)

    if request.method == 'POST':
        engine.delete()
        messages.success(request, 'Engine successfully deleted!')
        return redirect('list_engines_view')

    return render(request, 'delete-scan-engine.html', {'engine': engine})


def info_engine_api(request, engine_id):
    engine = get_object_or_404(EngineInstance, id=engine_id)

    engine_infos = None
    current_scans = None
    try:
        resp = requests.get(url=str(engine.api_url)+"info", verify=False)

        if resp.status_code == 200:
            engine_infos = json.loads(resp.text)
    except requests.exceptions.RequestException:
        pass

    if not engine_infos:
        engine_infos = {"status": "ERROR"}
    else:
        try:
            resp = requests.get(url=str(engine.api_url)+"status", verify=False)

            if resp.status_code == 200:
                current_scans = json.loads(resp.text)['scans']
        except requests.exceptions.RequestException:
            pass

    nb_scans = Scan.objects.filter(engine=engine).count()

    return JsonResponse({
        'engine': model_to_dict(engine),
        'engine_infos': engine_infos,
        'nb_scans': nb_scans,
        'current_scans': current_scans},
        json_dumps_params={'indent': 2}
    )


def edit_engine_view(request, engine_id):
    engine = get_object_or_404(EngineInstance, id=engine_id)
    form = EngineInstanceForm()

    if request.method == 'GET':
        form = EngineInstanceForm(instance=engine)
    elif request.method == 'POST':
        form = EngineInstanceForm(request.POST, instance=engine)
        if form.is_valid():
            engine.engine = form.cleaned_data['engine']
            engine.name = form.cleaned_data['name']
            engine.api_url = form.cleaned_data['api_url']
            engine.enabled = form.cleaned_data['enabled'] is True
            engine.authentication_method = form.cleaned_data['authentication_method']
            engine.api_key = form.cleaned_data['api_key']
            engine.username = form.cleaned_data['username']
            engine.password = form.cleaned_data['password']
            engine.save()
            messages.success(request, 'Update submission successful')
            return redirect('list_engines_view')

    return render(request, 'edit-scan-engine.html', {'form': form, 'engine_id': engine.id})


def list_policies_view(request):
    policies = EnginePolicy.objects.all().order_by("engine__name", "name")
    return render(request, 'list-engine-policies.html', {'policies': policies})


def export_policy(request, policy_id):
    if request.method == 'GET':
        policy = get_object_or_404(EnginePolicy, id=policy_id)
        response = JsonResponse({"policies": [policy.as_dict()]})
        response['Content-Disposition'] = 'attachment; filename=enginepolicy_'+str(policy.id)+'.json'
        return response
    else:
        return redirect('list_policies_view')


@csrf_exempt
def export_policies(request):
    if request.method == 'GET' and request.GET.get('all', None):
        policies = EnginePolicy.objects.all()
    elif request.method == 'POST':
        policies = []
        for policy_id in json.loads(request.body):
            policies.append(EnginePolicy.objects.get(id=policy_id))
    else:
        return redirect('list_policies_view')

    response = JsonResponse({"policies": [policy.as_dict() for policy in policies]})
    response['Content-Disposition'] = 'attachment; filename=enginepolicies_'+str(int(time.time() * 1000))+'.json'
    return response


def import_policies_view(request):
    if request.method == 'GET':
        form = EnginePolicyImportForm()
    elif request.method == 'POST':
        form = EnginePolicyImportForm(request.POST, request.FILES)
        if form.is_valid():
            # store the file in /media/imports/<owner_id>/<tmp_file>
            policies_path = settings.MEDIA_ROOT + "/policies/_imports/"
            if not os.path.exists(policies_path):
                os.makedirs(policies_path)

            policies_file = policies_path + request.FILES['file'].name
            with open(policies_file, 'wb') as destfile:
                for chunk in request.FILES['file'].chunks():
                    destfile.write(chunk)
            destfile.close()

            destfile = open(policies_file).read()
            ep_fields = ['description', 'scope_names', 'name', 'engine_name', 'options', 'file']

            for policy in json.loads(destfile)['policies']:
                # check if all keys are set
                if not set(ep_fields).issubset(policy.keys()):
                    messages.error(request, 'Error: missing args in policy "{}".'.format(policy['name']))
                    continue

                # check if engine_name exists
                if not Engine.objects.filter(name__iexact=policy['engine_name']):
                    messages.error(request, 'Error: policy "{}" defines an unknown engine ("{}").'.format(
                        policy['name'], policy['engine_name']))
                    continue

                # check if scope names exist
                has_error = False
                for scope in policy['scope_names']:
                    if not EnginePolicyScope.objects.filter(name__iexact=scope):
                        messages.error(request, 'Error: policy "{}" defines an unknown engine scope ("{}").'.format(policy['name'], scope))
                        has_error = True
                        continue
                if has_error:
                    continue

                # check if policy_name exists
                if EnginePolicy.objects.filter(name__iexact=policy['name']):
                    messages.error(request, 'Error: policy "{}" defines already exists (name check).'.format(policy['name']))
                    continue

                # All is OK: create new engine policy
                new_policy = EnginePolicy(
                    name=policy['name'],
                    description=policy['description'],
                    options=policy['options'],
                    engine=Engine.objects.get(name__iexact=policy['engine_name']),
                    owner=request.user,
                    default=False,
                    is_default=False
                )
                new_policy.save()
                for scope in policy['scope_names']:
                    new_policy.scopes.add(EnginePolicyScope.objects.get(name__iexact=scope))

                if policy["file"]:
                    # decode the file and store it in the right folder
                    fp_policy = settings.MEDIA_ROOT + "/policies/" + policy["engine_name"].upper() + "/" #+ str(request.user.id)
                    if not os.path.exists(fp_policy):
                        os.makedirs(fp_policy)
                    fp_policy_engine = fp_policy + str(request.user.id)
                    if not os.path.exists(fp_policy_engine):
                        os.makedirs(fp_policy_engine)
                    fh = open(settings.MEDIA_ROOT + "/policies/" + policy["engine_name"].upper() + "/" + str(request.user.id) + "/" + policy["file"]["filename"], "wb")
                    fh.write(base64.b64decode(policy["file"]["content"]))
                    fh.close()

                    # assign it to the new policy object
                    fh = open(settings.MEDIA_ROOT + "/policies/" + policy["engine_name"].upper() + "/" + str(request.user.id) + "/" + policy["file"]["filename"])
                    new_policy.file.save(policy["file"]["filename"], File(fh))
                    fh.close()

                new_policy.save()
                messages.success(request, 'policy "{}" successfully imported.'.format(policy["name"]))
            return redirect('list_policies_view')

    return render(request, 'import-engine-policies.html', {'form': form})


def add_policy_view(request):
    form = None

    if request.method == 'GET':
        form = EnginePolicyForm()
    elif request.method == 'POST':
        form = EnginePolicyForm(request.POST, request.FILES)

        if form.is_valid():
            policy_args = {
                'engine': form.cleaned_data['engine'],
                'name': form.cleaned_data['name'],
                'description': form.cleaned_data['description'],
                'options': form.cleaned_data['options'],
                'owner': request.user,
            }

            policy = EnginePolicy(**policy_args)
            if request.FILES:
                policy.file = request.FILES['file']
            policy.save()
            policy.scopes = form.cleaned_data['scopes']
            policy.save()
            messages.success(request, 'Creation submission successful')
            return HttpResponseRedirect('list')

    return render(request, 'add-engine-policy.html', {'form': form})


def delete_policy_view(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)

    if request.method == 'POST':
        policy.delete()
        messages.success(request, 'Scan policy successfully deleted!')
        return redirect('list_policies_view')

    return render(request, 'delete-engine-policy.html', {'policy': policy})


def edit_policy_view(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)

    # check the ownership of the asset
    # if policy.owner != request.user:
    #     return HttpResponse(status=403)

    form = EnginePolicyForm()

    if request.method == 'GET':
        form = EnginePolicyForm(None, instance=policy)
    elif request.method == 'POST':
        form = EnginePolicyForm(request.POST, request.FILES)
        if form.is_valid():
            policy.engine = form.cleaned_data['engine']
            policy.name = form.cleaned_data['name']
            policy.description = form.cleaned_data['description']
            policy.scopes = form.cleaned_data['scopes']
            policy.is_default = form.cleaned_data['is_default'] is True
            policy.default = form.cleaned_data['is_default'] is True
            policy.status = "active"
            policy.options = form.cleaned_data['options']

            if 'file-clear' in form.data.keys() and form.data['file-clear'] == 'on':
                if policy.file.name and os.path.isfile(policy.file.path):
                    os.remove(policy.file.path)
                    policy.file = None
            if request.FILES:
                policy.file = request.FILES['file']

            policy.save()
            messages.success(request, 'Update submission successful')
            return redirect('list_policies_view')

    return render(request, 'edit-engine-policy.html', {'form': form, 'policy_id': policy.id})


def duplicate_policy_view(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)

    # check the ownership of the asset
    # if policy.owner != request.user:
    #     return HttpResponse(status=403)

    policy_args = {
        'engine': policy.engine,
        'name': policy.name + " (copy)",
        'description': policy.description,
        'owner': policy.owner,
        'default': policy.default,
        'options': policy.options,
        'file': policy.file,
        'status': policy.status,
        'is_default': policy.is_default
    }

    new_policy = EnginePolicy(**policy_args)
    new_policy.save()
    new_policy.scopes = policy.scopes.all() #M2M field
    new_policy.save()
    messages.success(request, 'Duplicate submission successful')

    return redirect('list_policies_view')


def list_engine_types(request):
    engines = Engine.objects.all().values()[::1]
    return JsonResponse(engines, json_dumps_params={'indent': 2}, safe=False)


def list_engine_types_view(request):
    engines = Engine.objects.all().order_by("name")
    for eng in engines:
        if eng.allowed_asset_types:
            eng.allowed_asset_types = ", ".join(eval(eng.allowed_asset_types))
    return render(request, 'list-engines.html', {'engines': engines})


def add_engine_types_view(request):
    form = None

    if request.method == 'GET':
        form = EngineForm()
    elif request.method == 'POST':
        form = EngineForm(request.POST)
        if form.is_valid():
            engine_args = {
                'name': form.cleaned_data['name'],
                'description': form.cleaned_data['description'],
                'allowed_asset_types': form.data.getlist('allowed_asset_types')
            }

            engine = Engine(**engine_args)
            engine.save()
            messages.success(request, 'Creation submission successful')
            return redirect('list_engine_types_view')

    return render(request, 'add-engine.html', {'form': form})


def edit_engine_type_view(request, engine_id):
    engine = get_object_or_404(Engine, id=engine_id)
    form = None

    if request.method == 'GET':
        form = EngineForm(instance=engine, initial={
            'allowed_asset_types': eval(engine.allowed_asset_types)
        })
    elif request.method == 'POST':
        form = EngineForm(request.POST, instance=engine)
        if form.is_valid():
            engine.name = form.cleaned_data['name']
            engine.description = form.cleaned_data['description']
            engine.save()
            messages.success(request, 'Update submission successful')
            return redirect('list_engine_types_view')

    return render(request, 'edit-engine.html', {
        'form': form,
        'engine_id': engine.id
    })


def delete_engine_type_view(request, engine_id):
    engine = get_object_or_404(Engine, id=engine_id)

    if request.method == 'POST':
        engine.delete()
        messages.success(request, 'Engine type successfully deleted!')
        return redirect('list_engine_types_view')

    return render(request, 'delete-engine.html', {'engine': engine})
