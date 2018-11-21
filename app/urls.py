# -*- coding: utf-8 -*-

from django.conf import settings
from django.conf.urls import include, url
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.contrib.auth import views as auth_views
# from django.contrib.auth import logout as v_logout
from users import views as user_views
from reportings import views as rep_views

def i18n_javascript(request):
    return admin.site.i18n_javascript(request)

urlpatterns = [
        url(r'^', include([
            url(r'^$', rep_views.homepage_dashboard_view, name='homepage_dashboard_view'),
            url(r'^list$', user_views.list_users_view, name='list_users_view'),
            url(r'^home$', user_views.home, name='home'),
            url(r'^dashboard$', rep_views.homepage_dashboard_view, name='homepage_dashboard_view'),
            url(r'^details$', user_views.user_details_view, name='user_details_view'),
            url(r'^add$', user_views.add_user_view, name='add_user_view'),
            url(r'^admin/', admin.site.urls),
            url(r'^engines/', include('engines.urls')),
            url(r'^findings/', include('findings.urls')),
            url(r'^assets/', include('assets.urls')),
            url(r'^users/', include('users.urls')),
            url(r'^scans/', include('scans.urls')),
            url(r'^events/', include('events.urls')),
            url(r'^rules/', include('rules.urls')),
            url(r'^reportings/', include('reportings.urls')),
            url(r'^settings/', include('settings.urls')),
            url(r'^search', include('search.urls')),

            url(r'^login$', user_views.login, name='login'),
            url(r'^logout$', auth_views.logout, {'next_page': 'login'}, name='logout'),
            # url(r'^logout$', v_logout, {'next_page': '/login'}, name='logout'),
            url(r'^signup$', user_views.signup, name='signup'),

            url(r'^admin/jsi18n/', i18n_javascript),
        ]))

    #url(r'^api-auth/', include('rest_framework.urls')),
    # url(r'^api-auth/', include('rest_framework.urls')),
]

# debug toolbar & download file
if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        url(r'^__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns

urlpatterns += staticfiles_urlpatterns()
