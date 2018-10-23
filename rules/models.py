from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.postgres.fields import JSONField
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from events.models import Event
from settings.models import Setting
from django_celery_beat.models import PeriodicTask

import json
import requests
import uuid
from thehive4py.api import TheHiveApi
from thehive4py.models import Alert, AlertArtifact

RULE_SCOPES = (
    ('asset', 'Asset'),
    ('finding', 'Finding'),
    ('scan', 'Scan'),
)

RULE_SCOPE_ATTRIBUTES = {
    "asset": {
        # 'value':        {"type": "numeric"},
        'value':        {"type": "text"},
        'name':         {"type": "text"},
        'type':         {"type": "list", "values": ['ip', 'domain', 'url']},
        'description':  {"type": "text"},
        'criticity':    {"type": "list", "values": ['low', 'medium', 'high']}
        },
    "finding": {
        'title':        {"type": "text"},
        'description':  {"type": "text"},
        'type':         {"type": "text"},
        'hash':         {"type": "text"},
        'solution':     {"type": "text"},
        'severity':     {"type": "list", "values": ['info', 'low', 'medium', 'high', 'critical']},
        'status':       {"type": "list", "values": ['new', 'ack']},
        # 'tags':         {"type": "in_list"},
        },
    "scan": {
        'status': {"type": "text"},
        },
}

RULE_TARGETS = (
    ('event',   'Patrowl event'),
    ('logfile', 'To logfile'),
    ('email',   'Send email'),
    ('thehive', 'To TheHive (event'),
    ('splunk',  'To Splunk'),
    ('slack',   'To Slack'),
)

RULE_TRIGGERS = (
    ('ondemand', 'On-demand'),
    ('auto',     'Auto'),
    ('periodic', 'Periodic'),  # frequency ?
)

RULE_CONDITIONS = {
    'text': {
        "__iexact":      "is exactly",
        "__icontains":   "contains",
        "__istartswith": "starts with",
        "__iendswith":   "ends with",
    },
    'numeric': {
        "__gt":  "greater than",
        "__gte": "greater than/equal to",
        "__lt":  "less than",
        "__lte": "less than/equal to",
    },
    'list': None,  # see values
}

RULE_SEVERITIES = (
    ('Low', 'Low'),
    ('Medium', 'Medium'),
    ('High', 'High'),
)

class Rule(models.Model):
    title            = models.CharField(max_length=256)
    comments         = models.CharField(max_length=256, default='n/a')
    scope            = models.CharField(choices=RULE_SCOPES, default='finding', max_length=10)
    scope_attr       = models.CharField(max_length=20, null=True, blank=True)
    condition        = JSONField(null=True, blank=True)
    target           = models.CharField(choices=RULE_TARGETS, default='event', max_length=10)
    severity         = models.CharField(choices=RULE_SEVERITIES, default='Low', max_length=10)
    trigger          = models.CharField(choices=RULE_TRIGGERS, default='auto', max_length=10)
    trigger_attr     = models.CharField(max_length=20, null=True, blank=True)
    summary          = JSONField(null=True, blank=True)
    periodic_task    = models.ForeignKey(PeriodicTask, null=True, blank=True, on_delete=models.CASCADE)
    enabled          = models.BooleanField(default=False)
    nb_matches       = models.IntegerField(default=0)
    owner            = models.ForeignKey(User, on_delete=models.DO_NOTHING)
    created_at       = models.DateTimeField(default=timezone.now)
    updated_at       = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'rules'

    def __str__(self):
        return "{}/{}".format(self.id, self.title)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            self.updated_at = timezone.now()
        return super(Rule, self).save(*args, **kwargs)

    def notify(self, message="", asset=None, description=""):
        if self.target == 'email':
            send_email_message(self, message, description)
        elif self.target == 'slack':
            send_slack_message(self, message)
        elif self.target == 'thehive':
            send_thehive_message(self, message, asset, description)
        elif self.target == 'event':
            Event.objects.create(message="[Alert][Rule={}]{}".format(self.title, message), type="ALERT", severity="INFO")

        self.nb_matches += 1
        print (self.nb_matches)
        self.save()

@receiver(post_save, sender=Rule)
def rule_create_update_log(sender, **kwargs):
    if kwargs['created']:
        Event.objects.create(message="[Rule] New rule created (id={}): {}".format(kwargs['instance'].id, kwargs['instance']),
                             type="CREATE", severity="DEBUG")
    else:
        Event.objects.create(message="[Rule] Rule '{}' modified (id={})".format(kwargs['instance'], kwargs['instance'].id),
                             type="UPDATE", severity="DEBUG")

@receiver(post_delete, sender=Rule)
def rule_delete_log(sender, **kwargs):
    Event.objects.create(message="[Rule] Rule '{}' deleted (id={})".format(kwargs['instance'], kwargs['instance'].id),
                 type="DELETE", severity="DEBUG")


def send_email_message(rule, message, description):
    from django.core.mail import send_mail
    contact_mail = Setting.objects.get(key="alerts.endpoint.email").value
    send_mail(
        '[Patrowl] New alert: '+message,
        'Here is the message.',
        'alerts@patrowl.io',
        [contact_mail],
        fail_silently=False,
    )


def send_slack_message(rule, message):
    slack_url = Setting.objects.get(key="alerts.endpoint.slack.webhook")
    alert_message = "[Alert][Rule={}]{}".format(rule.title, message)
    try:
        requests.post(
            slack_url.value,
            data=json.dumps({'text': alert_message}),
            headers={'content-type': 'application/json'})
    except Exception:
        Event.objects.create(message="[Rule] Send slack message failed (id={})".format(rule.id),
                     type="ERROR", severity="ERROR", description=alert_message)


def send_thehive_message(rule, message, asset, description):
    thehive_url = Setting.objects.get(key="alerts.endpoint.thehive.url")
    thehive_apikey = Setting.objects.get(key="alerts.endpoint.thehive.apikey")
    alert_message = "[Alert][Rule={}]{}".format(rule.title, message)

    api = TheHiveApi(thehive_url.value, thehive_apikey.value)
    sourceRef = str(uuid.uuid4())[0:6]
    rule_severity = 0
    if rule.severity == "Low":
        rule_severity = 1
    elif rule.severity == "Medium":
        rule_severity = 2
    elif rule.severity == "High":
        rule_severity = 3

    tlp = 0
    if asset.criticity == "low":
        tlp = 1
    elif asset.criticity == "medium":
        tlp = 2
    elif asset.criticity == "high":
        tlp = 3

    if asset:
        artifacts = [AlertArtifact(dataType=asset.type, data=asset.value)]
        try:
            alert = Alert(
                        title=alert_message,
                        tlp=tlp,
                        severity=rule_severity,
                        tags=['PatrOwl'],
                        description=description,
                        type='external',
                        source='patrowl',
                        sourceRef=sourceRef,
                        artifacts=artifacts)

            response = api.create_alert(alert)

            if response.status_code == 201:
                alert_id = response.json()['id']
                # todo: track theHive alerts
            else:
                Event.objects.create(
                    message="[Rule][send_thehive_message()] Unable to send "
                    "alert to TheHive with message ='{}'".format(message),
                    type="ERROR", severity="ERROR"
                )
        except Exception:
            Event.objects.create(
                message="[Rule][send_thehive_message()] Unable to send alert "
                "to TheHive with message ='{}'".format(message),
                type="ERROR", severity="ERROR")
