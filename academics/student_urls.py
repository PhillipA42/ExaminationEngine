from django.urls import path
from . import student_views

urlpatterns = [
    path('login/', student_views.student_login, name='student_login'),
    path('logout/', student_views.student_logout, name='student_logout'),
    path('timetable/', student_views.student_timetable, name='student_timetable'),
    path('examination-pass/', student_views.student_exam_pass, name='student_exam_pass'),
    path('results/', student_views.student_results, name='student_results'),
]
