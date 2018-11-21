# -*- coding: utf-8 -*-

from django.conf.urls import url
from . import views


urlpatterns = [
    ## WEB Views
    # ex: /settings/
    url(r'^$', views.show_settings_menu, name='show_settings_menu'),

    ## API views
    # ex: /settings/api/v1/update
    url(r'^api/v1/update$', views.update_setting_api, name='update_setting_api'),
    # ex: /settings/api/v1/add
    url(r'^api/v1/add$', views.add_setting_api, name='add_setting_api'),
    # ex: /settings/api/v1/delete/3
    url(r'^api/v1/delete/(?P<setting_id>[0-9]+)$', views.delete_setting_api, name='delete_setting_api'),
    # ex: /settings/api/v1/export
    url(r'^api/v1/export$', views.export_settings_api, name='export_settings_api'),

]
