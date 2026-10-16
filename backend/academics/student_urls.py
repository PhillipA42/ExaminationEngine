from django.urls import path
from . import student_views

urlpatterns = [
    path('', student_views.student_dashboard, name='student_dashboard'),
    path('login/', student_views.student_login, name='student_login'),
    path('logout/', student_views.student_logout, name='student_logout'),
    path('profile/', student_views.student_profile, name='student_profile'),
    path('timetable/', student_views.student_timetable, name='student_timetable'),
    path('examination/<int:allocation_id>/', student_views.student_exam_detail, name='student_exam_detail'),
    path('examination/<int:allocation_id>/navigate/', student_views.student_venue_navigation, name='student_venue_navigation'),
    path('examination-pass/', student_views.student_exam_pass, name='student_exam_pass'),
    path('examination-pass/verify/', student_views.exam_pass_verification, name='exam_pass_verify'),
    path('results/', student_views.student_results, name='student_results'),
    path('notifications/', student_views.student_notifications, name='student_notifications'),
]
