"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Web Portals
    path('invigilator/', include('invigilators.urls')),
    path('student/', include('academics.student_urls')),
    path('', RedirectView.as_view(url='/student/timetable/', permanent=False)),

    # REST APIs
    path('api/auth/', include('authentication.urls')),
    path('api/academics/', include('academics.urls')),
    path('api/locations/', include('locations.urls')),
    path('api/scheduling/', include('scheduling.urls')),
    path('api/invigilators/', include('invigilators.api_urls')),
    path('api/malpractice/', include('malpractice.urls')),
    path('api-auth/', include('rest_framework.urls')),
]
