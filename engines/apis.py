# -*- coding: utf-8 -*-

from django.http import JsonResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404
from django.forms.models import model_to_dict
from django_celery_beat.models import PeriodicTask, IntervalSchedule
from rest_framework.decorators import api_view
from .models import Engine, EngineInstance, EnginePolicy
from .tasks import refresh_engines_status_task
from .tasks import get_engine_status_task, get_engine_info_task
from scans.models import Scan
from scans.views import _update_celerybeat
import requests
import json
import time


@api_view(['GET'])
def list_engines_api(request):
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


@api_view(['GET'])
def list_instances_by_id_api(request, engine_id):
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


@api_view(['GET'])
def list_instances_by_name_api(request, engine_name):
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


@api_view(['GET'])
def list_policies_by_engine_api(request, engine_name):
    list_policies = []
    for policy in EnginePolicy.objects.filter(owner_id=request.user.id):
        list_policies.append(policy.as_dict())
    return JsonResponse(list_policies, safe=False)


@api_view(['GET'])
def get_engine_status_api(request, engine_id):
    inst = get_object_or_404(EngineInstance, id=engine_id)
    get_engine_status_task.apply_async(
        args=[inst.id], queue='default', retry=False)
    return JsonResponse({"status": "enqueued"})


@api_view(['PATCH'])
def toggle_engine_status_api(request, engine_id):
    engine_instance = get_object_or_404(EngineInstance, id=engine_id)
    engine_instance.enabled = not engine_instance.enabled
    engine_instance.save()

    return JsonResponse({'status': 'success'}, json_dumps_params={'indent': 2})


@api_view(['GET'])
def get_engine_info_api(request, engine_id):
    inst = get_object_or_404(EngineInstance, id=engine_id)
    get_engine_info_task.apply_async(
        args=[inst.id], queue='default', retry=False)
    return JsonResponse({"status": "enqueued"})


@api_view(['GET'])
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

    nb_scans = Scan.objects.filter(engine=engine).count()

    return JsonResponse({
        'engine': model_to_dict(engine),
        'engine_infos': engine_infos,
        'nb_scans': nb_scans,
        'current_scans': current_scans},
        json_dumps_params={'indent': 2}
    )


@api_view(['GET'])
def refresh_engines_status_api(request):
    refresh_engines_status_task.apply_async(
        queue='default',
        retry=False
    )
    return JsonResponse({"status": "success"})


@api_view(['GET'])
def toggle_autorefresh_engine_status_api(request):
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


@api_view(['GET'])
def list_engines_intances_api(requests):
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


@api_view(['GET'])
def export_policy_api(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)
    response = JsonResponse({"policies": [policy.as_dict()]})
    response['Content-Disposition'] = 'attachment; filename=enginepolicy_'+str(policy.id)+'.json'
    return response


@api_view(['GET', 'POST'])
def export_policies_api(request):
    if request.method == 'GET' and request.GET.get('all', None):
        policies = EnginePolicy.objects.all()
    elif request.method == 'POST':
        policies = []
        for policy_id in request.data:
            policies.append(EnginePolicy.objects.get(id=policy_id))

    response = JsonResponse({"policies": [policy.as_dict() for policy in policies]})
    response['Content-Disposition'] = 'attachment; filename=enginepolicies_'+str(int(time.time() * 1000))+'.json'
    return response


@api_view(['DELETE'])
def delete_policy_api(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)

    policy.delete()
    messages.success(request, 'Scan policy successfully deleted!')
    return JsonResponse({"status": "deleted"})


@api_view(['PUT'])
def duplicate_policy_api(request, policy_id):
    policy = get_object_or_404(EnginePolicy, id=policy_id)

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

    return JsonResponse({"status": "duplicated"})


@api_view(['GET'])
def list_engine_types_api(request):
    engines = Engine.objects.all().values()[::1]
    return JsonResponse(engines, json_dumps_params={'indent': 2}, safe=False)
